"""One block per rule: the call that should pass, and the calls that should fire it."""

from datetime import UTC, datetime, timedelta

import pytest

from payguard.core.action import Action, ActionKind, Money, ResourceState
from payguard.core.decide import decide
from payguard.core.policy import Policy
from payguard.core.rules import Context, PastAction, Verdict

NOW = datetime(2026, 10, 5, 14, 0, tzinfo=UTC)
POLICY = Policy.load("policies/default.yaml")
PAY = "pay_N8xAbCdEfGhIjK"


def inr(minor: int) -> Money:
    return Money(minor=minor, currency="INR")


class FakeHistory:
    def __init__(self, actions: list[PastAction] | None = None) -> None:
        self.actions = actions or []

    def actions_since(self, since: datetime) -> list[PastAction]:
        return [a for a in self.actions if a.ts >= since]


def past(kind=ActionKind.REFUND, minor=10_000, resource=PAY, ago=timedelta(hours=1), session="ses_1",
         executed=True, verdict=Verdict.ALLOW) -> PastAction:
    return PastAction(ts=NOW - ago, session_id=session, kind=kind, resource_id=resource,
                      amount=inr(minor) if minor is not None else None, verdict=verdict, executed=executed)


def state(amount=49_900, refunded=0, status="succeeded") -> ResourceState:
    return ResourceState(status=status, amount=inr(amount), amount_refunded=inr(refunded))


def refund(minor=49_900, resource=PAY, currency="INR") -> Action:
    return Action(provider="test", tool="refund_tool", kind=ActionKind.REFUND, resource_id=resource,
                  amount=Money(minor=minor, currency=currency))


def run(action, st=None, history=None, policy=POLICY, is_live=False, session="ses_1"):
    ctx = Context(action=action, policy=policy, history=history or FakeHistory(), now=NOW, session_id=session,
                  state=st, is_live=is_live)
    return decide(ctx)


def test_clean_refund_is_allowed():
    d = run(refund(), state())
    assert d.verdict == Verdict.ALLOW and d.fired == []


def test_reads_never_hit_rules():
    read = Action(provider="test", tool="fetch", kind=ActionKind.READ)
    assert run(read, is_live=True).verdict == Verdict.ALLOW


# ---- KIND / STATE ----------------------------------------------------------------------------

@pytest.mark.parametrize("kind", [ActionKind.PAYOUT, ActionKind.CREDENTIAL, ActionKind.UNKNOWN])
def test_denied_kinds(kind):
    d = run(Action(provider="test", tool="t", kind=kind))
    assert d.verdict == Verdict.DENY and d.rules == ["KIND"]


def test_state_change_is_allowed():
    assert run(Action(provider="test", tool="t", kind=ActionKind.STATE_CHANGE)).verdict == Verdict.ALLOW


def test_policy_can_deny_more_kinds():
    strict = POLICY.model_copy(update={"deny_kinds": [*POLICY.deny_kinds, ActionKind.STATE_CHANGE]})
    assert run(Action(provider="test", tool="t", kind=ActionKind.STATE_CHANGE), policy=strict).verdict == Verdict.DENY


def test_refund_without_state_fails_closed():
    d = run(refund(), st=None)
    assert d.verdict == Verdict.DENY and "STATE" in d.rules


# ---- R1 refundable amount --------------------------------------------------------------------

def test_r1_full_refund_ok():
    assert "R1" not in run(refund(49_900), state(49_900)).rules


def test_r1_over_refund_denied():
    d = run(refund(50_000), state(49_900))
    assert d.verdict == Verdict.DENY and "R1" in d.rules


def test_r1_counts_previous_refunds():
    d = run(refund(30_000), state(49_900, refunded=20_000, status="partially_refunded"))
    assert d.verdict == Verdict.DENY and "R1" in d.rules
    assert "R1" not in run(refund(29_900), state(49_900, refunded=20_000, status="partially_refunded")).rules


def test_r1_zero_amount_denied():
    assert "R1" in run(refund(0), state()).rules


def test_r1_currency_mismatch_denied():
    assert "R1" in run(refund(49_900, currency="USD"), state()).rules


# ---- R2 status -------------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["failed", "authorized", "refunded"])
def test_r2_non_refundable_status(status):
    d = run(refund(100), state(status=status))
    assert d.verdict == Verdict.DENY and "R2" in d.rules


def test_r2_partially_refunded_is_refundable():
    assert "R2" not in run(refund(100), state(refunded=100, status="partially_refunded")).rules


# ---- R3 single threshold ---------------------------------------------------------------------

def test_r3_at_threshold_is_allowed():
    assert run(refund(500_000), state(500_000)).verdict == Verdict.ALLOW


def test_r3_above_threshold_escalates():
    d = run(refund(500_100), state(600_000))
    assert d.verdict == Verdict.ESCALATE and d.rules == ["R3"]


