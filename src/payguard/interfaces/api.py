"""REST API + live feed for the dashboard (Starlette, which FastAPI is built on; no extra dependencies).

    payguard dashboard --db payguard.db            # http://127.0.0.1:8787

Endpoints:
  GET  /api/health
  GET  /api/events?after=<event id>&limit=200     audit log, oldest first
  GET  /api/stream                                live feed: new audit events as server-sent events
  GET  /api/approvals                             pending and recent approvals
  POST /api/approvals/{id}/approve|reject         a human decides; the running proxy executes
  GET  /api/reports, /api/reports/{merchant}      Payment Doctor reports (reports/*.json)
  GET  /api/evals                                 eval results summary
  GET  /                                          the dashboard (the Next.js build in web/out)

Binds to 127.0.0.1 by default: approving here moves money, so it is not for the open network.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sse_starlette.sse import EventSourceResponse
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from payguard.core.approvals import Approval, ApprovalQueue
from payguard.core.audit import AuditEvent, AuditLog

POLL_S = 0.5


def event_json(e: AuditEvent) -> dict[str, Any]:
    ts = e.ts if e.ts.tzinfo else e.ts.replace(tzinfo=UTC)
    return {
        "id": e.id, "ts": ts.isoformat(), "session_id": e.session_id, "provider": e.provider, "tool": e.tool,
        "kind": e.kind, "resource_id": e.resource_id,
        "amount": {"minor": e.amount_minor, "currency": e.currency} if e.amount_minor is not None else None,
        "verdict": e.verdict, "rules": json.loads(e.rules_fired), "reason": e.reason,
        "intent_check": e.intent_check, "latency_ms": e.latency_ms, "approval_id": e.approval_id,
        "executed": e.executed, "args": json.loads(e.args_json),
    }


def approval_json(a: Approval) -> dict[str, Any]:
    return {
        "id": a.id, "session_id": a.session_id, "created_at": a.created_at.isoformat(),
        "expires_at": a.expires_at.isoformat(), "tool": a.tool, "args": a.args, "rules": json.loads(a.rules_fired),
        "reason": a.reason, "intent_check": a.intent_check,
        "state": json.loads(a.state_json) if a.state_json else None, "status": a.status,
        "decided_by": a.decided_by, "result": a.result_text,
    }


def create_app(db_url: str, reports_dir: Path, results_dir: Path, static_dir: Path) -> Starlette:
    audit = AuditLog(db_url)
    queue = ApprovalQueue(audit)

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "mode": "live"})

    def _after(events: list[AuditEvent], after: str | None) -> list[AuditEvent]:
        return [e for e in events if after is None or e.id > after]

    async def events(request: Request) -> JSONResponse:
        limit = int(request.query_params.get("limit", 200))
        rows = _after(audit.events(), request.query_params.get("after"))
        return JSONResponse([event_json(e) for e in rows[-limit:]])

    async def stream(request: Request) -> EventSourceResponse:
        last = request.headers.get("last-event-id") or request.query_params.get("after")

        async def gen():
            nonlocal last
            while not await request.is_disconnected():
                for e in _after(audit.events(), last):
                    last = e.id
                    yield {"id": e.id, "event": "audit", "data": json.dumps(event_json(e))}
                await asyncio.sleep(POLL_S)

        return EventSourceResponse(gen())

    async def approvals(request: Request) -> JSONResponse:
        queue.expire_due(datetime.now(UTC))
        rows = sorted(queue.list(), key=lambda a: a.created_at, reverse=True)[:100]
        return JSONResponse([approval_json(a) for a in rows])

    async def decide(request: Request) -> JSONResponse:
        approval_id, action = request.path_params["id"], request.path_params["action"]
        if action not in ("approve", "reject"):
            return JSONResponse({"error": "action must be approve or reject"}, status_code=400)
        try:
            a = queue.decide(approval_id, approve=action == "approve", by="dashboard", now=datetime.now(UTC))
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        return JSONResponse(approval_json(a))

    async def report_list(request: Request) -> JSONResponse:
        return JSONResponse(sorted(p.stem for p in reports_dir.glob("*.json")))

    async def report(request: Request) -> JSONResponse:
        name = request.path_params["merchant"]
        path = reports_dir / f"{name}.json"
        if not name.replace("-", "").isalnum() or not path.exists():
            return JSONResponse({"error": f"no report for {name}"}, status_code=404)
        return JSONResponse(json.loads(path.read_text(encoding="utf-8")))

    async def evals(request: Request) -> JSONResponse:
        try:
            from evals import report as eval_report
            from evals.scenario import load
        except ImportError:
            return JSONResponse({"error": "evals package not available"}, status_code=404)
        n = len(load(Path("evals/scenarios/razorpay")))
        return JSONResponse(eval_report.as_json(results_dir, n))

    return Starlette(routes=[
        Route("/api/health", health),
        Route("/api/events", events),
        Route("/api/stream", stream),
        Route("/api/approvals", approvals),
        Route("/api/approvals/{id}/{action}", decide, methods=["POST"]),
        Route("/api/reports", report_list),
        Route("/api/reports/{merchant}", report),
        Route("/api/evals", evals),
        *([Mount("/", StaticFiles(directory=str(static_dir), html=True), name="ui")] if static_dir.is_dir() else []),
    ])
