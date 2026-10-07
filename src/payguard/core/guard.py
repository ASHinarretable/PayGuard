"""The guard: classify -> fetch state -> decide -> forward or refuse -> audit.

It talks to the provider only through an Adapter and an Upstream, so it works the same for every
provider and every interface (MCP server, HTTP gateway, library).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from payguard.core.action import (
    Action,
    ActionKind,
    ClassificationError,
    FailureReason,
    ResourceState,
    StateUnavailable,
)
from payguard.core.audit import AuditLog
from payguard.core.decide import Decision, decide
from payguard.core.policy import Policy
from payguard.core.rules import Context, RuleResult, Verdict
from payguard.core.rules.kind import NEEDS_STATE


@dataclass
class ToolOutcome:
    is_error: bool
    text: str
    raw: Any = None  # the interface's own result object, passed back untouched when a call is forwarded


class Upstream(Protocol):
    async def call(self, tool: str, args: dict[str, Any]) -> ToolOutcome: ...


class Adapter(Protocol):
    name: str

    def classify(self, tool: str, args: dict[str, Any]) -> Action: ...

    async def fetch_state(self, action: Action) -> ResourceState | None: ...

    def untrusted_paths(self, tool: str) -> list[str]: ...

    def is_live(self) -> bool: ...

    def normalize_error(self, payment: dict[str, Any]) -> FailureReason: ...


def _now() -> datetime:
    return datetime.now(UTC)


class Guard:
    def __init__(self, adapter: Adapter, upstream: Upstream, policy: Policy, audit: AuditLog,
                 clock: Callable[[], datetime] = _now) -> None:
        self.adapter = adapter
        self.upstream = upstream
        self.policy = policy
        self.audit = audit
        self.clock = clock

    async def handle(self, session_id: str, tool: str, args: dict[str, Any]) -> ToolOutcome:
        started = time.perf_counter()
        now = self.clock()
        try:
            action = self.adapter.classify(tool, args)
        except ClassificationError as exc:
            action = Action(provider=self.adapter.name, tool=tool, kind=ActionKind.UNKNOWN, raw_args=args)
            decision = Decision(verdict=Verdict.DENY,
                                fired=[RuleResult(rule="ARGS", verdict=Verdict.DENY, reason=str(exc))])
        else:
            decision = await self._decide(session_id, action, now)

        upstream_ms = 0.0
        executed, upstream_error = False, None
        if decision.verdict == Verdict.ALLOW:
            t0 = time.perf_counter()
            outcome = await self.upstream.call(tool, args)
            upstream_ms = (time.perf_counter() - t0) * 1000
            executed = not outcome.is_error
            upstream_error = outcome.text[:500] if outcome.is_error else None
        else:
            outcome = ToolOutcome(is_error=True, text=self._refusal(decision))

        if "R5" in decision.rules:
            self.audit.pause(session_id, decision.reason, now)
        self.audit.record(
            ts=now, session_id=session_id, action=action, verdict=decision.verdict, rules=decision.rules,
            reason=decision.reason, executed=executed, upstream_error=upstream_error,
            latency_ms=round((time.perf_counter() - started) * 1000 - upstream_ms),
        )
        return outcome

    async def _decide(self, session_id: str, action: Action, now: datetime) -> Decision:
        if not action.is_write:
            return Decision(verdict=Verdict.ALLOW)
        paused = self.audit.paused(session_id)
        if paused is not None:
            return Decision(verdict=Verdict.DENY, fired=[RuleResult(
                rule="PAUSED", verdict=Verdict.DENY,
                reason=f"session paused by the circuit breaker at {paused.paused_at:%H:%M} UTC; "
                       "a human must resume it before any more writes")])
        state = None
        if action.kind in NEEDS_STATE and action.resource_id:
            try:
                state = await self.adapter.fetch_state(action)
            except StateUnavailable:
                state = None
        ctx = Context(action=action, policy=self.policy, history=self.audit, now=now, session_id=session_id,
                      state=state, is_live=self.adapter.is_live())
        return decide(ctx)

    @staticmethod
    def _refusal(decision: Decision) -> str:
        tags = ",".join(decision.rules)
        hints = "".join(f" Hint: {h}" for h in decision.hints)
        if decision.verdict == Verdict.ESCALATE:
            return (f"PENDING_APPROVAL [{tags}]: a human must approve this call. {decision.reason}.{hints} "
                    "It has not been executed. Do not retry it.")
        return (f"DENIED by PayGuard [{tags}]: {decision.reason}.{hints} "
                "This call was not executed; do not retry it unchanged.")
