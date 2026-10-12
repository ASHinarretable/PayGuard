"""Where Doctor gets payments from: straight from generated data (fast, offline), or through an MCP
session (PayGuard or the provider), paging fetch_all_payments like any agent would."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from mcp import ClientSession

from datagen.models import Payment
from mock.serializers import razorpay as rz
from payguard.core.action import PaymentRecord

UNTRUSTED_TAG = re.compile(r'<untrusted id="u_\d+">(.*?)</untrusted>', re.DOTALL)
PAGE = 100


def unwrap(node: Any) -> Any:
    """Strips PayGuard's <untrusted> labels. Doctor reads customer text as data, so it needs the values."""
    if isinstance(node, str):
        return UNTRUSTED_TAG.sub(r"\1", node)
    if isinstance(node, dict):
        return {k: unwrap(v) for k, v in node.items()}
    if isinstance(node, list):
        return [unwrap(v) for v in node]
    return node


def from_generated(data_dir: Path, merchant: str, adapter) -> list[PaymentRecord]:
    path = Path(data_dir) / merchant / "payments.jsonl"
    with path.open(encoding="utf-8") as f:
        return [adapter.payment_record(rz.payment(Payment.model_validate_json(line))) for line in f if line.strip()]


async def from_session(session: ClientSession, adapter, frm: int | None = None, to: int | None = None) -> list[PaymentRecord]:
    records: list[PaymentRecord] = []
    skip = 0
    while True:
        args: dict[str, Any] = {"count": PAGE, "skip": skip}
        if frm is not None:
            args["from"] = frm
        if to is not None:
            args["to"] = to
        result = await session.call_tool("fetch_all_payments", args)
        if result.is_error:
            raise RuntimeError(f"fetch_all_payments failed: {result.content[0].text}")
        items = unwrap(json.loads(result.content[0].text)).get("items", [])
        if not items:
            return records
        records.extend(adapter.payment_record(p) for p in items)
        skip += len(items)
