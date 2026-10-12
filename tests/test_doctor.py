"""Payment Doctor: detectors against the generated ground truth, and the report's guard rails."""

import json

import pytest

from doctor import sources
from doctor.narrate import check_numbers, facts, narrate, template
from doctor.report import build, markdown
from doctor.score import passed, score
from payguard.adapters.razorpay import RazorpayAdapter
from tests.conftest import MERCHANT


@pytest.fixture(scope="module")
def report(data_dir):
    return build(MERCHANT, sources.from_generated(data_dir, MERCHANT, RazorpayAdapter()))


@pytest.fixture(scope="module")
def truth(data_dir):
    return json.loads((data_dir / "ground_truth.json").read_text(encoding="utf-8"))


def test_report_matches_ground_truth(report, truth):
    s = score(report, truth)
    assert passed(s), s
    assert s["config_gaps"] == [{"reason": "upi_intent_not_enabled", "found": True}]


def test_top_fix_is_the_config_gap_or_carts(report):
    titles = [f.title for f in report.top_fixes]
    assert any("UPI Intent" in t for t in titles)
    assert report.top_fixes == sorted(report.top_fixes, key=lambda f: -f.impact_paise)


def test_lost_money_adds_up(report):
    assert report.lost_paise == sum(b["lost_paise"] for b in report.buckets)


def test_recovery_links_skip_malformed_emails(report):
    for a in report.actions:
        if a.kind == "recovery_link":
            email = a.calls[0]["args"].get("customer_email")
            assert email is None or (";" not in email and " " not in email)


def test_proposed_refunds_are_full_duplicates(report):
    dup_seconds = {d["second"]: d["amount"] for d in report.duplicates}
    for a in report.actions:
        if a.kind == "refund_duplicate":
            assert dup_seconds[a.payment_id] == a.amount == a.calls[0]["args"]["amount"]


def test_invented_numbers_are_flagged(report):
    f = facts(report)
    real = f["lost_total"]
    assert check_numbers(f"You lost {real} this month.", f) == []
    assert check_numbers("You lost ₹9,99,999 this month.", f) == ["₹9,99,999 is not in the computed facts"]


async def test_template_narrative_without_a_key(report, monkeypatch):
    monkeypatch.setenv("DOCTOR_MODEL", "gemini/gemini-flash-latest")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    r = await narrate(report.model_copy(deep=True))
    assert r.narrative_source == "template" and r.narrative == template(report)


def test_markdown_renders(report):
    md = markdown(report)
    assert md.startswith(f"# Payment Doctor: {MERCHANT}") and "## Top fixes" in md


def test_unwrap_strips_payguard_labels():
    data = {"email": '<untrusted id="u_3">a@b.in</untrusted>', "notes": {"x": '<untrusted id="u_9">hi</untrusted>'}}
    assert sources.unwrap(data) == {"email": "a@b.in", "notes": {"x": "hi"}}
