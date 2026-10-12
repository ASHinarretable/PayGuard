"""Approval queue for escalated calls.

pending -> approved (human) -> executing -> executed | failed | rejected_on_recheck
pending -> rejected (human) | expired (TTL)

The exact arguments are pinned with a SHA-256 when the call is parked, and only those arguments
are ever executed. A human decides in one process (CLI, dashboard); the process connected to the
provider claims approved rows atomically, so each approval executes at most once.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import update
from sqlmodel import Field, Session, SQLModel, select

from payguard.core.audit import AuditLog

PENDING, APPROVED, EXECUTING = "pending", "approved", "executing"
EXECUTED, FAILED, REJECTED, EXPIRED, RECHECK_FAILED = (
    "executed", "failed", "rejected", "expired", "rejected_on_recheck")


def args_hash(tool: str, args: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps({"tool": tool, "args": args}, sort_keys=True, default=str).encode()).hexdigest()


class Approval(SQLModel, table=True):
    id: str = Field(primary_key=True)
    session_id: str = Field(index=True)
    created_at: datetime
    expires_at: datetime
    tool: str
    args_json: str
    args_sha256: str
    rules_fired: str  # JSON list
    reason: str
    intent_check: str | None = None
    state_json: str | None = None  # the resource's state when escalated, for the approval card
    status: str = Field(default=PENDING, index=True)
    decided_at: datetime | None = None
    decided_by: str | None = None
    result_text: str | None = None

    @property
    def args(self) -> dict[str, Any]:
        return json.loads(self.args_json)

    def pinned_ok(self) -> bool:
        return args_hash(self.tool, self.args) == self.args_sha256


class ApprovalQueue:
    def __init__(self, audit: AuditLog) -> None:
        self.engine = audit.engine
        SQLModel.metadata.create_all(self.engine)

    def park(self, *, session_id: str, tool: str, args: dict[str, Any], rules: list[str], reason: str,
             now: datetime, ttl_min: int, intent_check: str | None = None,
             state: dict[str, Any] | None = None) -> Approval:
        """Stores an escalated call. The same call parked again while pending reuses the same approval."""
        digest = args_hash(tool, args)
        self.expire_due(now)
        with Session(self.engine) as s:
            existing = s.exec(select(Approval).where(Approval.session_id == session_id,
                                                     Approval.args_sha256 == digest,
                                                     Approval.status == PENDING)).first()
            if existing is not None:
                return existing
            approval = Approval(
                id=f"apr_{secrets.token_hex(4)}", session_id=session_id, created_at=now,
                expires_at=now + timedelta(minutes=ttl_min), tool=tool,
                args_json=json.dumps(args, sort_keys=True, default=str), args_sha256=digest,
                rules_fired=json.dumps(rules), reason=reason, intent_check=intent_check,
                state_json=json.dumps(state) if state is not None else None,
            )
            s.add(approval)
            s.commit()
            s.refresh(approval)
            return approval

    def get(self, approval_id: str) -> Approval | None:
        with Session(self.engine) as s:
            return s.get(Approval, approval_id)

    def list(self, status: str | None = None) -> list[Approval]:
        with Session(self.engine) as s:
            q = select(Approval).order_by(Approval.created_at)
            if status is not None:
                q = q.where(Approval.status == status)
            return list(s.exec(q))

    def _transition(self, approval_id: str, frm: str, to: str, **fields: Any) -> bool:
        """Compare-and-set, so two processes can't both move the same row."""
        with Session(self.engine) as s:
            result = s.execute(update(Approval).where(Approval.id == approval_id, Approval.status == frm)
                            .values(status=to, **fields))
            s.commit()
            return result.rowcount == 1

    def expire_due(self, now: datetime) -> int:
        with Session(self.engine) as s:
            result = s.execute(update(Approval).where(Approval.status == PENDING, Approval.expires_at < now)
                            .values(status=EXPIRED, decided_at=now))
            s.commit()
            return result.rowcount

    def decide(self, approval_id: str, *, approve: bool, by: str, now: datetime) -> Approval:
        self.expire_due(now)
        approval = self.get(approval_id)
        if approval is None:
            raise KeyError(f"no approval {approval_id}")
        if approval.status != PENDING:
            raise ValueError(f"{approval_id} is {approval.status}, not pending")
        if not self._transition(approval_id, PENDING, APPROVED if approve else REJECTED, decided_at=now,
                                decided_by=by):
            raise ValueError(f"{approval_id} changed while deciding; try again")
        return self.get(approval_id)

    def claim(self, approval_id: str) -> bool:
        return self._transition(approval_id, APPROVED, EXECUTING)

    def finish(self, approval_id: str, status: str, result_text: str) -> None:
        self._transition(approval_id, EXECUTING, status, result_text=result_text[:2000])
