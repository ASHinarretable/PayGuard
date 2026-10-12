"""Payment Doctor's numbers. Everything here is pandas over provider-neutral PaymentRecords; the
LLM never computes a figure.

Money "lost" is counted per order: an order whose every attempt failed, attributed to the reason
of its last attempt. A failed attempt the customer retried successfully cost nothing.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

import pandas as pd

from payguard.core.action import FailureBucket, PaymentRecord

DAY = 86_400
BIN = 1_800  # 30-minute outage bins
OUTAGE_FACTOR = 3.0
OUTAGE_BIN_MIN = 2  # failures of the reason in one bin
OUTAGE_WINDOW_MIN = 5  # failures of the reason in a whole window
OUTAGE_EXPECTED_FLOOR = 0.1  # failures per bin; keeps a rare reason from spiking on a single pair
BASELINE_FLOOR = 0.02
REPEAT_MIN = 5  # a business-side reason that repeats this often is a pattern, not noise
DUPLICATE_WINDOW_S = 600
RECOVERABLE_MIN = 100_000  # ₹1,000 in paise
RECOVERY_WINDOW_S = DAY

OK_STATUSES = {"succeeded", "partially_refunded", "refunded", "authorized"}
FIXES = {
    "payment_method_not_enabled": "Enable the payment method in the dashboard",
    "upi_intent_not_enabled": "Enable UPI Intent in the dashboard's payment methods",
    "upi_collect_not_enabled": "Enable UPI Collect in the dashboard's payment methods",
    "card_network_not_enabled": "Enable the card network in the dashboard",
    "bank_not_enabled": "Enable the bank for netbanking in the dashboard",
}


def frame(records: Iterable[PaymentRecord]) -> pd.DataFrame:
    rows = []
    for r in records:
        rows.append({
            "id": r.id, "order_id": r.order_id or r.id, "amount": r.amount, "currency": r.currency,
            "status": r.status, "method": r.method, "created_at": r.created_at, "email": r.email,
            "phone": r.phone, "channel": r.channel, "failed": r.failure is not None,
            "reason": r.failure.code if r.failure else None,
            "bucket": r.failure.bucket.value if r.failure else None,
            "note_keys": tuple(r.note_keys), "customer": customer_key(r.phone, r.email),
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["ok"] = ~df["failed"] & df["status"].isin(OK_STATUSES)
    return df.sort_values("created_at", kind="stable").reset_index(drop=True)


def customer_key(phone: str | None, email: str | None) -> str | None:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) >= 10:
        return f"phone:{digits[-10:]}"
    return f"email:{email.strip().lower()}" if email else None


# ---- orders and lost money -----------------------------------------------------------------

def lost_orders(df: pd.DataFrame) -> pd.DataFrame:
    """One row per order that never succeeded: its last attempt."""
    ok_orders = set(df.loc[df["ok"], "order_id"])
    failed = df[df["failed"] & ~df["order_id"].isin(ok_orders)]
    return failed.groupby("order_id", sort=False).tail(1)


def bucket_totals(df: pd.DataFrame, end: int) -> list[dict]:
    lost = lost_orders(df)
    this_week = lost[lost["created_at"] >= end - 7 * DAY]
    last_week = lost[(lost["created_at"] >= end - 14 * DAY) & (lost["created_at"] < end - 7 * DAY)]
    attempts = df[df["failed"]].groupby("bucket").size()
    out = []
    for bucket in FailureBucket:
        b = bucket.value
        out.append({
            "bucket": b,
            "failed_attempts": int(attempts.get(b, 0)),
            "lost_orders": int((lost["bucket"] == b).sum()),
            "lost_paise": int(lost.loc[lost["bucket"] == b, "amount"].sum()),
            "this_week_paise": int(this_week.loc[this_week["bucket"] == b, "amount"].sum()),
            "last_week_paise": int(last_week.loc[last_week["bucket"] == b, "amount"].sum()),
        })
    return sorted(out, key=lambda r: -r["lost_paise"])


# ---- detector 2: outage windows ------------------------------------------------------------

def outages(df: pd.DataFrame) -> list[dict]:
    """Time windows where an outage-type reason (bank down, UPI app down, ...) spikes, and the bank
    or app behind it.

    One merchant sees only a few attempts per channel per 30 minutes, too few to judge a channel's
    rate bin by bin. So the spike is found on the reason first: per 30-minute bin, its count against
    its own average over the previous 7 days. Spiking bins merge into windows, and a window must
    hold enough failures to rule out chance. Then each channel's failure rate inside the window is
    compared with its own 7-day baseline; channels above 3x are the ones affected, and the worst is
    named."""
    fails = df[df["failed"] & (df["bucket"] == FailureBucket.OUTAGE.value)]
    if fails.empty:
        return []
    incidents = []
    bins_per_week = 7 * DAY // BIN
    for reason, g in fails.groupby("reason"):
        counts = (g["created_at"] // BIN).value_counts().sort_index()
        flagged = []
        for b, c in counts.items():
            prior = counts[(counts.index >= b - bins_per_week) & (counts.index < b - DAY // BIN)].sum()
            expected = max(prior / bins_per_week, OUTAGE_EXPECTED_FLOOR)
            if c >= OUTAGE_BIN_MIN and c > OUTAGE_FACTOR * expected:
                flagged.append(int(b))
        for run in _runs(flagged):
            lo, hi = run[0] * BIN, (run[-1] + 1) * BIN
            in_window = g[(g["created_at"] >= lo) & (g["created_at"] < hi)]
            if len(in_window) < OUTAGE_WINDOW_MIN:
                continue
            channels = _affected_channels(df, reason, lo, hi)
            incidents.append({"reason": reason, "start": lo, "end": hi, "failures": len(in_window),
                              "channels": channels, "primary": channels[0]["channel"] if channels else None})
    lost = lost_orders(df)
    for inc in incidents:
        hit = lost[(lost["created_at"] >= inc["start"]) & (lost["created_at"] < inc["end"])
                   & (lost["reason"] == inc["reason"])]
        inc["lost_orders"], inc["lost_paise"] = len(hit), int(hit["amount"].sum())
    return sorted(incidents, key=lambda i: i["start"])


def _runs(bins: list[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for b in bins:
        if runs and b - runs[-1][-1] <= 2:  # allow one quiet bin inside a window
            runs[-1].append(b)
        else:
            runs.append([b])
    return runs


def _affected_channels(df: pd.DataFrame, reason: str, lo: int, hi: int) -> list[dict]:
    out = []
    window = df[(df["created_at"] >= lo) & (df["created_at"] < hi) & df["channel"].notna()]
    prior = df[(df["created_at"] >= lo - 7 * DAY) & (df["created_at"] < lo - DAY // 2) & df["channel"].notna()]
    base = prior.groupby("channel")["failed"].mean()
    for channel, w in window.groupby("channel"):
        hits = int((w["reason"] == reason).sum())
        if hits < 2:
            continue
        rate = float(w["failed"].mean())
        baseline = float(base.get(channel, df.loc[df["channel"] == channel, "failed"].mean()))
        if rate > OUTAGE_FACTOR * max(baseline, BASELINE_FLOOR):
            out.append({"channel": channel, "attempts": len(w), "failures": hits, "rate": round(rate, 3),
                        "baseline": round(baseline, 3)})
    return sorted(out, key=lambda c: (-c["rate"], -c["failures"]))


# ---- detector 3 and the integration-bug half of the business bucket -------------------------

def config_gaps(df: pd.DataFrame) -> list[dict]:
    return _business_patterns(df, FailureBucket.MERCHANT_CONFIG)


def integration_bugs(df: pd.DataFrame) -> list[dict]:
    return _business_patterns(df, FailureBucket.INTEGRATION_BUG)


def _business_patterns(df: pd.DataFrame, bucket: FailureBucket) -> list[dict]:
    fails = df[df["bucket"] == bucket.value]
    lost = lost_orders(df)
    out = []
    for reason, g in fails.groupby("reason"):
        if len(g) < REPEAT_MIN:
            continue
        daily = g.groupby(g["created_at"] // DAY).size()
        item = {
            "reason": reason, "failed_attempts": len(g),
            "lost_orders": int((lost["reason"] == reason).sum()),
            "lost_paise": int(lost.loc[lost["reason"] == reason, "amount"].sum()),
            "first_seen": int(g["created_at"].min()), "days_seen": int(daily.size),
            "methods": sorted(g["method"].unique().tolist()),
            "examples": g["id"].head(3).tolist(),
        }
        if bucket == FailureBucket.MERCHANT_CONFIG:
            item["fix"] = FIXES.get(reason, "Check the payment settings in the dashboard")
        else:
            item["pattern"] = _note_pattern(df, g)
            item["fix"] = "Fix the integration; the failing pattern is shown"
        out.append(item)
    return sorted(out, key=lambda r: -r["lost_paise"])


def _note_pattern(df: pd.DataFrame, failing: pd.DataFrame) -> str | None:
    """A note key on (nearly) every failing attempt but on few attempts overall, e.g. a coupon."""
    since = df[df["created_at"] >= failing["created_at"].min()]
    keys = {k for ks in failing["note_keys"] for k in ks}
    for key in sorted(keys):
        in_fail = failing["note_keys"].map(lambda ks, k=key: k in ks).mean()
        overall = since["note_keys"].map(lambda ks, k=key: k in ks).mean()
        if in_fail >= 0.9 and overall < 0.5:
            return f"only orders with notes.{key} ({in_fail:.0%} of failures vs {overall:.0%} of all attempts)"
    return None


# ---- detector 4: duplicate charges ---------------------------------------------------------

def duplicate_charges(df: pd.DataFrame) -> list[dict]:
    paid = df[df["status"].isin({"succeeded", "partially_refunded", "refunded"}) & df["customer"].notna()]
    paid = paid.sort_values(["customer", "amount", "created_at"])
    prev = paid.groupby(["customer", "amount"])[["id", "order_id", "created_at"]].shift(1)
    gap = paid["created_at"] - prev["created_at"]
    hit = paid[prev["id"].notna() & (paid["order_id"] != prev["order_id"]) & (gap < DUPLICATE_WINDOW_S)]
    out = [{"first": prev.at[i, "id"], "second": row.id, "amount": int(row.amount), "customer": row.customer,
            "second_status": row.status, "gap_s": int(gap.at[i]), "created_at": int(row.created_at)}
           for i, row in zip(hit.index, hit.itertuples(), strict=True)]
    return sorted(out, key=lambda r: r["created_at"])


# ---- detector 5: recoverable carts ---------------------------------------------------------

def recoverable_carts(df: pd.DataFrame) -> list[dict]:
    lost = lost_orders(df)
    carts = lost[(lost["bucket"] == FailureBucket.CUSTOMER_DROP_OFF.value) & (lost["amount"] > RECOVERABLE_MIN)
                 & lost["customer"].notna()]
    successes = df[df["ok"] & df["customer"].notna()].groupby("customer")["created_at"].apply(list).to_dict()
    out = []
    for row in carts.to_dict("records"):
        later = successes.get(row["customer"], [])
        if any(row["created_at"] < t <= row["created_at"] + RECOVERY_WINDOW_S for t in later):
            continue
        out.append({"payment_id": row["id"], "order_id": row["order_id"], "amount": int(row["amount"]),
                    "currency": row["currency"], "reason": row["reason"], "email": row["email"],
                    "phone": row["phone"], "created_at": int(row["created_at"])})
    return sorted(out, key=lambda r: -r["amount"])
