"""Words for the report. An LLM (Gemini Flash by default) explains the computed facts; it is given
no raw payments, and any rupee figure it writes that isn't in the facts is flagged. Without a model
key, a plain template writes the summary instead.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from doctor.report import Report, rupees, when

DEFAULT_MODEL = "gemini/gemini-flash-latest"
RUPEE_RE = re.compile(r"₹\s?([\d,]+(?:\.\d{1,2})?)")
SYSTEM = (
    "You are Payment Doctor. Write 3-5 short sentences for a merchant explaining why payments failed and "
    "what to do first, using ONLY the facts given. Quote rupee amounts exactly as written in the facts; "
    "never compute, round or invent a number. Plain language, no headings, no bullet points."
)


def facts(r: Report) -> dict[str, Any]:
    return {
        "merchant": r.merchant,
        "period": f"{when(r.start, '%d %b')} to {when(r.end - 1, '%d %b')}",
        "lost_total": rupees(r.lost_paise),
        "lost_this_week": rupees(r.this_week_paise),
        "lost_last_week": rupees(r.last_week_paise),
        "by_bucket": {b["bucket"]: rupees(b["lost_paise"]) for b in r.buckets if b["lost_paise"]},
        "top_fixes": [{"fix": f.title, "owner": f.owner, "impact": rupees(f.impact_paise), "detail": f.detail}
                      for f in r.top_fixes],
        "outages": [{"reason": o["reason"], "where": o["primary"],
                     "when": f"{when(o['start'])}-{when(o['end'], '%H:%M')} IST", "lost": rupees(o["lost_paise"])}
                    for o in r.outages],
        "duplicate_charges": len(r.duplicates),
        "recoverable_carts": len(r.recoverable_carts),
    }


def check_numbers(text: str, allowed: dict[str, Any]) -> list[str]:
    """Rupee figures in the text that don't appear anywhere in the facts."""
    known = {m.group(1).replace(",", "") for m in RUPEE_RE.finditer(json.dumps(allowed, ensure_ascii=False))}
    return [f"₹{m.group(1)} is not in the computed facts" for m in RUPEE_RE.finditer(text)
            if m.group(1).replace(",", "") not in known]


def template(r: Report) -> str:
    parts = [f"{r.merchant} lost {rupees(r.lost_paise)} to orders that never went through."]
    if r.top_fixes:
        f = r.top_fixes[0]
        parts.append(f"The biggest single item is: {f.title.lower()} ({rupees(f.impact_paise)}; {f.detail}).")
    for o in r.outages:
        parts.append(f"{o['primary']} had an outage ({o['reason']}) on {when(o['start'])}-{when(o['end'], '%H:%M')} IST.")
    if r.duplicates:
        parts.append(f"{len(r.duplicates)} customers were charged twice; refunds are proposed below.")
    return " ".join(parts)


async def narrate(r: Report, model: str | None = None) -> Report:
    model = model or os.environ.get("DOCTOR_MODEL", DEFAULT_MODEL)
    payload = facts(r)
    key = os.environ.get("GROQ_API_KEY" if model.startswith("groq/") else "GEMINI_API_KEY", "")
    if not model.startswith("ollama/") and (not key or key.endswith("xxx")):
        r.narrative, r.narrative_source = template(r), "template"
        return r
    import litellm

    try:
        response = await litellm.acompletion(
            model=model, temperature=0, timeout=30,
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=1)}])
        text = (response.choices[0].message.content or "").strip()
    except Exception as exc:  # noqa: BLE001 - the report stands without the narrative
        r.narrative, r.narrative_source = template(r), "template"
        r.narrative_warnings = [f"model call failed ({type(exc).__name__}); used the template"]
        return r
    r.narrative, r.narrative_source = text, model
    r.narrative_warnings = check_numbers(text, payload)
    return r
