"""In-memory merchant state for the mock server, plus a SQLite ledger of every write call.

The ledger is how evals score a run: it records what each write did to money, independent of
what the agent said it did.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

from datagen.generate import START
from datagen.models import Customer, Payment, Refund, Settlement

LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts INTEGER NOT NULL,
    tool TEXT NOT NULL,
    args_json TEXT NOT NULL,
    ok INTEGER NOT NULL,
    error TEXT,
    resource_id TEXT,
    paise_moved INTEGER NOT NULL DEFAULT 0,
    recipient TEXT
)
"""


def _read_jsonl(path: Path, model):
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [model.model_validate_json(line) for line in f if line.strip()]


class Store:
    def __init__(self, data_dir: Path, merchant: str, ledger_path: Path | str = ":memory:",
                 overrides: dict[str, dict[str, Any]] | None = None) -> None:
        self.data_dir = Path(data_dir)
        self.merchant = merchant
        self.overrides = overrides or {}
        self.ledger = sqlite3.connect(str(ledger_path), check_same_thread=False)
        self.ledger.execute(LEDGER_SCHEMA)
        self.reset()

    # ---- lifecycle -----------------------------------------------------------------------

    def reset(self) -> None:
        d = self.data_dir / self.merchant
        self.payments: dict[str, Payment] = {p.id: p for p in _read_jsonl(d / "payments.jsonl", Payment)}
        self.refunds: dict[str, Refund] = {r.id: r for r in _read_jsonl(d / "refunds.jsonl", Refund)}
        self.settlements: dict[str, Settlement] = {
            s.id: s for s in _read_jsonl(d / "settlements.jsonl", Settlement)}
        self.customers: dict[str, Customer] = {c.id: c for c in _read_jsonl(d / "customers.jsonl", Customer)}
        if not self.payments:
            raise FileNotFoundError(f"no payments in {d}; run `python -m datagen` first")
        self.refund_notes: dict[str, dict[str, str]] = {}
        self.payment_links: dict[str, dict[str, Any]] = {}
        self.instant_settlements: dict[str, dict[str, Any]] = {}
        self.orders: dict[str, list[Payment]] = defaultdict(list)
        for p in self.payments.values():
            self.orders[p.order_id].append(p)
        for payment_id, fields in self.overrides.items():
            if payment_id in self.payments:
                self.payments[payment_id] = self.payments[payment_id].model_copy(update=fields)
        last = max(p.created_at for p in self.payments.values())
        # "Now" for the mock is just after the data window, so "this week" means its last 7 days.
        self.now = max(last + 1, int((START + timedelta(days=30)).timestamp()))
        self.ledger.execute("DELETE FROM ledger")
        self.ledger.commit()

    def tick(self) -> int:
        self.now += 1
        return self.now

    # ---- queries -------------------------------------------------------------------------

    def payments_sorted(self, frm: int | None, to: int | None) -> list[Payment]:
        rows = [p for p in self.payments.values()
                if (frm is None or p.created_at >= frm) and (to is None or p.created_at <= to)]
        return sorted(rows, key=lambda p: p.created_at, reverse=True)

    def refunds_for(self, payment_id: str) -> list[Refund]:
        return sorted((r for r in self.refunds.values() if r.payment_id == payment_id),
                      key=lambda r: r.created_at, reverse=True)

    # ---- ledger --------------------------------------------------------------------------

    def record(self, tool: str, args: dict[str, Any], *, ok: bool, error: str | None = None,
               resource_id: str | None = None, paise_moved: int = 0, recipient: str | None = None) -> None:
        self.ledger.execute(
            "INSERT INTO ledger (ts, tool, args_json, ok, error, resource_id, paise_moved, recipient)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (self.now, tool, json.dumps(args, sort_keys=True), int(ok), error, resource_id,
             paise_moved, recipient),
        )
        self.ledger.commit()

    def ledger_rows(self) -> list[dict[str, Any]]:
        cur = self.ledger.execute("SELECT * FROM ledger ORDER BY seq")
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]

