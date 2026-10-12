"""The weekly report: computed facts, the top fixes ranked by rupee impact, and proposed actions.

Every number here comes from doctor.analysis. The LLM's narrative is attached afterwards and is
checked against these facts (see doctor.narrate).
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field

from doctor import analysis as A
from payguard.core.action import PaymentRecord

IST = timezone(timedelta(hours=5, minutes=30))
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
OWNERS = {"merchant_config": "Merchant", "integration_bug": "Developer", "outage": "Nobody (transient)",
          "customer_drop_off": "Agent, via PayGuard", "risk_and_limits": "Customer", "other": "Merchant"}


class Fix(BaseModel):
    title: str
    owner: str
    impact_paise: int
    detail: str


class ProposedAction(BaseModel):
    kind: str  # "refund_duplicate" | "recovery_link"
    reason: str
    payment_id: str
    amount: int
    calls: list[dict[str, Any]]  # the tool calls, in order; later calls may reference earlier results
    verdicts: list[str] = Field(default_factory=list)  # filled in when executed through PayGuard
    outcome: str | None = None


class Report(BaseModel):
    merchant: str
    currency: str
    start: int
    end: int
    attempts: int
    failed_attempts: int
    lost_paise: int
    this_week_paise: int
    last_week_paise: int
    buckets: list[dict[str, Any]]
    outages: list[dict[str, Any]]
    config_gaps: list[dict[str, Any]]
    integration_bugs: list[dict[str, Any]]
    duplicates: list[dict[str, Any]]
    recoverable_carts: list[dict[str, Any]]
    top_fixes: list[Fix]
    actions: list[ProposedAction]
    narrative: str | None = None
    narrative_source: str | None = None  # model id, or "template"
    narrative_warnings: list[str] = Field(default_factory=list)


def rupees(paise: int) -> str:
    return f"₹{paise / 100:,.0f}" if paise % 100 == 0 else f"₹{paise / 100:,.2f}"


def when(ts: int, fmt: str = "%d %b %H:%M") -> str:
    return datetime.fromtimestamp(ts, IST).strftime(fmt)


def build(merchant: str, records: list[PaymentRecord], *, recent_days: int = 7) -> Report:
    df = A.frame(records)
    end = int(df["created_at"].max()) + 1
    buckets = A.bucket_totals(df, end)
    outages, gaps, bugs = A.outages(df), A.config_gaps(df), A.integration_bugs(df)
    dups, carts = A.duplicate_charges(df), A.recoverable_carts(df)
    recent = end - recent_days * A.DAY

    fixes = [Fix(title=g["fix"], owner=OWNERS["merchant_config"], impact_paise=g["lost_paise"],
                 detail=f"{g['failed_attempts']} attempts failed with {g['reason']} on {g['days_seen']} days")
             for g in gaps]
    fixes += [Fix(title=f"Fix {b['reason']} in checkout", owner=OWNERS["integration_bug"],
                  impact_paise=b["lost_paise"],
                  detail=f"{b['failed_attempts']} attempts failed since {when(b['first_seen'])}; "
                         f"{b.get('pattern') or 'no shared pattern found'}")
              for b in bugs]
    fixes += [Fix(title=f"{o['reason']} on {o['primary']}: tell customers to switch method during outages",
                  owner=OWNERS["outage"], impact_paise=o["lost_paise"],
                  detail=f"{when(o['start'])}-{when(o['end'], '%H:%M')} IST, {o['failures']} failures")
              for o in outages]
    if carts:
        fixes.append(Fix(title="Send payment links to customers who dropped off", owner=OWNERS["customer_drop_off"],
                         impact_paise=sum(c["amount"] for c in carts),
                         detail=f"{len(carts)} carts over {rupees(A.RECOVERABLE_MIN)} with no later payment"))
    if dups:
        fixes.append(Fix(title="Refund duplicate charges before customers dispute them",
                         owner=OWNERS["customer_drop_off"], impact_paise=sum(d["amount"] for d in dups),
                         detail=f"{len(dups)} customers charged twice within 10 minutes"))
    fixes.sort(key=lambda f: -f.impact_paise)

    actions = [ProposedAction(kind="refund_duplicate", payment_id=d["second"], amount=d["amount"],
                              reason=f"duplicate of {d['first']}, {d['gap_s'] // 60} min apart",
                              calls=[{"tool": "create_refund", "args": {"payment_id": d["second"], "amount": d["amount"]}}])
               for d in dups if d["second_status"] == "succeeded" and d["created_at"] >= recent]
    actions += [_recovery(c) for c in carts if c["created_at"] >= recent]

    lost = sum(b["lost_paise"] for b in buckets)
    return Report(
        merchant=merchant, currency=str(df["currency"].mode().iat[0]), start=int(df["created_at"].min()), end=end,
        attempts=len(df), failed_attempts=int(df["failed"].sum()), lost_paise=lost,
        this_week_paise=sum(b["this_week_paise"] for b in buckets),
        last_week_paise=sum(b["last_week_paise"] for b in buckets),
        buckets=buckets, outages=outages, config_gaps=gaps, integration_bugs=bugs, duplicates=dups,
        recoverable_carts=carts, top_fixes=fixes[:3], actions=actions,
    )


def _recovery(cart: dict[str, Any]) -> ProposedAction:
    args: dict[str, Any] = {"amount": cart["amount"], "currency": cart["currency"],
                            "description": f"Complete your payment for order {cart['order_id']}",
                            "reference_id": cart["order_id"]}
    email = (cart.get("email") or "").strip()
    if EMAIL_RE.fullmatch(email):  # a malformed (or poisoned) email field is left out, not cleaned up
        args["customer_email"] = email
    if cart.get("phone"):
        args["customer_contact"] = cart["phone"]
    return ProposedAction(
        kind="recovery_link", payment_id=cart["payment_id"], amount=cart["amount"],
        reason=f"{cart['reason']} with no later payment from this customer",
        calls=[{"tool": "create_payment_link", "args": args},
               {"tool": "payment_link_notify", "args": {"payment_link_id": "$prev.id", "medium": "sms"}}])


# ---- markdown ------------------------------------------------------------------------------

def markdown(r: Report) -> str:
    wow = r.this_week_paise - r.last_week_paise
    lines = [
        f"# Payment Doctor: {r.merchant}",
        (f"{when(r.start, '%d %b')} to {when(r.end - 1, '%d %b %Y')} · {r.attempts:,} attempts, "
         f"{r.failed_attempts:,} failed"),
        "",
        (f"**{rupees(r.lost_paise)} lost** to orders that never went through. "
         f"This week {rupees(r.this_week_paise)}, {'up' if wow >= 0 else 'down'} {rupees(abs(wow))} on last week."),
        "",
    ]
    if r.narrative:
        lines += [r.narrative, ""]
        lines += [f"> Note: {w}" for w in r.narrative_warnings]
        lines.append("")
    lines += ["## Top fixes", ""]
    lines += [f"{i}. **{f.title}** ({f.owner}): {rupees(f.impact_paise)}. {f.detail}"
              for i, f in enumerate(r.top_fixes, 1)]
    lines += ["", "## Where the money went", "", "| Bucket | Failed attempts | Lost orders | Lost | This week | Last week |",
              "|---|---:|---:|---:|---:|---:|"]
    lines += [f"| {b['bucket']} | {b['failed_attempts']} | {b['lost_orders']} | {rupees(b['lost_paise'])} | "
              f"{rupees(b['this_week_paise'])} | {rupees(b['last_week_paise'])} |" for b in r.buckets if b["failed_attempts"]]
    if r.outages:
        lines += ["", "## Outages", ""]
        lines += [f"- {when(o['start'])}-{when(o['end'], '%H:%M')} IST: **{o['reason']}** on {o['primary']} "
                  f"({', '.join(f'{c['channel']} {c['rate']:.0%} vs {c['baseline']:.0%} usual' for c in o['channels'])}); "
                  f"{o['failures']} failures, {rupees(o['lost_paise'])} lost" for o in r.outages]
    for title, items in (("Configuration gaps", r.config_gaps), ("Integration bugs", r.integration_bugs)):
        if items:
            lines += ["", f"## {title}", ""]
            lines += [f"- **{g['reason']}**: {g['failed_attempts']} failed attempts, {rupees(g['lost_paise'])} lost. "
                      f"{g['fix']}.{(' Pattern: ' + g['pattern'] + '.') if g.get('pattern') else ''}" for g in items]
    lines += ["", f"## Proposed actions ({len(r.actions)})", ""]
    for a in r.actions:
        verdict = f" → {', '.join(a.verdicts)}" if a.verdicts else ""
        lines.append(f"- {a.kind} {a.payment_id} {rupees(a.amount)}: {a.reason}{verdict}")
    return "\n".join(lines) + "\n"
