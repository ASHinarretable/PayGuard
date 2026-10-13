"""R11: refunds on a payment the human's task never named go to a human."""

from datetime import UTC, datetime

from payguard.core.action import Action, ActionKind, Money, ResourceState
from payguard.core.decide import decide
from payguard.core.policy import Policy
from payguard.core.rules import Context, Verdict
from payguard.core.taint import SessionTaint

POLICY = Policy.load("policies/default.yaml")
PAY = "pay_N8xAbCdEfGhIjK"


class NoHistory:
    def actions_since(self, since):
        return []


def run(mandate, policy=POLICY):
    action = Action(provider="t", tool="refund", kind=ActionKind.REFUND, resource_id=PAY,
                    amount=Money(minor=10_000, currency="INR"))
    state = ResourceState(status="succeeded", amount=Money(minor=49_900, currency="INR"),
                          amount_refunded=Money(minor=0, currency="INR"))
    taint = SessionTaint(mandate=mandate) if mandate is not None else None
    ctx = Context(action=action, policy=policy, history=NoHistory(), now=datetime(2026, 10, 5, tzinfo=UTC),
                  session_id="s", state=state, taint=taint)
    return decide(ctx)


def test_named_payment_is_allowed():
    assert run(f"Refund ₹100 on {PAY}, damaged item").verdict == Verdict.ALLOW


def test_unnamed_payment_escalates():
    d = run("Refund Aryan's order")
    assert d.verdict == Verdict.ESCALATE and d.rules == ["R11"]


def test_no_recorded_task_means_no_r11():
    assert "R11" not in run(None).rules


def test_policy_can_turn_it_off():
    assert "R11" not in run("Refund Aryan's order", POLICY.model_copy(update={"require_named_refund_target": False})).rules
