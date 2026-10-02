"""Builds ground_truth.json: what Payment Doctor should find, computed from the generated data.

Outages, the config gap and the coupon bug come from the planted constants. Duplicates and
recoverable carts are computed by their definitions, so anything the generator created by
chance is counted too.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from itertools import pairwise

from datagen.generate import (
    BANK_OUTAGE,
    CONFIG_GAP,
    COUPON_BUG,
    DROP_OFF_REASONS,
    GPAY_HANDLES,
    IST,
    PSP_OUTAGE,
    START,
    MerchantData,
    day_ts,
)
from datagen.merchants import OTHER_HANDLES
from datagen.models import Payment

RECOVERABLE_MIN_PAISE = 100_000
RECOVERY_WINDOW_S = 24 * 3600
DUPLICATE_WINDOW_S = 10 * 60


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, IST).isoformat()


def _orders(payments: list[Payment]) -> dict[str, list[Payment]]:
    orders: dict[str, list[Payment]] = defaultdict(list)
    for p in payments:
        orders[p.order_id].append(p)
    for attempts in orders.values():
        attempts.sort(key=lambda p: p.created_at)
    return orders


def _lost_by_reason(payments: list[Payment]) -> tuple[Counter, Counter]:
    """Orders that never succeeded, attributed to the reason of their last failed attempt."""
    lost_paise, lost_orders = Counter(), Counter()
    for attempts in _orders(payments).values():
        if any(p.failure is None for p in attempts):
            continue
        reason = attempts[-1].failure.reason
        lost_paise[reason] += attempts[-1].amount
        lost_orders[reason] += 1
    return lost_paise, lost_orders


def _successes_by_customer(payments: list[Payment]) -> dict[str, list[int]]:
    out: dict[str, list[int]] = defaultdict(list)
    for p in payments:
        if p.failure is None:
            out[p.customer_id].append(p.created_at)
    return out


def recoverable_carts(m: MerchantData) -> list[dict]:
    successes = _successes_by_customer(m.payments)
    carts = []
    for attempts in _orders(m.payments).values():
        last = attempts[-1]
        if (last.failure is None or last.failure.reason not in DROP_OFF_REASONS
                or last.amount <= RECOVERABLE_MIN_PAISE
                or any(p.failure is None for p in attempts)):
            continue
        later = successes.get(last.customer_id, [])
        if any(last.created_at < t <= last.created_at + RECOVERY_WINDOW_S for t in later):
            continue
        carts.append({"merchant": m.profile.name, "payment_id": last.id, "order_id": last.order_id,
                      "customer_id": last.customer_id, "amount": last.amount, "reason": last.failure.reason})
    return carts


def duplicate_charges(m: MerchantData) -> list[dict]:
    by_key: dict[tuple[str, int], list[Payment]] = defaultdict(list)
    for p in m.payments:
        if p.captured:
            by_key[(p.customer_id, p.amount)].append(p)
    planted = {pair for pair in m.duplicate_pairs}
    found = []
    for group in by_key.values():
        group.sort(key=lambda p: p.created_at)
        for a, b in pairwise(group):
            if a.order_id != b.order_id and b.created_at - a.created_at < DUPLICATE_WINDOW_S:
                found.append({"merchant": m.profile.name, "first": a.id, "second": b.id,
                              "customer_id": a.customer_id, "amount": a.amount,
                              "planted": (a.id, b.id) in planted})
    return found


def _window_failures(merchants: list[MerchantData], reason: str, lo: int, hi: int) -> dict[str, int]:
    return {m.profile.name: sum(1 for p in m.payments
                                if p.failure and p.failure.reason == reason and lo <= p.created_at < hi)
            for m in merchants}


def build_ground_truth(merchants: list[MerchantData], seed: int, days: int) -> dict:
    bank_lo, bank_hi = BANK_OUTAGE["window"].bounds()
    psp_lo, psp_hi = PSP_OUTAGE["window"].bounds()
    by_name = {m.profile.name: m for m in merchants}

    truth: dict = {
        "seed": seed,
        "days": days,
        "start": START.isoformat(),
        "end": _iso(day_ts(days + 1, 0)),
        "outages": [
            {"type": "bank", "issuer": BANK_OUTAGE["issuer"], "reason": BANK_OUTAGE["reason"],
             "visible_as": {"netbanking_bank": BANK_OUTAGE["issuer"],
                            "vpa_handle": GPAY_HANDLES[BANK_OUTAGE["issuer"]]},
             "start": _iso(bank_lo), "end": _iso(bank_hi),
             "failed_attempts": _window_failures(merchants, BANK_OUTAGE["reason"], bank_lo, bank_hi)},
            {"type": "psp", "upi_app": PSP_OUTAGE["upi_app"], "reason": PSP_OUTAGE["reason"],
             "visible_as": {"vpa_handle": OTHER_HANDLES[PSP_OUTAGE["upi_app"]]},
             "start": _iso(psp_lo), "end": _iso(psp_hi),
             "failed_attempts": _window_failures(merchants, PSP_OUTAGE["reason"], psp_lo, psp_hi)},
        ],
        "config_gaps": [],
        "integration_bugs": [],
        "duplicates": [],
        "recoverable_carts": [],
        "summary": {},
    }

    for m in merchants:
        lost_paise, lost_orders = _lost_by_reason(m.payments)
        failed = Counter(p.failure.reason for p in m.payments if p.failure)
        truth["summary"][m.profile.name] = {
            "attempts": len(m.payments),
            "orders": len(_orders(m.payments)),
            "successful": sum(1 for p in m.payments if p.failure is None),
            "failed": sum(failed.values()),
            "failed_by_reason": dict(failed.most_common()),
            "lost_orders_by_reason": dict(lost_orders.most_common()),
            "lost_paise_by_reason": dict(lost_paise.most_common()),
            "lost_paise_total": sum(lost_paise.values()),
        }
        truth["duplicates"].extend(duplicate_charges(m))
        truth["recoverable_carts"].extend(recoverable_carts(m))

    for spec, key, extra in (
        (CONFIG_GAP, "config_gaps", {"fix": "Enable UPI Intent in the payment methods settings"}),
        (COUPON_BUG, "integration_bugs", {"pattern": "orders with a coupon in notes",
                                          "from": _iso(day_ts(COUPON_BUG["from_day"], 0))}),
    ):
        m = by_name.get(spec["merchant"])
        if m is None:
            continue
        summary = truth["summary"][m.profile.name]
        truth[key].append({"merchant": m.profile.name, "reason": spec["reason"], **extra,
                           "failed_attempts": summary["failed_by_reason"].get(spec["reason"], 0),
                           "lost_paise": summary["lost_paise_by_reason"].get(spec["reason"], 0)})
    return truth
