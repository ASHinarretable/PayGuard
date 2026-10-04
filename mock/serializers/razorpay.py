"""Neutral records -> Razorpay API entities, matching what the real MCP server returns.

Fields the generator doesn't model (acquirer data, card ids) are derived from a hash of the
record id, so they are stable across runs.
"""

from __future__ import annotations

import hashlib
from typing import Any

from datagen.models import Method, Payment, Refund, Settlement, Status


def _digits(seed: str, n: int) -> str:
    return str(int(hashlib.sha256(seed.encode()).hexdigest(), 16))[:n]


def _token(seed: str, n: int = 14) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    value = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    out = []
    for _ in range(n):
        value, i = divmod(value, len(alphabet))
        out.append(alphabet[i])
    return "".join(out)


def notes(value: dict[str, str]) -> dict[str, str] | list:
    # Razorpay's API returns an empty JSON array, not an object, when there are no notes.
    return dict(value) if value else []


def collection(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {"entity": "collection", "count": len(items), "items": items}


def _acquirer_data(p: Payment) -> dict[str, Any]:
    if p.failure is not None:
        return {}
    if p.method == Method.UPI:
        return {"rrn": _digits(p.id + "rrn", 12), "upi_transaction_id": _token(p.id + "upi", 24).upper()}
    if p.method == Method.NETBANKING:
        return {"bank_transaction_id": _digits(p.id + "bank", 10)}
    if p.method == Method.CARD:
        return {"auth_code": _digits(p.id + "auth", 6)}
    return {"transaction_id": None}


def payment(p: Payment) -> dict[str, Any]:
    fee = round(p.amount * 0.02) if p.captured else None
    tax = round(fee * 0.18) if fee is not None else None
    if p.amount_refunded == 0:
        refund_status = None
    else:
        refund_status = "full" if p.status == Status.REFUNDED else "partial"
    f = p.failure
    out: dict[str, Any] = {
        "id": p.id,
        "entity": "payment",
        "amount": p.amount,
        "currency": p.currency,
        "status": p.status.value,
        "order_id": p.order_id,
        "invoice_id": None,
        "international": False,
        "method": p.method.value,
        "amount_refunded": p.amount_refunded,
        "refund_status": refund_status,
        "captured": p.captured,
        "description": p.description,
        "card_id": "card_" + _token(p.id + "card") if p.method == Method.CARD else None,
        "bank": p.bank,
        "wallet": p.wallet,
        "vpa": p.vpa,
        "email": p.email,
        "contact": p.contact,
        "notes": notes(p.notes),
        "fee": fee,
        "tax": tax,
        "error_code": f.code if f else None,
        "error_description": f.description if f else None,
        "error_source": f.source if f else None,
        "error_step": f.step if f else None,
        "error_reason": f.reason if f else None,
        "acquirer_data": _acquirer_data(p),
        "created_at": p.created_at,
    }
    if p.method == Method.UPI:
        out["upi"] = {"payer_account_type": "bank_account", "vpa": p.vpa, "flow": p.upi_flow}
    return out


def card_details(p: Payment) -> dict[str, Any]:
    return {
        "id": "card_" + _token(p.id + "card"),
        "entity": "card",
        "name": "",
        "last4": _digits(p.id + "last4", 4),
        "network": p.card_network,
        "type": "debit" if int(_digits(p.id + "type", 1)) < 6 else "credit",
        "issuer": p.issuer,
        "international": False,
        "emi": False,
        "sub_type": "consumer",
    }


def refund(r: Refund, refund_notes: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "id": r.id,
        "entity": "refund",
        "amount": r.amount,
        "currency": r.currency,
        "payment_id": r.payment_id,
        "notes": notes(refund_notes or {}),
        "receipt": None,
        "acquirer_data": {"arn": _digits(r.id + "arn", 23)},
        "created_at": r.created_at,
        "batch_id": None,
        "status": r.status,
        "speed_processed": r.speed,
        "speed_requested": r.speed,
    }


def order(order_id: str, attempts: list[Payment]) -> dict[str, Any]:
    first = min(attempts, key=lambda p: p.created_at)
    paid = any(p.captured for p in attempts)
    return {
        "id": order_id,
        "entity": "order",
        "amount": first.amount,
        "amount_paid": first.amount if paid else 0,
        "amount_due": 0 if paid else first.amount,
        "currency": first.currency,
        "receipt": None,
        "offer_id": None,
        "status": "paid" if paid else "attempted",
        "attempts": len(attempts),
        "notes": notes(first.notes if "coupon" in first.notes else {}),
        "created_at": first.created_at - 5,
    }


def settlement(s: Settlement) -> dict[str, Any]:
    return {
        "id": s.id,
        "entity": "settlement",
        "amount": s.amount,
        "status": s.status,
        "fees": s.fees,
        "tax": s.tax,
        "utr": s.utr,
        "created_at": s.created_at,
    }
