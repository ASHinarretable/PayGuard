"""Razorpay adapter: the only place that knows Razorpay's tool names, argument names and entities.

Every tool on razorpay-mcp-server must appear in TOOL_KINDS. A tool missing from the table is
classified UNKNOWN and denied, so a new provider tool stays blocked until it is reviewed here; the
contract tests fail when the server's tool list and this table drift apart.
"""

from __future__ import annotations

import json
import math
import re
from typing import Any

from payguard.core.action import (
    Action,
    ActionKind,
    ClassificationError,
    Contact,
    FailureBucket,
    FailureReason,
    Money,
    PaymentRecord,
    ResourceState,
    StateUnavailable,
)
from payguard.core.guard import Upstream

K = ActionKind
TOOL_KINDS: dict[str, ActionKind] = {
    # reads
    **{t: K.READ for t in (
        "fetch_payment", "fetch_payment_card_details", "fetch_all_payments",
        "fetch_order", "fetch_all_orders", "fetch_order_payments",
        "fetch_refund", "fetch_all_refunds", "fetch_multiple_refunds_for_payment",
        "fetch_specific_refund_for_payment",
        "fetch_payment_link", "fetch_all_payment_links",
        "fetch_qr_code", "fetch_all_qr_codes", "fetch_qr_codes_by_customer_id", "fetch_qr_codes_by_payment_id",
        "fetch_payments_for_qr_code",
        "fetch_settlement_with_id", "fetch_settlement_recon_details", "fetch_all_settlements",
        "fetch_all_instant_settlements", "fetch_instant_settlement_with_id",
        "fetch_payout_with_id", "fetch_all_payouts",
        "detect_stack", "integrate_razorpay_checkout",
    )},
    "create_refund": K.REFUND,
    "capture_payment": K.CAPTURE,
    # asking someone for money: the recipient and amount matter (R8, R9)
    "create_payment_link": K.PAYMENT_REQUEST,
    "payment_link_upi_create": K.PAYMENT_REQUEST,
    "create_qr_code": K.PAYMENT_REQUEST,
    "payment_link_notify": K.NOTIFY_CUSTOMER,
    # bookkeeping changes
    "update_payment": K.STATE_CHANGE,
    "update_refund": K.STATE_CHANGE,
    "create_order": K.STATE_CHANGE,
    "update_order": K.STATE_CHANGE,
    "update_payment_link": K.STATE_CHANGE,
    "close_qr_code": K.STATE_CHANGE,
    # moves the merchant's balance
    "create_instant_settlement": K.PAYOUT,
    # saved methods, mandates, OTPs and charging a saved token
    "fetch_tokens": K.CREDENTIAL,
    "revoke_token": K.CREDENTIAL,
    "create_registration_link": K.CREDENTIAL,
    "initiate_payment": K.CREDENTIAL,
    "submit_otp": K.CREDENTIAL,
    "resend_otp": K.CREDENTIAL,
}

RESOURCE_ARGS = ("payment_id", "order_id", "refund_id", "payment_link_id", "qr_code_id", "settlement_id",
                 "token_id")
AMOUNT_KINDS = frozenset({K.REFUND, K.CAPTURE, K.PAYMENT_REQUEST, K.PAYOUT})

# Fields customers write, on the entities that fetch_* tools return. Read by taint labelling.
UNTRUSTED_PATHS = ["$..notes.*", "$..description", "$..email", "$..contact", "$..customer.*"]

ERROR_BUCKETS: dict[str, FailureBucket] = {
    **dict.fromkeys(("payment_method_not_enabled", "upi_intent_not_enabled", "upi_collect_not_enabled",
                     "card_network_not_enabled", "bank_not_enabled"), FailureBucket.MERCHANT_CONFIG),
    **dict.fromkeys(("order_amount_mismatch", "invalid_order_id", "order_already_paid",
                     "input_validation_failed"), FailureBucket.INTEGRATION_BUG),
    **dict.fromkeys(("bank_not_available", "bank_technical_error", "psp_app_not_available",
                     "upi_app_technical_error", "gateway_technical_error", "issuer_technical_error"),
                    FailureBucket.OUTAGE),
    **dict.fromkeys(("payment_cancelled", "payment_timed_out", "incorrect_otp", "insufficient_funds",
                     "authentication_failed"), FailureBucket.CUSTOMER_DROP_OFF),
    **dict.fromkeys(("payment_risk_check_failed", "transaction_limit_exceeded",
                     "transaction_frequency_limit_exceeded"), FailureBucket.RISK_AND_LIMITS),
}


UNKNOWN_OUTCOME = re.compile(
    r"deadline exceeded|timed? ?out|timeout|connection reset|unexpected EOF|\b(502|503|504)\b", re.IGNORECASE)


