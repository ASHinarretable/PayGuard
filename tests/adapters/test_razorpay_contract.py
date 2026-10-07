"""Contract tests: catch it when Razorpay's MCP server and the adapter drift apart."""

import json
from pathlib import Path

import pytest
import yaml

from payguard.adapters.razorpay import TOOL_KINDS, RazorpayAdapter
from payguard.core.action import ActionKind, ClassificationError, FailureBucket

FIXTURES = Path(__file__).parent / "fixtures" / "razorpay"
SERVER_TOOLS = {t["name"]: t for t in json.loads((FIXTURES / "tools.json").read_text(encoding="utf-8"))["tools"]}
CALLS = yaml.safe_load((FIXTURES / "calls.yaml").read_text(encoding="utf-8"))
adapter = RazorpayAdapter()


def test_every_server_tool_is_classified():
    missing = sorted(set(SERVER_TOOLS) - set(TOOL_KINDS))
    assert not missing, f"new Razorpay tools need a kind in TOOL_KINDS (they are denied until then): {missing}"


def test_no_stale_tools_in_table():
    stale = sorted(set(TOOL_KINDS) - set(SERVER_TOOLS))
    assert not stale, f"TOOL_KINDS lists tools the server no longer has: {stale}"


@pytest.mark.parametrize("name", sorted(SERVER_TOOLS))
def test_reads_agree_with_server_annotations(name):
    """Anything we call READ must be read-only on the server too. (The reverse needn't hold: the
    server marks fetch_tokens as a write, and we treat it as a credential.)"""
    if TOOL_KINDS[name] == ActionKind.READ:
        assert SERVER_TOOLS[name]["annotations"]["readOnlyHint"] is True


@pytest.mark.parametrize("call", CALLS, ids=lambda c: f"{c['tool']}:{json.dumps(c['args'])[:40]}")
def test_call_fixtures(call):
    if "error" in call:
        with pytest.raises(ClassificationError, match=call["error"]):
            adapter.classify(call["tool"], call["args"])
        return
    action = adapter.classify(call["tool"], call["args"])
    got = action.model_dump(mode="json")
    for key, want in call["expect"].items():
        assert got[key] == want, f"{key}: {got[key]!r} != {want!r}"
    assert action.raw_args == call["args"]


def test_state_from_payment_entity():
    s = adapter.state_from_payment({"id": "pay_1", "amount": 49900, "currency": "INR", "status": "captured",
                                    "amount_refunded": 10000, "email": "a@b.in", "contact": "+911"})
    assert s.status == "partially_refunded" and s.refundable == 39900 and s.customer.email == "a@b.in"
    failed = adapter.state_from_payment({"amount": 100, "currency": "INR", "status": "failed"})
    assert failed.status == "failed"


def test_live_mode_from_key_prefix():
    assert RazorpayAdapter(key_id="rzp_live_abc").is_live()
    assert not RazorpayAdapter(key_id="rzp_test_abc").is_live()
    assert not RazorpayAdapter().is_live()


def test_error_buckets():
    assert adapter.normalize_error({"error_reason": "upi_intent_not_enabled"}).bucket == FailureBucket.MERCHANT_CONFIG
    assert adapter.normalize_error({"error_reason": "bank_not_available"}).bucket == FailureBucket.OUTAGE
    assert adapter.normalize_error({"error_reason": "brand_new_reason"}).bucket == FailureBucket.OTHER


def test_untrusted_paths_only_for_reads():
    assert adapter.untrusted_paths("fetch_payment")
    assert adapter.untrusted_paths("create_refund") == []
