"""Dashboard API: audit feed, approvals (deciding from the dashboard), reports, evals."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from datagen.models import Status
from mock.store import Store
from payguard.adapters.razorpay import RazorpayAdapter
from payguard.core.audit import AuditLog
from payguard.core.guard import Guard
from payguard.core.policy import Policy
from payguard.interfaces.api import create_app
from tests.conftest import MERCHANT
from tests.test_guard import InProcessUpstream


@pytest.fixture
async def setup(data_dir, tmp_path):
    db = tmp_path / "payguard.db"
    store = Store(data_dir, MERCHANT)
    up = InProcessUpstream(store)
    guard = Guard(RazorpayAdapter(upstream=up), up, Policy.load("policies/default.yaml"), AuditLog(f"sqlite:///{db}"))
    small = next(p for p in store.payments.values() if p.status == Status.CAPTURED and p.amount < 400_000)
    big = next(p for p in store.payments.values() if p.status == Status.CAPTURED and p.amount > 500_100)
    await guard.handle("s1", "fetch_payment", {"payment_id": small.id})
    await guard.handle("s1", "create_refund", {"payment_id": small.id, "amount": 10_000})
    await guard.handle("s1", "revoke_token", {"customer_id": "c", "token_id": "t"})
    await guard.handle("s1", "create_refund", {"payment_id": big.id, "amount": big.amount})
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "demo-x.json").write_text('{"merchant": "demo-x"}', encoding="utf-8")
    app = create_app(f"sqlite:///{db}", reports, Path("evals/results"), Path("web/out"))
    return TestClient(app), guard, store


async def test_events_feed(setup):
    client, _, _ = setup
    events = client.get("/api/events").json()
    assert [e["verdict"] for e in events] == ["ALLOW", "ALLOW", "DENY", "ESCALATE"]
    assert events[2]["rules"] == ["KIND"] and events[1]["amount"] == {"minor": 10_000, "currency": "INR"}
    after = client.get("/api/events", params={"after": events[1]["id"]}).json()
    assert [e["tool"] for e in after] == ["revoke_token", "create_refund"]


async def test_approve_from_dashboard_then_proxy_executes(setup):
    client, guard, store = setup
    [pending] = [a for a in client.get("/api/approvals").json() if a["status"] == "pending"]
    assert pending["rules"] == ["R3"] and pending["state"]["status"] == "succeeded"
    r = client.post(f"/api/approvals/{pending['id']}/approve")
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["decided_by"] == "dashboard"
    assert client.post(f"/api/approvals/{pending['id']}/approve").status_code == 409  # can't decide twice
    [done] = await guard.process_approved()
    assert done.status == "executed" and len(store.ledger_rows()) == 2


async def test_bad_requests(setup):
    client, _, _ = setup
    assert client.post("/api/approvals/apr_nope/approve").status_code == 404
    assert client.post("/api/approvals/apr_nope/maybe").status_code == 400
    assert client.get("/api/reports/..%2Fsecret").status_code == 404


async def test_reports_and_static(setup):
    client, _, _ = setup
    assert client.get("/api/reports").json() == ["demo-x"]
    assert client.get("/api/reports/demo-x").json() == {"merchant": "demo-x"}
    assert client.get("/api/health").json()["mode"] == "live"
    if Path("web/out/index.html").exists():  # only after `npm run build`
        assert "PayGuard" in client.get("/").text


def test_evals_endpoint_shape():
    app = create_app("sqlite://", Path("reports"), Path("evals/results"), Path("web/out"))
    d = TestClient(app).get("/api/evals").json()
    assert {"headline", "summary", "categories", "setup_names", "runs"} <= set(d)
    assert datetime.now(UTC)  # keeps the import honest