def parse_minor(value: Any, field: str = "amount") -> int:
    """Razorpay amounts are whole paise. Accepts 49900, 49900.0 and "49900"; refuses anything else
    rather than guessing which unit the agent meant."""
    if isinstance(value, bool):
        raise ClassificationError(f"{field} must be a whole number of paise, got {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise ClassificationError(f"{field} must be a whole number of paise, got {value!r}")


class RazorpayAdapter:
    name = "razorpay"

    def __init__(self, upstream: Upstream | None = None, key_id: str | None = None) -> None:
        self.upstream = upstream
        self.key_id = key_id or ""

    def classify(self, tool: str, args: dict[str, Any]) -> Action:
        kind = TOOL_KINDS.get(tool, K.UNKNOWN)
        resource_id = next((str(args[a]) for a in RESOURCE_ARGS if args.get(a)), None)
        amount = None
        if kind in AMOUNT_KINDS and args.get("amount") is not None:
            amount = Money(minor=parse_minor(args["amount"]), currency=str(args.get("currency") or "INR").upper())
        elif kind == K.REFUND:
            raise ClassificationError("create_refund needs an amount in paise")
        recipient = None
        if kind == K.PAYMENT_REQUEST and (args.get("customer_email") or args.get("customer_contact")):
            recipient = Contact(email=args.get("customer_email"), phone=args.get("customer_contact"))
        return Action(provider=self.name, tool=tool, kind=kind, resource_id=resource_id, amount=amount,
                      recipient=recipient, raw_args=dict(args))

    async def fetch_state(self, action: Action) -> ResourceState | None:
        if not (action.resource_id or "").startswith("pay_"):
            return None
        if self.upstream is None:
            raise StateUnavailable("no upstream connection to fetch state")
        outcome = await self.upstream.call("fetch_payment", {"payment_id": action.resource_id})
        if outcome.is_error:
            raise StateUnavailable(outcome.text)
        try:
            return self.state_from_payment(json.loads(outcome.text))
        except (ValueError, KeyError, TypeError) as exc:
            raise StateUnavailable(f"unreadable payment entity: {exc}") from exc

    @staticmethod
    def state_from_payment(p: dict[str, Any]) -> ResourceState:
        currency = p["currency"]
        amount, refunded = int(p["amount"]), int(p.get("amount_refunded") or 0)
        status = {"captured": "succeeded", "refunded": "refunded", "authorized": "authorized"}.get(
            p["status"], "failed")
        if status == "succeeded" and refunded > 0:
            status = "partially_refunded"
        customer = Contact(email=p.get("email") or None, phone=p.get("contact") or None)
        return ResourceState(status=status, amount=Money(minor=amount, currency=currency),
                             amount_refunded=Money(minor=refunded, currency=currency), customer=customer)

    def untrusted_paths(self, tool: str) -> list[str]:
        return UNTRUSTED_PATHS if TOOL_KINDS.get(tool) == K.READ else []

    def customer_contacts(self, tool: str, data: Any) -> list[Contact]:
        """Customers on record in a read result: the payer of each payment and the customer on each
        payment link, as Razorpay returned them."""
        found: list[Contact] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if node.get("entity") == "payment" and (node.get("email") or node.get("contact")):
                    found.append(Contact(email=node.get("email") or None, phone=node.get("contact") or None))
                if str(node.get("id", "")).startswith("plink_") and isinstance(node.get("customer"), dict):
                    c = node["customer"]
                    if c.get("email") or c.get("contact"):
                        found.append(Contact(email=c.get("email") or None, phone=c.get("contact") or None))
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for v in node:
                    walk(v)

        if TOOL_KINDS.get(tool) == K.READ:
            walk(data)
        return found

    def outcome_unknown(self, error_text: str) -> bool:
        """Errors after which Razorpay may still have acted: the request left, the answer didn't come back."""
        return bool(UNKNOWN_OUTCOME.search(error_text))

    def is_live(self) -> bool:
        return self.key_id.startswith("rzp_live_")

    def payment_record(self, p: dict[str, Any]) -> PaymentRecord:
        state = self.state_from_payment(p)
        if p.get("bank"):
            channel = f"bank:{p['bank']}"
        elif p.get("vpa") and "@" in str(p["vpa"]):
            channel = f"upi:{str(p['vpa']).rsplit('@', 1)[1]}"
        elif p.get("wallet"):
            channel = f"wallet:{p['wallet']}"
        else:
            channel = p.get("method")
        notes = p.get("notes")
        return PaymentRecord(
            id=p["id"], order_id=p.get("order_id"), amount=int(p["amount"]), currency=p["currency"],
            status=state.status, method=p.get("method") or "unknown", created_at=int(p["created_at"]),
            email=p.get("email") or None, phone=p.get("contact") or None, channel=channel,
            failure=self.normalize_error(p) if p.get("status") == "failed" else None,
            note_keys=sorted(notes) if isinstance(notes, dict) else [],
        )

    def normalize_error(self, payment: dict[str, Any]) -> FailureReason:
        reason = payment.get("error_reason") or "unknown"
        return FailureReason(bucket=ERROR_BUCKETS.get(reason, FailureBucket.OTHER), code=reason,
                             source=payment.get("error_source"))
