"""python -m mock --merchant demo-fashion [--ledger path] [--overrides scenario.json]"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import anyio
from mcp.server.stdio import stdio_server

from mock.server import build_server
from mock.store import Store


async def serve(store: Store) -> None:
    server = build_server(store)
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    parser = argparse.ArgumentParser(prog="mock", description="Mock Razorpay MCP server over stdio.")
    parser.add_argument("--merchant", default="demo-fashion")
    parser.add_argument("--data", type=Path, default=Path("data/generated"))
    parser.add_argument("--ledger", default=":memory:", help="SQLite path for the write ledger")
    parser.add_argument("--overrides", type=Path, help="JSON {payment_id: {field: value}} applied on load")
    args = parser.parse_args()
    overrides = json.loads(args.overrides.read_text(encoding="utf-8")) if args.overrides else None
    anyio.run(serve, Store(args.data, args.merchant, args.ledger, overrides))


if __name__ == "__main__":
    main()
