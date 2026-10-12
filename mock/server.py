"""Mock payment-provider MCP server.

Serves the provider's real tool list (captured by scripts/probe_razorpay.py) over stdio, answers
from generated merchant data, and writes every write-tool call to the ledger.

    uv run python -m mock --merchant demo-fashion --ledger runs/ledger.db
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server

from mock.razorpay_tools import HANDLERS, ToolError
from mock.store import Store

TOOLS_FIXTURE = Path("tests/adapters/fixtures/razorpay/tools.json")
TIMEOUT_TEXT = "request failed: Post \"https://api.razorpay.com/v1/...\": context deadline exceeded (Client.Timeout exceeded)"


def load_tools(path: Path = TOOLS_FIXTURE) -> tuple[dict[str, Any], list[types.Tool]]:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    return fixture, [types.Tool.model_validate(t) for t in fixture["tools"]]


def _is_write(tool: types.Tool) -> bool:
    return not (tool.annotations and tool.annotations.read_only_hint)


def _effects(store: Store, name: str, args: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """What a successful write did, in provider-neutral terms, for the ledger."""
    if name == "create_refund":
        return {"resource_id": result["payment_id"], "paise_moved": result["amount"]}
    if name in ("create_payment_link", "payment_link_upi_create"):
        customer = result.get("customer", {})
        recipient = ",".join(v for v in (customer.get("email"), customer.get("contact")) if v)
        return {"resource_id": result["id"], "recipient": recipient or None}
    if name == "payment_link_notify":
        link = store.payment_links[args["payment_link_id"]]
        customer = link.get("customer", {})
        field = "email" if args.get("medium") == "email" else "contact"
        return {"resource_id": link["id"], "recipient": customer.get(field)}
    if name == "create_instant_settlement":
        return {"resource_id": result["id"]}
    return {"resource_id": args.get("payment_id") or args.get("order_id") or args.get("refund_id")}


def call(store: Store, tools: dict[str, types.Tool], name: str, args: dict[str, Any]) -> types.CallToolResult:
    """Runs one tool call against the store. Separate from the MCP plumbing so tests can call it."""
    tool = tools.get(name)
    if tool is None:
        return types.CallToolResult(content=[types.TextContent(type="text", text=f"tool not found: {name}")],
                                    is_error=True)
    handler = HANDLERS.get(name)
    try:
        if handler is None:
            raise ToolError(f"{name} is not implemented by the mock server")
        result = handler(store, args)
    except ToolError as exc:
        if _is_write(tool):
            store.record(name, args, ok=False, error=str(exc))
        return types.CallToolResult(content=[types.TextContent(type="text", text=str(exc))], is_error=True)
    if _is_write(tool):
        store.record(name, args, ok=True, **_effects(store, name, args, result))
    if store.faults.get(name, 0) > 0:  # the call went through, but the caller never hears so
        store.faults[name] -= 1
        return types.CallToolResult(content=[types.TextContent(type="text", text=TIMEOUT_TEXT)], is_error=True)
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(result))])


def build_server(store: Store, tools_path: Path = TOOLS_FIXTURE) -> Server:
    fixture, tool_list = load_tools(tools_path)
    by_name = {t.name: t for t in tool_list}
    server_info = fixture.get("server", {})

    async def on_list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tool_list)

    async def on_call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        return call(store, by_name, params.name, dict(params.arguments or {}))

    return Server(server_info.get("name", "razorpay-mcp-server"), version=server_info.get("version", "1.0.0"),
                  on_list_tools=on_list_tools, on_call_tool=on_call_tool)

