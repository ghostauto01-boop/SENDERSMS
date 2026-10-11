"""Call an MCP tool the way an assistant does (dispatcher -> in-process app), return its envelope."""

from __future__ import annotations

import json


class _Stub:
    """The audit row the dispatcher writes; the tests do not need it."""

    def add(self, row):
        pass

    async def flush(self):
        pass


async def call_tool(name: str, **args) -> dict:
    """The tool's response envelope: ``ok``, ``http_status``, ``data`` ... (see mcp/response.py)."""
    from app.mcp import server
    from app.mcp.registry import TOOLS_BY_NAME

    server.CURRENT_ADMIN_ID.set(1)
    outcome = await server.run_tool(
        _Stub(), server.TokenView(id=1, name="t", scope="write", prefix="mcp_t"),
        TOOLS_BY_NAME[name], args,
    )
    return json.loads(outcome.result["content"][0]["text"])
