"""PayGuard as an MCP proxy: an MCP server to the agent, an MCP client to the provider.

The agent sees the provider's own tool list, unchanged. Every call goes through the Guard; only
allowed calls reach the provider.
"""

from __future__ import annotations

from typing import Any

from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from payguard.adapters import ADAPTERS
from payguard.core.audit import AuditLog
from payguard.core.guard import Guard, ToolOutcome
from payguard.core.policy import Policy

INSTRUCTIONS = (
    "These payment tools are guarded by PayGuard. Calls that are unsafe are refused with DENIED, and "
    "risky ones return PENDING_APPROVAL until a human approves them. Do not retry refused calls."
)


class McpUpstream:
    """Upstream over an MCP client session."""

    def __init__(self, session: ClientSession) -> None:
        self.session = session

    async def call(self, tool: str, args: dict[str, Any]) -> ToolOutcome:
        result = await self.session.call_tool(tool, args)
        text = "\n".join(c.text for c in result.content if isinstance(c, types.TextContent))
        return ToolOutcome(is_error=bool(result.is_error), text=text, raw=result)


def build_proxy_server(guard: Guard, tools: list[types.Tool], session_id: str) -> Server:
    async def on_list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        outcome = await guard.handle(session_id, params.name, dict(params.arguments or {}))
        if isinstance(outcome.raw, types.CallToolResult):
            return outcome.raw
        return types.CallToolResult(content=[types.TextContent(type="text", text=outcome.text)],
                                    is_error=outcome.is_error)

    return Server("payguard", version="0.1.0", instructions=INSTRUCTIONS,
                  on_list_tools=on_list_tools, on_call_tool=on_call_tool)


async def run_stdio_proxy(*, upstream: StdioServerParameters, adapter: str, policy: Policy, db_url: str,
                          session_id: str, key_id: str | None = None) -> None:
    async with stdio_client(upstream) as (read, write), ClientSession(read, write) as up_session:
        await up_session.initialize()
        tools = (await up_session.list_tools()).tools
        up = McpUpstream(up_session)
        guard = Guard(ADAPTERS[adapter](upstream=up, key_id=key_id), up, policy, AuditLog(db_url))
        server = build_proxy_server(guard, tools, session_id)
        async with stdio_server() as (s_read, s_write):
            await server.run(s_read, s_write, server.create_initialization_options())
