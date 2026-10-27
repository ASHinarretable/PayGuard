"""Dashboard API: audit feed, approvals (deciding from the dashboard), reports, evals."""

import json
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
    client = TestClient(app, base_url="http://127.0.0.1:8787", headers={"X-PayGuard-CSRF": "1"})
    return client, guard, store


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
    d = TestClient(app, base_url="http://127.0.0.1:8787").get("/api/evals").json()
    assert {"headline", "summary", "categories", "setup_names", "runs"} <= set(d)
    assert datetime.now(UTC)  # keeps the import honest


# ---- the API defends itself against other pages in the same browser -------------------------------

async def test_wrong_host_is_refused_dns_rebinding(setup):
    client, _, _ = setup
    assert client.get("/api/approvals", headers={"Host": "evil.example"}).status_code == 421
    assert client.get("/api/approvals", headers={"Host": "localhost:8787"}).status_code == 200
    assert client.get("/api/approvals", headers={"Host": "[::1]:8787"}).status_code == 200


async def test_writes_need_the_csrf_header_and_a_same_origin(setup):
    client, _, _ = setup
    [pending] = [a for a in client.get("/api/approvals").json() if a["status"] == "pending"]
    url = f"/api/approvals/{pending['id']}/approve"
    # what a malicious page can send: a simple cross-origin POST, with no way to add the custom header
    assert client.post(url, headers={"X-PayGuard-CSRF": "", "Origin": "https://evil.example"}).status_code == 403
    assert client.post(url, headers={"Origin": "https://evil.example"}).status_code == 403  # header present, wrong origin
    still = [a for a in client.get("/api/approvals").json() if a["id"] == pending["id"]]
    assert still[0]["status"] == "pending"
    assert client.post(url, headers={"Origin": "http://127.0.0.1:8787"}).status_code == 200


async def test_responses_forbid_framing(setup):
    client, _, _ = setup
    r = client.get("/api/health")
    assert r.headers["x-frame-options"] == "DENY" and "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert client.get("/api/approvals", headers={"Host": "evil.example"}).headers["x-frame-options"] == "DENY"


async def test_odd_query_values_do_not_crash(setup):
    client, _, _ = setup
    assert client.get("/api/events", params={"limit": "abc"}).status_code == 200
    assert len(client.get("/api/events", params={"limit": "-5"}).json()) == 1  # clamped to 1
    assert client.get("/api/events", params={"limit": "99999999999"}).status_code == 200


async def test_secrets_in_arguments_never_reach_the_log_or_the_dashboard(setup):
    client, guard, _ = setup
    await guard.handle("s1", "submit_otp", {"payment_id": "pay_x", "otp_string": "482913"})
    shown = json.dumps(client.get("/api/events").json())
    assert "482913" not in shown and "<redacted>" in shown
    assert all("482913" not in e.args_json for e in guard.audit.events())



async def test_approval_ids_are_not_guessable(setup):
    client, _, _ = setup
    [pending] = [a for a in client.get("/api/approvals").json() if a["status"] == "pending"]
    assert len(pending["id"]) == len("apr_") + 32  # 128 random bits
