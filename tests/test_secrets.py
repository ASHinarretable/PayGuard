"""PayGuard never holds the provider's secret, and its child processes never get its environment."""

import argparse

from payguard.cli import _provider_key_id, _upstream
from payguard.env import read_env_value


def args(tmp_path, **kw):
    base = {"upstream": "docker", "image": "razorpay-mcp:main", "provider_env": str(tmp_path / ".env.razorpay"),
            "merchant": "demo-fashion", "data": "data/generated", "ledger": None}
    return argparse.Namespace(**{**base, **kw})


def test_env_file_goes_to_docker_and_secret_stays_out(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_should_not_leak")
    f = tmp_path / ".env.razorpay"
    f.write_text("RAZORPAY_KEY_ID=rzp_test_abc\nRAZORPAY_KEY_SECRET=topsecret\n", encoding="utf-8")
    p = _upstream(args(tmp_path), env={})
    assert "--env-file" in p.args and str(f.resolve()) in p.args
    assert "topsecret" not in str(p) and "RAZORPAY_KEY_SECRET" not in (p.env or {})
    assert "GROQ_API_KEY" not in (p.env or {})  # minimal environment for the Docker client
    assert _provider_key_id(args(tmp_path), env={}) == "rzp_test_abc"


def test_fallback_passes_only_the_two_razorpay_keys(tmp_path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "should_not_leak")
    p = _upstream(args(tmp_path), env={"RAZORPAY_KEY_ID": "rzp_test_x", "RAZORPAY_KEY_SECRET": "s", "GROQ_API_KEY": "g"})
    assert p.env["RAZORPAY_KEY_ID"] == "rzp_test_x" and "GROQ_API_KEY" not in p.env and "GEMINI_API_KEY" not in p.env


def test_read_env_value_reads_one_line(tmp_path):
    f = tmp_path / "e"
    f.write_text("# RAZORPAY_KEY_ID=commented\nA=1\nRAZORPAY_KEY_ID = rzp_live_9\n", encoding="utf-8")
    assert read_env_value(f, "RAZORPAY_KEY_ID") == "rzp_live_9" and read_env_value(f, "MISSING") is None
