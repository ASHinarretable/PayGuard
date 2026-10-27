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

Binds to 127.0.0.1 by default: approving here moves money, so it is not for the open network. A page
open in the same browser can still reach 127.0.0.1, so the API also defends itself:
  - the Host header must be a loopback name (or one given with --allow-host): defeats DNS rebinding
  - every POST needs the X-PayGuard-CSRF header, which a cross-origin page cannot send without a CORS
    preflight this server never grants, and an Origin header, when present, must match Host
  - responses forbid framing (clickjacking on the approve button) and restrict what the page may load
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sse_starlette.sse import EventSourceResponse
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from payguard.core.approvals import Approval, ApprovalQueue
from payguard.core.audit import AuditEvent, AuditLog
from payguard.core.redact import redact

POLL_S = 0.5
MAX_STREAMS = 16  # each live feed polls the database; a handful of dashboards is plenty
LOOPBACK = frozenset({"127.0.0.1", "localhost", "[::1]"})
CSRF_HEADER = "x-payguard-csrf"
CSP = (b"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
       b"img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"content-security-policy", CSP),
]


def hostname(host: str) -> str:
    """'127.0.0.1:8787' -> '127.0.0.1'; '[::1]:8787' -> '[::1]'."""
    host = host.strip().lower()
    if host.startswith("["):
        return host.split("]")[0] + "]"
    return host.rsplit(":", 1)[0] if ":" in host else host


class Guarded:
    """ASGI middleware: Host allowlist, CSRF check on writes, security headers on every response."""

    def __init__(self, app: ASGIApp, allowed_hosts: frozenset[str]) -> None:
        self.app = app
        self.allowed_hosts = allowed_hosts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        refusal = self.refuse(scope["method"], Headers(scope=scope))

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), *SECURITY_HEADERS]
            await send(message)

        if refusal is not None:
            await JSONResponse({"error": refusal[1]}, status_code=refusal[0])(scope, receive, send_with_headers)
            return
        await self.app(scope, receive, send_with_headers)

    def refuse(self, method: str, headers: Headers) -> tuple[int, str] | None:
        if hostname(headers.get("host", "")) not in self.allowed_hosts:
            return 421, "unexpected Host header"
        if method not in ("GET", "HEAD", "OPTIONS"):
            if headers.get(CSRF_HEADER) != "1":
                return 403, f"missing {CSRF_HEADER} header"
            origin = headers.get("origin")
            if origin is not None and origin.split("://", 1)[-1].lower() != headers.get("host", "").lower():
                return 403, "cross-origin request refused"
        return None


def clamp(value: str | None, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value))) if value is not None else default
    except ValueError:
        return default


def event_json(e: AuditEvent) -> dict[str, Any]:
    ts = e.ts if e.ts.tzinfo else e.ts.replace(tzinfo=UTC)
    return {
        "id": e.id, "ts": ts.isoformat(), "session_id": e.session_id, "provider": e.provider, "tool": e.tool,
        "kind": e.kind, "resource_id": e.resource_id,
        "amount": {"minor": e.amount_minor, "currency": e.currency} if e.amount_minor is not None else None,
        "verdict": e.verdict, "rules": json.loads(e.rules_fired), "reason": e.reason,
        "intent_check": e.intent_check, "latency_ms": e.latency_ms, "approval_id": e.approval_id,
        "executed": e.executed, "shadow": e.shadow, "args": json.loads(e.args_json),
    }


def approval_json(a: Approval) -> dict[str, Any]:
    return {
        "id": a.id, "session_id": a.session_id, "created_at": a.created_at.isoformat(),
        "expires_at": a.expires_at.isoformat(), "tool": a.tool, "args": redact(a.args), "rules": json.loads(a.rules_fired),
        "reason": a.reason, "intent_check": a.intent_check,
        "state": json.loads(a.state_json) if a.state_json else None, "status": a.status,
        "decided_by": a.decided_by, "result": a.result_text,
    }


def create_app(db_url: str, reports_dir: Path, results_dir: Path, static_dir: Path,
               allow_hosts: tuple[str, ...] = ()) -> ASGIApp:
    audit = AuditLog(db_url)
    streams = 0
    queue = ApprovalQueue(audit)

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, "mode": "live"})

    async def events(request: Request) -> JSONResponse:
        limit = clamp(request.query_params.get("limit"), 200, 1, 1000)
        rows = audit.events(after=request.query_params.get("after"))
        return JSONResponse([event_json(e) for e in rows[-limit:]])

    async def stream(request: Request) -> Response:
        if streams >= MAX_STREAMS:
            return JSONResponse({"error": "too many open live feeds"}, status_code=503)
        last = request.headers.get("last-event-id") or request.query_params.get("after")

        async def gen():
            nonlocal last, streams
            streams += 1
            try:
                while not await request.is_disconnected():
                    for e in audit.events(after=last):
                        last = e.id
                        yield {"id": e.id, "event": "audit", "data": json.dumps(event_json(e))}
                    await asyncio.sleep(POLL_S)
            finally:
                streams -= 1

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

    app = Starlette(routes=[
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
    return Guarded(app, LOOPBACK | {hostname(h) for h in allow_hosts})