def test_r3_uses_currency_limits():
    usd = ResourceState(status="succeeded", amount=Money(minor=20_000, currency="USD"),
                        amount_refunded=Money(minor=0, currency="USD"))
    assert run(refund(15_000, currency="USD"), usd).verdict == Verdict.ESCALATE


# ---- R4 daily cap ----------------------------------------------------------------------------

def test_r4_under_cap_ok():
    h = FakeHistory([past(minor=2_000_000)])
    assert "R4" not in run(refund(49_900), state(), h).rules


def test_r4_over_cap_denied():
    h = FakeHistory([past(minor=2_000_000, resource="pay_other"), past(minor=490_000, resource="pay_other2")])
    d = run(refund(49_900), state(), h)
    assert d.verdict == Verdict.DENY and "R4" in d.rules


def test_r4_ignores_old_and_unexecuted_refunds():
    h = FakeHistory([past(minor=2_400_000, ago=timedelta(hours=25)),
                     past(minor=2_400_000, executed=False, verdict=Verdict.DENY)])
    assert "R4" not in run(refund(49_900), state(), h).rules


def test_r4_counts_other_sessions():
    h = FakeHistory([past(minor=2_480_000, session="ses_other", resource="pay_x")])
    assert "R4" in run(refund(49_900), state(), h).rules


def test_r4_currency_without_limits_denied():
    eur = ResourceState(status="succeeded", amount=Money(minor=100, currency="EUR"),
                        amount_refunded=Money(minor=0, currency="EUR"))
    d = run(refund(100, currency="EUR"), eur)
    assert d.verdict == Verdict.DENY and "R4" in d.rules


# ---- R5 circuit breaker ----------------------------------------------------------------------

def _burst(n, session="ses_1", ago=timedelta(minutes=2)):
    return FakeHistory([past(minor=100, resource=f"pay_{i}", ago=ago, session=session,
                             executed=False, verdict=Verdict.DENY) for i in range(n)])


def test_r5_fifth_call_ok_sixth_escalates():
    assert "R5" not in run(refund(100), state(), _burst(4)).rules
    d = run(refund(100), state(), _burst(5))
    assert d.verdict == Verdict.ESCALATE and "R5" in d.rules


def test_r5_only_counts_this_session_and_window():
    assert "R5" not in run(refund(100), state(), _burst(5, session="ses_other")).rules
    assert "R5" not in run(refund(100), state(), _burst(5, ago=timedelta(minutes=11))).rules


# ---- R6 duplicates ---------------------------------------------------------------------------

def test_r6_same_refund_again_denied():
    d = run(refund(10_000), state(49_900, refunded=10_000, status="partially_refunded"),
            FakeHistory([past(minor=10_000)]))
    assert d.verdict == Verdict.DENY and "R6" in d.rules
    assert d.hints  # tells the agent to check before retrying


def test_r6_different_amount_or_resource_ok():
    h = FakeHistory([past(minor=10_000)])
    assert "R6" not in run(refund(20_000), state(), h).rules
    assert "R6" not in run(refund(10_000, resource="pay_otherAbCdEfGh"), state(), h).rules


def test_r6_failed_attempt_is_not_a_duplicate():
    h = FakeHistory([past(minor=10_000, executed=False, verdict=Verdict.DENY)])
    assert "R6" not in run(refund(10_000), state(), h).rules


# ---- R7 unit mix-ups -------------------------------------------------------------------------

def test_r7_rupees_sent_as_paise():
    d = run(refund(499), state(49_900))
    assert d.verdict == Verdict.DENY and "R7" in d.rules
    assert "49900" in d.hints[0]


def test_r7_paise_times_hundred():
    d = run(refund(4_990_000), state(49_900))
    assert "R7" in d.rules and "R1" in d.rules


def test_r7_normal_partial_refund_ok():
    assert "R7" not in run(refund(10_000), state(49_900)).rules


# ---- R10 live mode ---------------------------------------------------------------------------

def test_r10_live_denies_writes():
    d = run(refund(), state(), is_live=True)
    assert d.verdict == Verdict.DENY and "R10" in d.rules


def test_r10_allow_live_opt_in():
    live_ok = POLICY.model_copy(update={"allow_live": True})
    assert "R10" not in run(refund(), state(), policy=live_ok, is_live=True).rules


# ---- combining -------------------------------------------------------------------------------

def test_deny_beats_escalate():
    d = run(refund(900_000), state(600_000))  # R3 escalates, R1 denies
    assert d.verdict == Verdict.DENY and {"R1", "R3"} <= set(d.rules)


def test_policy_rejects_unknown_fields(tmp_path):
    bad = tmp_path / "p.yaml"
    bad.write_text("adapter: x\nmerchant: y\nallow_lve: true\n")
    with pytest.raises(ValueError):
        Policy.load(bad)
