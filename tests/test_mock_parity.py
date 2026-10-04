"""Day 1 "done when": fetch_payment returns the same shape from the real and mock servers.

Needs Docker, the razorpay-mcp:main image and real rzp_test_ keys in .env, with at least one
test payment in the account. Run with: uv run pytest -m live
"""

import json
import os
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from scripts.probe_razorpay import has_real_keys, load_env

pytestmark = pytest.mark.live
ENV = load_env()


def _shape(value):
    """Key names and value types, recursively; values themselves are ignored."""
    if isinstance(value, dict):
        return {k: _shape(v) for k, v in value.items()}
    return type(value).__name__


async def _first_payment(params: StdioServerParameters, method: str | None = None) -> dict:
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        listing = json.loads((await session.call_tool("fetch_all_payments", {"count": 10})).content[0].text)
        items = [p for p in listing["items"] if method is None or p["method"] == method]
        if not items:
            pytest.skip(f"no {method or ''} payments to compare")
        result = await session.call_tool("fetch_payment", {"payment_id": items[0]["id"]})
        return json.loads(result.content[0].text)


@pytest.mark.skipif(not has_real_keys(ENV), reason="needs real rzp_test_ keys in .env")
async def test_fetch_payment_shape_matches_real_server():
    real_params = StdioServerParameters(
        command="docker",
        args=["run", "--rm", "-i", "-e", "RAZORPAY_KEY_ID", "-e", "RAZORPAY_KEY_SECRET", "razorpay-mcp:main"],
        env={**os.environ, "RAZORPAY_KEY_ID": ENV["RAZORPAY_KEY_ID"], "RAZORPAY_KEY_SECRET": ENV["RAZORPAY_KEY_SECRET"]},
    )
    real = await _first_payment(real_params)
    mock = await _first_payment(
        StdioServerParameters(command=sys.executable, args=["-m", "mock", "--merchant", "demo-fashion"]),
        method=real["method"],
    )
    missing, extra = set(real) - set(mock), set(mock) - set(real)
    assert not missing and not extra, f"mock is missing {sorted(missing)}, has extra {sorted(extra)}"
    # Nullable fields legitimately differ by status; compare the ones present in both as non-null.
    both = [k for k in real if real[k] is not None and mock[k] is not None]
    mismatched = {k: (_shape(real[k]), _shape(mock[k])) for k in both if _shape(real[k]) != _shape(mock[k])}
    assert not mismatched, mismatched
