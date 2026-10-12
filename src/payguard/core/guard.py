"""The guard: classify -> fetch state -> decide -> forward, park or refuse -> audit.

Reads are forwarded and their customer-written fields labelled. Writes go through the rules and
the intent check; escalations are parked in the approval queue with their exact arguments.

It talks to the provider only through an Adapter and an Upstream, so it works the same for every
provider and every interface (MCP server, HTTP gateway, library).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from payguard.core.action import (
    Action,
    ActionKind,
    ClassificationError,
    Contact,
    FailureReason,
    ResourceState,
    StateUnavailable,
)
from payguard.core.approvals import (
    APPROVED,
    EXECUTED,
    FAILED,
    RECHECK_FAILED,
    Approval,
    ApprovalQueue,
)
from payguard.core.audit import AuditLog
from payguard.core.decide import Decision, decide
from payguard.core.intent import IntentChecker, IntentResult
from payguard.core.policy import Policy
from payguard.core.rules import RULES, Context, RuleResult, Verdict
from payguard.core.rules.kind import NEEDS_STATE
from payguard.core.taint import SessionTaint

RECHECK_ON_APPROVE = frozenset({"STATE", "R1", "R2", "R6"})  # state may have changed while parked


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

    def customer_contacts(self, tool: str, data: Any) -> list[Contact]: ...

    def outcome_unknown(self, error_text: str) -> bool: ...

    def is_live(self) -> bool: ...

    def normalize_error(self, payment: dict[str, Any]) -> FailureReason: ...


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class _Judgement:
    decision: Decision
    state: ResourceState | None = None
    intent: IntentResult | None = None
    intent_ms: float = 0.0


class Guard:
    def __init__(self, adapter: Adapter, upstream: Upstream, policy: Policy, audit: AuditLog,
                 intent: IntentChecker | None = None, clock: Callable[[], datetime] = _now) -> None:
        self.adapter = adapter
        self.upstream = upstream
        self.policy = policy
        self.audit = audit
        self.intent = intent
        self.clock = clock
        self.approvals = ApprovalQueue(audit)
        self.sessions: dict[str, SessionTaint] = {}

    def start_session(self, session_id: str, mandate: str | None = None) -> SessionTaint:
        """Records the human's task for a session. It can be set once, before the agent reads anything."""
        if session_id in self.sessions:
            raise ValueError(f"session {session_id} already started")
        self.sessions[session_id] = SessionTaint(mandate=mandate)
        return self.sessions[session_id]

    def session(self, session_id: str) -> SessionTaint:
        return self.sessions.setdefault(session_id, SessionTaint())

    # ---- one tool call -------------------------------------------------------------------

    async def handle(self, session_id: str, tool: str, args: dict[str, Any]) -> ToolOutcome:
        started = time.perf_counter()
        now = self.clock()
        try:
            action = self.adapter.classify(tool, args)
        except ClassificationError as exc:
            action = Action(provider=self.adapter.name, tool=tool, kind=ActionKind.UNKNOWN, raw_args=args)
            judged = _Judgement(Decision(verdict=Verdict.DENY,
                                         fired=[RuleResult(rule="ARGS", verdict=Verdict.DENY, reason=str(exc))]))
        else:
            judged = await self._judge(session_id, action, now)
        decision = judged.decision

        upstream_ms = 0.0
        executed, uncertain, upstream_error, approval_id = False, False, None, None
        if decision.verdict == Verdict.ALLOW:
            t0 = time.perf_counter()
            outcome = await self.upstream.call(tool, args)
            upstream_ms = (time.perf_counter() - t0) * 1000
            executed = not outcome.is_error
            upstream_error = outcome.text[:500] if outcome.is_error else None
            uncertain = outcome.is_error and action.is_write and self.adapter.outcome_unknown(outcome.text)
            if executed and not action.is_write:
                outcome = self._label(session_id, tool, outcome)
        elif decision.verdict == Verdict.ESCALATE:
            approval = self.approvals.park(
                session_id=session_id, tool=tool, args=args, rules=decision.rules, reason=decision.reason,
                now=now, ttl_min=self.policy.approval_ttl_min,
                intent_check=judged.intent.label() if judged.intent else None,
                state=judged.state.model_dump(mode="json") if judged.state else None)
            approval_id = approval.id
            outcome = ToolOutcome(is_error=True, text=self._pending(decision, approval))
        else:
            outcome = ToolOutcome(is_error=True, text=self._denied(decision))

        if "R5" in decision.rules:
            self.audit.pause(session_id, decision.reason, now)
        self.audit.record(
            ts=now, session_id=session_id, action=action, verdict=decision.verdict, rules=decision.rules,
            reason=decision.reason, executed=executed, upstream_error=upstream_error, approval_id=approval_id,
            uncertain=uncertain,
            intent_check=judged.intent.label() if judged.intent else None,
            latency_ms=round((time.perf_counter() - started) * 1000 - upstream_ms),
            intent_ms=round(judged.intent_ms),
        )
        return outcome

    async def _judge(self, session_id: str, action: Action, now: datetime) -> _Judgement:
        if not action.is_write:
            return _Judgement(Decision(verdict=Verdict.ALLOW))
        paused = self.audit.paused(session_id)
        if paused is not None:
            return _Judgement(Decision(verdict=Verdict.DENY, fired=[RuleResult(
                rule="PAUSED", verdict=Verdict.DENY,
                reason=f"session paused by the circuit breaker at {paused.paused_at:%H:%M} UTC; "
                       "a human must resume it before any more writes")]))
        state = await self._state(action)
        taint = self.session(session_id)
        ctx = Context(action=action, policy=self.policy, history=self.audit, now=now, session_id=session_id,
                      state=state, is_live=self.adapter.is_live(), taint=taint)
        decision = decide(ctx)
        intent, intent_ms = None, 0.0
        if decision.verdict != Verdict.DENY and self.intent is not None and taint.mandate:
            t0 = time.perf_counter()
            intent = await self._check_intent(taint.mandate, action)
            intent_ms = (time.perf_counter() - t0) * 1000
            if intent.aligned is False:
                decision = decision.plus(RuleResult(rule="INTENT", verdict=Verdict.ESCALATE,
                                                    reason=f"intent check: {intent.reason}"))
        return _Judgement(decision, state, intent, intent_ms)

    async def _state(self, action: Action) -> ResourceState | None:
        if action.kind not in NEEDS_STATE or not action.resource_id:
            return None
        try:
            return await self.adapter.fetch_state(action)
        except StateUnavailable:
            return None

    async def _check_intent(self, mandate: str, action: Action) -> IntentResult:
        try:
            return await self.intent.check(mandate, action)
        except Exception as exc:  # noqa: BLE001 - a backstop that errors must not block or allow anything
            return IntentResult(aligned=None, reason=f"check failed: {type(exc).__name__}")

    def _label(self, session_id: str, tool: str, outcome: ToolOutcome) -> ToolOutcome:
        paths = self.adapter.untrusted_paths(tool)
        try:
            data = json.loads(outcome.text)
        except ValueError:
            return outcome
        customers = self.adapter.customer_contacts(tool, data)
        labelled = self.session(session_id).observe(tool, data, paths, customers)
        return ToolOutcome(is_error=False, text=json.dumps(labelled, ensure_ascii=False))

    # ---- approvals -----------------------------------------------------------------------

    def approval_status(self, approval_id: str, session_id: str | None = None) -> str:
        approval = self.approvals.get(approval_id)
        if approval is None or (session_id is not None and approval.session_id != session_id):
            return f"No approval {approval_id} in this session."
        self.approvals.expire_due(self.clock())
        approval = self.approvals.get(approval_id)
        text = f"{approval.id}: {approval.status}"
        if approval.result_text:
            text += f". Result: {approval.result_text}"
        return text

    async def process_approved(self) -> list[Approval]:
        """Executes calls a human approved: re-checks the state-dependent rules, then runs the pinned
        arguments. Safe to call from several processes; each approval runs at most once."""
        done = []
        for approval in self.approvals.list(APPROVED):
            if not self.approvals.claim(approval.id):
                continue
            done.append(await self._execute_approved(approval))
        return [self.approvals.get(a.id) for a in done]

    async def _execute_approved(self, approval: Approval) -> Approval:
        now = self.clock()
        if not approval.pinned_ok():
            self.approvals.finish(approval.id, FAILED, "stored arguments no longer match their hash")
            return approval
        action = self.adapter.classify(approval.tool, approval.args)
        ctx = Context(action=action, policy=self.policy, history=self.audit, now=now,
                      session_id=approval.session_id, state=await self._state(action),
                      is_live=self.adapter.is_live())
        recheck = decide(ctx, [r for r in RULES if r.id in RECHECK_ON_APPROVE])
        if recheck.verdict == Verdict.DENY:
            self.approvals.finish(approval.id, RECHECK_FAILED, recheck.reason)
            self.audit.record(ts=now, session_id=approval.session_id, action=action, verdict=Verdict.DENY,
                              rules=["APPROVED", *recheck.rules], reason=f"approved, then {recheck.reason}",
                              executed=False, approval_id=approval.id, latency_ms=0)
            return approval
        outcome = await self.upstream.call(approval.tool, approval.args)
        self.approvals.finish(approval.id, FAILED if outcome.is_error else EXECUTED, outcome.text)
        self.audit.record(ts=now, session_id=approval.session_id, action=action, verdict=Verdict.ALLOW,
                          rules=["APPROVED"], reason=f"approved by {approval.decided_by}",
                          executed=not outcome.is_error, upstream_error=outcome.text[:500] if outcome.is_error
                          else None, approval_id=approval.id, latency_ms=0)
        return approval

    # ---- messages to the agent -----------------------------------------------------------

    @staticmethod
    def _pending(decision: Decision, approval: Approval) -> str:
        hints = "".join(f" Hint: {h}" for h in decision.hints)
        return (f"PENDING_APPROVAL {approval.id} [{','.join(decision.rules)}]: a human must approve this call. "
                f"{decision.reason}.{hints} It has not been executed. Do not retry it; "
                f"call payguard_approval_status with {approval.id} to check on it.")

    @staticmethod
    def _denied(decision: Decision) -> str:
        hints = "".join(f" Hint: {h}" for h in decision.hints)
        return (f"DENIED by PayGuard [{','.join(decision.rules)}]: {decision.reason}.{hints} "
                "This call was not executed; do not retry it unchanged.")
