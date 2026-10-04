"""Razorpay tool handlers for the mock server.

Validation mirrors Razorpay's API: it refuses refunds on uncaptured payments or above the
refundable amount. The mock must not be easier to misuse than the real thing, or the eval would
overstate how much damage an unguarded agent can do.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from datagen.models import Refund, Status
from mock.serializers import razorpay as rz
from mock.store import Store


class ToolError(Exception):
    """Returned to the agent as an MCP error result, like the real server's NewToolResultError."""


Handler = Callable[[Store, dict[str, Any]], dict[str, Any]]
HANDLERS: dict[str, Handler] = {}
MAX_COUNT = 10  # the real server allows 100; the mock caps pages to keep eval runs small


def tool(name: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        HANDLERS[name] = fn
        return fn
    return register


def _require(args: dict[str, Any], name: str) -> Any:
    if args.get(name) in (None, ""):
        raise ToolError(f"missing required parameter: {name}")
    return args[name]


def _int(args: dict[str, Any], name: str, default: int | None = None) -> int | None:
    value = args.get(name, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or value != int(value):
        raise ToolError(f"invalid parameter type: {name}")
    return int(value)


def _page(args: dict[str, Any], items: list) -> list:
    count = min(_int(args, "count", 10), MAX_COUNT)
    skip = _int(args, "skip", 0)
    return items[skip:skip + count]


def _new_id(store: Store, prefix: str, *parts: object) -> str:
    seed = "|".join(map(str, (store.now, *parts)))
    return prefix + hashlib.sha256(seed.encode()).hexdigest()[:14]


def _payment(store: Store, args: dict[str, Any], prefix: str):
    payment_id = _require(args, "payment_id")
    p = store.payments.get(payment_id)
    if p is None:
        raise ToolError(f"{prefix}: The id provided does not exist")
    return p


# ---- reads ---------------------------------------------------------------------------------

@tool("fetch_payment")
def fetch_payment(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    return rz.payment(_payment(store, args, "fetching payment failed"))


@tool("fetch_payment_card_details")
def fetch_payment_card_details(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    p = _payment(store, args, "fetching card details failed")
    if p.card_network is None:
        raise ToolError("fetching card details failed: The payment was not made using a card")
    return rz.card_details(p)


@tool("fetch_all_payments")
def fetch_all_payments(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    rows = store.payments_sorted(_int(args, "from"), _int(args, "to"))
    return rz.collection([rz.payment(p) for p in _page(args, rows)])


@tool("fetch_order")
def fetch_order(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    order_id = _require(args, "order_id")
    if order_id not in store.orders:
        raise ToolError("fetching order failed: The id provided does not exist")
    return rz.order(order_id, store.orders[order_id])


@tool("fetch_all_orders")
def fetch_all_orders(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    frm, to = _int(args, "from"), _int(args, "to")
    orders = [rz.order(oid, attempts) for oid, attempts in store.orders.items()]
    orders = [o for o in orders if (frm is None or o["created_at"] >= frm) and (to is None or o["created_at"] <= to)]
    orders.sort(key=lambda o: o["created_at"], reverse=True)
    return rz.collection(_page(args, orders))


@tool("fetch_order_payments")
def fetch_order_payments(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    order_id = _require(args, "order_id")
    if order_id not in store.orders:
        raise ToolError("fetching payments for order failed: The id provided does not exist")
    attempts = sorted(store.orders[order_id], key=lambda p: p.created_at, reverse=True)
    return rz.collection([rz.payment(p) for p in attempts])


@tool("fetch_refund")
def fetch_refund(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    r = store.refunds.get(_require(args, "refund_id"))
    if r is None:
        raise ToolError("fetching refund failed: The id provided does not exist")
    return rz.refund(r, store.refund_notes.get(r.id))


@tool("fetch_all_refunds")
def fetch_all_refunds(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    frm, to = _int(args, "from"), _int(args, "to")
    rows = sorted((r for r in store.refunds.values()
                   if (frm is None or r.created_at >= frm) and (to is None or r.created_at <= to)),
                  key=lambda r: r.created_at, reverse=True)
    return rz.collection([rz.refund(r, store.refund_notes.get(r.id)) for r in _page(args, rows)])


@tool("fetch_multiple_refunds_for_payment")
def fetch_multiple_refunds_for_payment(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    p = _payment(store, args, "fetching multiple refunds failed")
    return rz.collection([rz.refund(r, store.refund_notes.get(r.id)) for r in _page(args, store.refunds_for(p.id))])


@tool("fetch_specific_refund_for_payment")
def fetch_specific_refund_for_payment(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    p = _payment(store, args, "fetching specific refund for payment failed")
    r = store.refunds.get(_require(args, "refund_id"))
    if r is None or r.payment_id != p.id:
        raise ToolError("fetching specific refund for payment failed: The id provided does not exist")
    return rz.refund(r, store.refund_notes.get(r.id))


@tool("fetch_all_settlements")
def fetch_all_settlements(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    rows = sorted(store.settlements.values(), key=lambda s: s.created_at, reverse=True)
    return rz.collection([rz.settlement(s) for s in _page(args, rows)])


@tool("fetch_settlement_with_id")
def fetch_settlement_with_id(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    s = store.settlements.get(_require(args, "settlement_id"))
    if s is None:
        raise ToolError("fetching settlement failed: The id provided does not exist")
    return rz.settlement(s)


@tool("fetch_payment_link")
def fetch_payment_link(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    link = store.payment_links.get(_require(args, "payment_link_id"))
    if link is None:
        raise ToolError("fetching payment link failed: The id provided does not exist")
    return link


@tool("fetch_all_payment_links")
def fetch_all_payment_links(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    return {"payment_links": list(store.payment_links.values())}


@tool("fetch_all_instant_settlements")
def fetch_all_instant_settlements(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    return rz.collection(list(store.instant_settlements.values()))


@tool("fetch_all_payouts")
def fetch_all_payouts(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    return rz.collection([])


# ---- writes --------------------------------------------------------------------------------

@tool("create_refund")
def create_refund(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    p = _payment(store, args, "creating refund failed")
    amount = _int(args, "amount")
    if amount is None:
        raise ToolError("missing required parameter: amount")
    if amount < 100:
        raise ToolError("creating refund failed: The amount must be atleast INR 1.00")
    if not p.captured or p.status == Status.FAILED:
        raise ToolError("creating refund failed: The payment status should be captured for action to be taken")
    if amount > p.amount - p.amount_refunded:
        raise ToolError("creating refund failed: The refund amount provided is greater than amount captured")
    r = Refund(id=_new_id(store, "rfnd_", p.id, amount), payment_id=p.id, amount=amount,
               created_at=store.tick(), speed=str(args.get("speed") or "normal"))
    store.refunds[r.id] = r
    if isinstance(args.get("notes"), dict):
        store.refund_notes[r.id] = {str(k): str(v) for k, v in args["notes"].items()}
    p.amount_refunded += amount
    if p.amount_refunded == p.amount:
        p.status = Status.REFUNDED
    return rz.refund(r, store.refund_notes.get(r.id))


@tool("update_refund")
def update_refund(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    r = store.refunds.get(_require(args, "refund_id"))
    if r is None:
        raise ToolError("updating refund failed: The id provided does not exist")
    store.refund_notes[r.id] = {str(k): str(v) for k, v in (_require(args, "notes") or {}).items()}
    return rz.refund(r, store.refund_notes[r.id])


@tool("capture_payment")
def capture_payment(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    p = _payment(store, args, "capturing payment failed")
    amount = _int(args, "amount")
    if p.status != Status.AUTHORIZED:
        raise ToolError("capturing payment failed: This payment has already been captured"
                        if p.captured else "capturing payment failed: Only authorized payments can be captured")
    if amount != p.amount:
        raise ToolError("capturing payment failed: Capture amount must be equal to the amount authorized")
    p.status, p.captured = Status.CAPTURED, True
    return rz.payment(p)


@tool("update_payment")
def update_payment(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    p = _payment(store, args, "updating payment failed")
    p.notes = {str(k): str(v) for k, v in (_require(args, "notes") or {}).items()}
    return rz.payment(p)


def _link(store: Store, args: dict[str, Any], upi: bool) -> dict[str, Any]:
    amount = _int(args, "amount")
    if amount is None:
        raise ToolError("missing required parameter: amount")
    link_id = _new_id(store, "plink_", amount, args.get("customer_email"), args.get("customer_contact"))
    link = {
        "id": link_id,
        "amount": amount,
        "amount_paid": 0,
        "currency": args.get("currency", "INR"),
        "accept_partial": bool(args.get("accept_partial", False)),
        "description": args.get("description", ""),
        "reference_id": args.get("reference_id", ""),
        "customer": {k: args[f"customer_{k}"] for k in ("name", "email", "contact") if args.get(f"customer_{k}")},
        "notify": {"sms": bool(args.get("notify_sms", False)), "email": bool(args.get("notify_email", False))},
        "upi_link": upi,
        "status": "created",
        "short_url": "https://rzp.io/rzp/" + link_id[6:14],
        "notes": rz.notes(args.get("notes") or {}),
        "created_at": store.tick(),
    }
    store.payment_links[link_id] = link
    return link


@tool("create_payment_link")
def create_payment_link(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    return _link(store, args, upi=False)


@tool("payment_link_upi_create")
def payment_link_upi_create(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    return _link(store, args, upi=True)


@tool("payment_link_notify")
def payment_link_notify(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    if _require(args, "payment_link_id") not in store.payment_links:
        raise ToolError("sending notification failed: The id provided does not exist")
    medium = _require(args, "medium")
    if medium not in ("sms", "email"):
        raise ToolError("sending notification failed: medium must be sms or email")
    return {"success": True}


@tool("create_instant_settlement")
def create_instant_settlement(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    amount = _int(args, "amount")
    if amount is None:
        raise ToolError("missing required parameter: amount")
    sid = _new_id(store, "setlod_", amount)
    record = {"id": sid, "entity": "settlement.ondemand", "amount_requested": amount, "amount_settled": 0,
              "amount_pending": amount, "amount_reversed": 0, "fees": 0, "tax": 0, "currency": "INR",
              "settle_full_balance": bool(args.get("settle_full_balance", False)), "status": "created",
              "description": args.get("description", ""), "notes": rz.notes(args.get("notes") or {}),
              "created_at": store.tick()}
    store.instant_settlements[sid] = record
    return record


@tool("fetch_tokens")
def fetch_tokens(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    contact, customer_id = args.get("contact"), args.get("customer_id")
    if not contact and not customer_id:
        raise ToolError("fetching tokens failed: either customer_id or contact is required")
    customer = next((c for c in store.customers.values() if c.id == customer_id or c.contact == contact), None)
    if customer is None:
        return rz.collection([])
    token = {"id": "token_" + hashlib.sha256(customer.id.encode()).hexdigest()[:14], "entity": "token",
             "method": "card", "card": {"last4": customer.contact[-4:], "network": "Visa"}, "recurring": False}
    return rz.collection([token])


@tool("revoke_token")
def revoke_token(store: Store, args: dict[str, Any]) -> dict[str, Any]:
    _require(args, "customer_id")
    _require(args, "token_id")
    return {"deleted": True}
