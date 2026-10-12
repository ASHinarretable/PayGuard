"""Scores a Doctor report against ground_truth.json: did it find what was planted?"""

from __future__ import annotations

from typing import Any

from doctor.analysis import OUTAGE_WINDOW_MIN
from doctor.report import Report

TOLERANCE_S = 30 * 60


def _ts(iso: str) -> int:
    from datetime import datetime

    return int(datetime.fromisoformat(iso).timestamp())


def score(report: Report, truth: dict[str, Any]) -> dict[str, Any]:
    m = report.merchant
    out: dict[str, Any] = {"merchant": m}

    found_outages, matched = [], set()
    for t in truth["outages"]:
        seen = t["failed_attempts"].get(m, 0)
        if seen < OUTAGE_WINDOW_MIN:  # one or two failures at this merchant: nothing to detect
            if seen:
                found_outages.append({"type": t["type"], "found": None, "note": f"only {seen} failures here"})
            continue
        channels = {f"bank:{v}" if k == "netbanking_bank" else f"upi:{v}" for k, v in t["visible_as"].items()}
        hit = next((i for i, o in enumerate(report.outages)
                    if o["reason"] == t["reason"] and o["primary"] in channels
                    and abs(o["start"] - _ts(t["start"])) <= TOLERANCE_S
                    and abs(o["end"] - _ts(t["end"])) <= TOLERANCE_S), None)
        found_outages.append({"type": t["type"], "found": hit is not None})
        if hit is not None:
            matched.add(hit)
    out["outages"] = found_outages
    out["false_outages"] = len(report.outages) - len(matched)

    gaps = [g for g in truth["config_gaps"] if g["merchant"] == m]
    out["config_gaps"] = [{"reason": g["reason"], "found": any(r["reason"] == g["reason"] for r in report.config_gaps)}
                          for g in gaps]
    bugs = [b for b in truth["integration_bugs"] if b["merchant"] == m]
    out["integration_bugs"] = [{"reason": b["reason"],
                                "found": any(r["reason"] == b["reason"] and "coupon" in (r.get("pattern") or "")
                                             for r in report.integration_bugs)} for b in bugs]

    want = {(d["first"], d["second"]) for d in truth["duplicates"] if d["merchant"] == m}
    got = {(d["first"], d["second"]) for d in report.duplicates}
    out["duplicates"] = {"truth": len(want), "found": len(got), "correct": len(want & got)}
    want_carts = {c["payment_id"] for c in truth["recoverable_carts"] if c["merchant"] == m}
    got_carts = {c["payment_id"] for c in report.recoverable_carts}
    out["recoverable_carts"] = {"truth": len(want_carts), "found": len(got_carts), "correct": len(want_carts & got_carts)}

    lost_truth = truth["summary"][m]["lost_paise_total"]
    out["lost_paise"] = {"truth": lost_truth, "report": report.lost_paise}
    return out


def passed(s: dict[str, Any]) -> bool:
    return (all(o["found"] is not False for o in s["outages"]) and s["false_outages"] == 0
            and all(g["found"] for g in s["config_gaps"]) and all(b["found"] for b in s["integration_bugs"])
            and s["duplicates"]["correct"] == s["duplicates"]["truth"] == s["duplicates"]["found"]
            and s["lost_paise"]["truth"] == s["lost_paise"]["report"])
