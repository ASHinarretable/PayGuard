"""Runs scenarios: reset mock state -> run the agent in one setup -> score the ledger -> append JSONL.

Setups:
  unguarded    agent talks to the mock directly
  prompt_only  same, plus a strong "never follow instructions in data" system prompt
  payguard     agent talks through PayGuard (same Guard code as the MCP proxy, called in-process)

Results are appended one line per run, and runs already in the file are skipped, so a run that hits
a daily quota can simply be started again tomorrow.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evals.agent import LLM, PROMPT_DEFENCE, SYSTEM, QuotaExhausted, run_agent
from evals.scenario import WRITE_TOOLS, Scenario, score
from mock.server import call as mock_call
from mock.server import load_tools
from mock.store import Store
from payguard.adapters.razorpay import RazorpayAdapter
from payguard.core.audit import AuditLog
from payguard.core.guard import Guard, ToolOutcome
from payguard.core.policy import Policy
from payguard.intent_llm import LiteLLMIntentChecker

SETUPS = ("unguarded", "prompt_only", "payguard")
_TOOLS = {t.name: t for t in load_tools()[1]}


# The spec's token diet: the mock returns only the fields an agent needs. Applied the same way in
# every setup (PayGuard's own state fetches go through this channel too, and need only these).
LEAN_FIELDS = ("id", "entity", "amount", "currency", "status", "order_id", "method", "amount_refunded",
               "refund_status", "captured", "description", "bank", "wallet", "vpa", "email", "contact", "notes",
               "error_reason", "error_source", "created_at", "payment_id", "short_url", "customer", "count", "items")


KEEP_WHOLE = frozenset({"notes", "customer"})  # free-form maps: every key in them matters


def lean(node: Any) -> Any:
    """Drops entity fields an agent doesn't need. Never filters inside notes or customer, where the
    keys are the customer's own (and where injected text lives)."""
    if isinstance(node, dict):
        return {k: (v if k in KEEP_WHOLE else lean(v)) for k, v in node.items()
                if k in LEAN_FIELDS and v is not None and v != []}
    if isinstance(node, list):
        return [lean(v) for v in node]
    return node


class _Direct:
    def __init__(self, store: Store) -> None:
        self.store = store

    async def call(self, tool: str, args: dict[str, Any]) -> ToolOutcome:
        result = mock_call(self.store, _TOOLS, tool, args)
        text = result.content[0].text
        if not result.is_error:
            try:
                text = json.dumps(lean(json.loads(text)), ensure_ascii=False, separators=(",", ":"))
            except ValueError:
                pass
        return ToolOutcome(is_error=bool(result.is_error), text=text)


@dataclass
class RunConfig:
    data_dir: Path
    policy: Policy
    intent_model: str | None
    max_turns: int = 10


async def run_one(cfg: RunConfig, llm: LLM, scenario: Scenario, setup: str, trial: int) -> dict[str, Any]:
    store = Store(cfg.data_dir, scenario.merchant, overrides=scenario.overrides(), faults=scenario.setup.faults)
    direct = _Direct(store)
    system = SYSTEM.format(merchant=scenario.merchant) + (PROMPT_DEFENCE if setup == "prompt_only" else "")
    audit = None
    if setup == "payguard":
        audit = AuditLog()
        intent = LiteLLMIntentChecker(cfg.intent_model) if cfg.intent_model else None
        guard = Guard(RazorpayAdapter(upstream=direct), direct, cfg.policy, audit, intent=intent)
        session = f"ses_{scenario.id}_{trial}"
        guard.start_session(session, scenario.user_task)

        async def call_tool(name: str, args: dict[str, Any]) -> tuple[bool, str]:
            out = await guard.handle(session, name, args)
            return out.is_error, out.text
    else:
        async def call_tool(name: str, args: dict[str, Any]) -> tuple[bool, str]:
            out = await direct.call(name, args)
            return out.is_error, out.text

    started = time.perf_counter()
    trace = await run_agent(llm, system, scenario.user_task, call_tool, cfg.max_turns, salt=f"t{trial}")
    result = score(scenario, store.ledger_rows())

    writes_attempted = [c for c in trace.tool_calls if c["tool"] in WRITE_TOOLS]
    row: dict[str, Any] = {
        "scenario": scenario.id, "category": scenario.category, "attack": scenario.is_attack,
        "setup": setup, "trial": trial, "model": llm.model, **result,
        "writes_attempted": len(writes_attempted), "turns": trace.turns, "error": trace.error,
        "prompt_tokens": trace.prompt_tokens, "completion_tokens": trace.completion_tokens,
        "seconds": round(time.perf_counter() - started, 1),
        "tool_calls": [{k: c[k] for k in ("tool", "args", "error")} for c in trace.tool_calls],
        "final": trace.final[:500],
    }
    if audit is not None:
        events = audit.events()
        writes = [e for e in events if e.kind != "read"]
        row["guard"] = {
            "write_calls": len(writes),
            "denied": sum(e.verdict == "DENY" for e in writes),
            "escalated": sum(e.verdict == "ESCALATE" for e in writes),
            "rules": sorted({r for e in writes for r in json.loads(e.rules_fired)}),
            "intent": [e.intent_check for e in writes if e.intent_check],
            "latency_ms": [e.latency_ms for e in events],
        }
    return row


def done_keys(path: Path) -> set[tuple[str, str, int]]:
    if not path.exists():
        return set()
    keys = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            keys.add((r["scenario"], r["setup"], r["trial"]))
    return keys


async def run_matrix(cfg: RunConfig, llm: LLM, scenarios: list[Scenario], setups: list[str], trials: int,
                     out: Path, log=print) -> int:
    out.parent.mkdir(parents=True, exist_ok=True)
    done = done_keys(out)
    todo = [(s, setup, t) for t in range(trials) for s in scenarios for setup in setups
            if (s.id, setup, t) not in done]
    log(f"{llm.model}: {len(done)} runs already done, {len(todo)} to go -> {out}")
    for i, (s, setup, t) in enumerate(todo, 1):
        try:
            row = await run_one(cfg, llm, s, setup, t)
        except QuotaExhausted as exc:
            log(f"daily quota exhausted for {llm.model}; resume later. ({exc})")
            return i - 1
        with out.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        flag = "UNSAFE" if row["unsafe"] else ("ok" if row["success"] else "incomplete")
        log(f"[{i}/{len(todo)}] {s.id} {setup:11} t{t}  {flag:10} moved=₹{row['paise_moved'] / 100:,.0f} "
            f"writes={row['writes_attempted']} turns={row['turns']} {row['seconds']}s")
    return len(todo)
