"""Turns evals/results/*.jsonl into the README's results table and headline sentence."""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

SETUP_NAMES = {"unguarded": "Unguarded", "prompt_only": "Prompt-only", "payguard": "PayGuard"}


def load(results_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(results_dir.glob("*.jsonl")):
        rows += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [r for r in rows if r.get("error") != "harness"]


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "—"


def summarise(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for model in sorted({r["model"] for r in rows}):
        for setup in SETUP_NAMES:
            rs = [r for r in rows if r["model"] == model and r["setup"] == setup]
            if not rs:
                continue
            attacks = [r for r in rs if r["attack"]]
            benign = [r for r in rs if not r["attack"]]
            item = {
                "model": model, "setup": setup, "runs": len(rs),
                "attack_runs": len(attacks), "unsafe": sum(r["unsafe"] for r in attacks),
                "paise_at_risk": sum(r["paise_at_risk"] for r in attacks),
                "benign_runs": len(benign), "benign_ok": sum(r["success"] for r in benign),
            }
            if setup == "payguard":
                benign_writes = sum(r["guard"]["write_calls"] for r in benign)
                blocked = sum(r["guard"]["denied"] + r["guard"]["escalated"] for r in benign)
                all_writes = sum(r["guard"]["write_calls"] for r in rs)
                item["false_block"] = (blocked, benign_writes)
                item["escalation"] = (sum(r["guard"]["escalated"] for r in rs), all_writes)
                for key, field in (("", "latency_ms"), ("rules_", "rules_ms")):
                    lat = sorted(x for r in rs for x in r["guard"].get(field, r["guard"]["latency_ms"]))
                    if lat:
                        item[f"{key}p50_ms"] = round(statistics.median(lat))
                        item[f"{key}p95_ms"] = lat[min(len(lat) - 1, int(0.95 * len(lat)))]
            out.append(item)
    return out


def table(summary: list[dict[str, Any]]) -> str:
    lines = ["| Model | Setup | Unsafe action rate | ₹ at risk | Benign success | False block rate |",
             "|---|---|---:|---:|---:|---:|"]
    for s in summary:
        fb = _pct(*s["false_block"]) if "false_block" in s else "—"
        lines.append(f"| {s['model'].split('/', 1)[1]} | {SETUP_NAMES[s['setup']]} | "
                     f"{_pct(s['unsafe'], s['attack_runs'])} ({s['unsafe']}/{s['attack_runs']}) | "
                     f"₹{s['paise_at_risk'] / 100:,.0f} | {_pct(s['benign_ok'], s['benign_runs'])} "
                     f"({s['benign_ok']}/{s['benign_runs']}) | {fb} |")
    return "\n".join(lines)


def by_category(rows: list[dict[str, Any]]) -> str:
    cats = ["injection", "amount", "duplicates", "scope", "ambiguity"]
    lines = ["| Setup | " + " | ".join(cats) + " |", "|---|" + "---:|" * len(cats)]
    for setup, name in SETUP_NAMES.items():
        cells = []
        for c in cats:
            rs = [r for r in rows if r["setup"] == setup and r["category"] == c]
            cells.append(_pct(sum(r["unsafe"] for r in rs), len(rs)) if rs else "—")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def headline(summary: list[dict[str, Any]], n_scenarios: int) -> str:
    un = [s for s in summary if s["setup"] == "unguarded"]
    pg = [s for s in summary if s["setup"] == "payguard"]
    if not un or not pg:
        return "(run both the unguarded and PayGuard setups to get the headline)"
    unsafe = sum(s["unsafe"] for s in un) / max(1, sum(s["attack_runs"] for s in un))
    risk = sum(s["paise_at_risk"] for s in un) / 100
    pg_unsafe = sum(s["unsafe"] for s in pg)
    ok = sum(s["benign_ok"] for s in pg) / max(1, sum(s["benign_runs"] for s in pg))
    return (f"Across {n_scenarios} scenarios, unguarded agents took an unsafe action in {unsafe:.0%} of attack cases "
            f"and moved ₹{risk:,.0f} they shouldn't have. With PayGuard: {pg_unsafe} unsafe actions, and "
            f"{ok:.0%} of legitimate tasks still completed.")


def render(results_dir: Path, n_scenarios: int) -> str:
    rows = load(results_dir)
    summary = summarise(rows)
    parts = ["# Results", "", headline(summary, n_scenarios), "", table(summary), "",
             "## Unsafe action rate by category (all models)", "", by_category(rows), ""]
    pg = [s for s in summary if s["setup"] == "payguard"]
    if pg:
        parts += ["## PayGuard overhead and escalations", "",
                  "| Model | Rules p50 | Rules p95 | With intent check p50 | p95 | Escalation rate |",
                  "|---|---:|---:|---:|---:|---:|"]
        parts += [f"| {s['model'].split('/', 1)[1]} | {s.get('rules_p50_ms', '—')} ms | {s.get('rules_p95_ms', '—')} ms | "
                  f"{s.get('p50_ms', '—')} ms | {s.get('p95_ms', '—')} ms | {_pct(*s['escalation'])} |" for s in pg]
    models = sorted({r["model"] for r in rows})
    parts += ["", (f"Models: {', '.join(models)}. Scored from the mock's ledger (did money actually move), "
                   "not from what the agent said."), ""]
    return "\n".join(parts)


CATEGORIES = ["injection", "amount", "duplicates", "scope", "ambiguity"]


def as_json(results_dir: Path, n_scenarios: int) -> dict[str, Any]:
    """The same numbers as the markdown, for the dashboard."""
    rows = load(results_dir)
    summary = summarise(rows)
    categories = {}
    for setup in SETUP_NAMES:
        categories[setup] = {}
        for c in CATEGORIES:
            rs = [r for r in rows if r["setup"] == setup and r["category"] == c]
            categories[setup][c] = [sum(r["unsafe"] for r in rs), len(rs)]
    return {"headline": headline(summary, n_scenarios), "summary": summary, "categories": categories,
            "setup_names": SETUP_NAMES, "models": sorted({r["model"] for r in rows}), "runs": len(rows)}
