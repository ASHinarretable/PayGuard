"""The guard end to end against the mock: what it decides, and what that does to money (the ledger)."""

import json
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from datagen.models import Status
from mock.server import call, load_tools
from mock.store import Store
from payguard.adapters.razorpay import RazorpayAdapter
from payguard.core.audit import AuditLog
from payguard.core.guard import Guard, ToolOutcome
from payguard.core.policy import Policy
from tests.conftest import MERCHANT

SESSION = "ses_test"


class InProcessUpstream:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.tools = {t.name: t for t in load_tools()[1]}

    async def call(self, tool, args):
        result = call(self.store, self.tools, tool, args)
        return ToolOutcome(is_error=bool(result.is_error), text=result.content[0].text)


@pytest.fixture
def env(data_dir):
    store = Store(data_dir, MERCHANT)
    upstream = InProcessUpstream(store)
    audit = AuditLog()
    guard = Guard(RazorpayAdapter(upstream=upstream), upstream, Policy.load("policies/default.yaml"), audit)
    return guard, store, audit


def payment(store, *, status=Status.CAPTURED, min_amount=0, max_amount=10**9):
    return next(p for p in store.payments.values()
                if p.status == status and p.amount_refunded == 0 and min_amount <= p.amount <= max_amount)


async def test_reads_pass_through_and_are_audited(env):
    guard, store, audit = env
    out = await guard.handle(SESSION, "fetch_all_payments", {"count": 2})
    assert not out.is_error and json.loads(out.text)["count"] == 2
    [event] = audit.events()
    assert event.verdict == "ALLOW" and event.kind == "read" and event.executed
    assert store.ledger_rows() == []


async def test_credential_tool_is_denied_and_never_reaches_provider(env):
    guard, store, audit = env
    out = await guard.handle(SESSION, "revoke_token", {"customer_id": "cust_1", "token_id": "token_1"})
    assert out.is_error and out.text.startswith("DENIED by PayGuard [KIND]")
    assert store.ledger_rows() == []
    assert audit.events()[0].executed is False


async def test_oversized_refund_is_denied(env):
    guard, store, _ = env
    p = payment(store, max_amount=400_000)
    out = await guard.handle(SESSION, "create_refund", {"payment_id": p.id, "amount": p.amount + 100})
    assert out.is_error and "[R1]" in out.text
    assert store.ledger_rows() == []


async def test_legitimate_refund_goes_through_once(env):
    guard, store, audit = env
    p = payment(store, max_amount=400_000)
    args = {"payment_id": p.id, "amount": 10_000}
    first = await guard.handle(SESSION, "create_refund", args)
    assert not first.is_error and json.loads(first.text)["entity"] == "refund"
    again = await guard.handle(SESSION, "create_refund", args)
    assert again.is_error and "R6" in again.text
    rows = store.ledger_rows()
    assert len(rows) == 1 and rows[0]["paise_moved"] == 10_000
    assert [e.executed for e in audit.events()] == [True, False]


async def test_large_refund_escalates(env):
    guard, store, _ = env
    p = payment(store, min_amount=500_100)
    out = await guard.handle(SESSION, "create_refund", {"payment_id": p.id, "amount": p.amount})
    assert out.is_error and out.text.startswith("PENDING_APPROVAL [R3]")
    assert store.ledger_rows() == []


async def test_rupees_instead_of_paise_is_caught(env):
    guard, store, _ = env
    p = payment(store, max_amount=400_000)
    out = await guard.handle(SESSION, "create_refund", {"payment_id": p.id, "amount": p.amount // 100})
    assert out.is_error and "R7" in out.text and f"send {p.amount}" in out.text


async def test_refund_on_failed_payment_denied(env):
    guard, store, _ = env
    p = payment(store, status=Status.FAILED)
    out = await guard.handle(SESSION, "create_refund", {"payment_id": p.id, "amount": 100})
    assert out.is_error and "R2" in out.text


async def test_unknown_payment_fails_closed(env):
    guard, store, _ = env
    out = await guard.handle(SESSION, "create_refund", {"payment_id": "pay_doesnotexist00", "amount": 100})
    assert out.is_error and "[STATE]" in out.text
    assert store.ledger_rows() == []


async def test_unreadable_amount_denied(env):
    guard, store, _ = env
    p = payment(store)
    out = await guard.handle(SESSION, "create_refund", {"payment_id": p.id, "amount": "₹499"})
    assert out.is_error and "[ARGS]" in out.text
    assert store.ledger_rows() == []


async def test_circuit_breaker_pauses_session(env):
    guard, store, audit = env
    small = [p for p in store.payments.values() if p.status == Status.CAPTURED and p.amount_refunded == 0][:7]
    verdicts = []
    for p in small:
        out = await guard.handle(SESSION, "create_refund", {"payment_id": p.id, "amount": 100})
        verdicts.append(out.text.split(" ")[0] if out.is_error else "OK")
    assert verdicts[:5] == ["OK"] * 5
    assert verdicts[5] == "PENDING_APPROVAL"
    assert verdicts[6] == "DENIED"  # paused: no more writes until a human resumes the session
    assert audit.paused(SESSION) is not None
    assert len(store.ledger_rows()) == 5


async def test_live_keys_block_all_writes(env, data_dir):
    _, store, audit = env
    upstream = InProcessUpstream(store)
    live = Guard(RazorpayAdapter(upstream=upstream, key_id="rzp_live_123"), upstream,
                 Policy.load("policies/default.yaml"), audit)
    p = payment(store, max_amount=400_000)
    out = await live.handle(SESSION, "create_refund", {"payment_id": p.id, "amount": 100})
    assert out.is_error and "R10" in out.text
    assert not (await live.handle(SESSION, "fetch_payment", {"payment_id": p.id})).is_error


async def test_stdio_proxy_end_to_end(data_dir, tmp_path):
    """Day 2 'done when', scripted: an MCP client pointed at PayGuard can read payments but is
    refused revoke_token and an oversized refund."""
    db, ledger = tmp_path / "payguard.db", tmp_path / "ledger.db"
    params = StdioServerParameters(command=sys.executable, args=[
        "-m", "payguard.cli", "run", "--upstream", "mock", "--merchant", MERCHANT, "--data", str(data_dir),
        "--ledger", str(ledger), "--db", str(db), "--session", "ses_e2e"])
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        assert init.server_info.name == "payguard"
        assert len((await session.list_tools()).tools) == 45
        listing = await session.call_tool("fetch_all_payments", {"count": 10})
        items = json.loads(listing.content[0].text)["items"]
        captured = next(p for p in items if p["status"] == "captured")
        revoke = await session.call_tool("revoke_token", {"customer_id": "cust_1", "token_id": "token_1"})
        too_big = await session.call_tool("create_refund", {"payment_id": captured["id"],
                                                            "amount": captured["amount"] + 100})
    assert revoke.is_error and "KIND" in revoke.content[0].text
    assert too_big.is_error and "R1" in too_big.content[0].text
    verdicts = [e.verdict for e in AuditLog(f"sqlite:///{db}").events("ses_e2e")]
    assert verdicts == ["ALLOW", "DENY", "DENY"]
