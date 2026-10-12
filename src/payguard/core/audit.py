"""Audit log: one row per tool call. It is also the history that R4, R5 and R6 read.

SQLite for the local sidecar, Postgres for the shared gateway; only the URL changes.
"""

from __future__ import annotations

import itertools
import json
import secrets
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.pool import StaticPool
from sqlmodel import Field, Session, SQLModel, create_engine, select

from payguard.core.action import Action, ActionKind, Money
from payguard.core.rules import PastAction, Verdict


class AuditEvent(SQLModel, table=True):
    id: str = Field(primary_key=True)
    ts: datetime = Field(index=True)  # UTC
    session_id: str = Field(index=True)
    provider: str
    tool: str
    kind: str
    resource_id: str | None = None
    amount_minor: int | None = None
    currency: str | None = None
    args_json: str
    verdict: str
    rules_fired: str = "[]"  # JSON list of rule ids
    reason: str = ""
    intent_check: str | None = None
    latency_ms: int = 0
    approval_id: str | None = None
    executed: bool = False  # forwarded upstream and accepted by the provider
    uncertain: bool = False  # forwarded, but the provider's answer was lost (e.g. a timeout): it may have run
    upstream_error: str | None = None


class PausedSession(SQLModel, table=True):
    session_id: str = Field(primary_key=True)
    reason: str
    paused_at: datetime


def _utc(ts: datetime) -> datetime:
    return ts.astimezone(UTC)


def _aware(ts: datetime) -> datetime:
    # SQLite hands datetimes back without a zone; everything stored here is UTC.
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


_seq = itertools.count()


def new_event_id(ts: datetime) -> str:
    # Sorts like the log: milliseconds, then a per-process counter for events in the same millisecond,
    # then randomness so ids from different processes don't collide.
    return f"evt_{int(ts.timestamp() * 1000):012x}{next(_seq) % 16**6:06x}{secrets.token_hex(3)}"


class AuditLog:
    def __init__(self, url: str = "sqlite://") -> None:
        kwargs: dict[str, Any] = {}
        if url in ("sqlite://", "sqlite:///:memory:"):
            kwargs = {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool}
        self.engine = create_engine(url, **kwargs)
        SQLModel.metadata.create_all(self.engine)

    def record(self, *, ts: datetime, session_id: str, action: Action, verdict: Verdict, rules: list[str],
               reason: str, latency_ms: int, executed: bool, upstream_error: str | None = None,
               uncertain: bool = False,
               approval_id: str | None = None, intent_check: str | None = None) -> AuditEvent:
        event = AuditEvent(
            id=new_event_id(ts), ts=_utc(ts), session_id=session_id, provider=action.provider,
            tool=action.tool, kind=action.kind.value, resource_id=action.resource_id,
            amount_minor=action.amount.minor if action.amount else None,
            currency=action.amount.currency if action.amount else None,
            args_json=json.dumps(action.raw_args, sort_keys=True, default=str), verdict=verdict.value,
            rules_fired=json.dumps(rules), reason=reason, intent_check=intent_check, latency_ms=latency_ms,
            approval_id=approval_id, executed=executed, upstream_error=upstream_error, uncertain=uncertain,
        )
        with Session(self.engine) as s:
            s.add(event)
            s.commit()
            s.refresh(event)
        return event

    def events(self, session_id: str | None = None) -> list[AuditEvent]:
        with Session(self.engine) as s:
            q = select(AuditEvent).order_by(AuditEvent.ts, AuditEvent.id)
            if session_id is not None:
                q = q.where(AuditEvent.session_id == session_id)
            return list(s.exec(q))

    # ---- History, for R4/R5/R6 -----------------------------------------------------------

    def actions_since(self, since: datetime) -> list[PastAction]:
        with Session(self.engine) as s:
            rows = s.exec(select(AuditEvent).where(AuditEvent.ts >= _utc(since),
                                                   AuditEvent.kind != ActionKind.READ.value))
            return [PastAction(
                ts=_aware(e.ts), session_id=e.session_id, kind=ActionKind(e.kind), resource_id=e.resource_id,
                amount=Money(minor=e.amount_minor, currency=e.currency) if e.amount_minor is not None else None,
                verdict=Verdict(e.verdict), executed=e.executed, uncertain=e.uncertain,
            ) for e in rows]

    # ---- circuit-breaker pauses (R5) -----------------------------------------------------

    def pause(self, session_id: str, reason: str, ts: datetime) -> None:
        with Session(self.engine) as s:
            s.merge(PausedSession(session_id=session_id, reason=reason, paused_at=_utc(ts)))
            s.commit()

    def resume(self, session_id: str) -> None:
        with Session(self.engine) as s:
            row = s.get(PausedSession, session_id)
            if row is not None:
                s.delete(row)
                s.commit()

    def paused(self, session_id: str) -> PausedSession | None:
        with Session(self.engine) as s:
            return s.get(PausedSession, session_id)
