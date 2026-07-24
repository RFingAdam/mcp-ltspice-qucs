"""Error-envelope middleware contract."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import MiddlewareContext
from fastmcp.tools.base import ToolResult

from rf_mcp_common.tool_errors import EnvelopeErrorMiddleware


def test_error_envelope_is_promoted_to_typed_tool_error() -> None:
    middleware = EnvelopeErrorMiddleware()
    context = MiddlewareContext(
        message={"name": "demo", "arguments": {}},
        method="tools/call",
    )

    async def next_error(_context):
        return ToolResult(
            structured_content={
                "status": "error",
                "data": None,
                "warnings": [],
                "metadata": {},
                "error": "simulation timeout after 10 s",
            }
        )

    async def call():
        return await middleware.on_call_tool(context, next_error)

    with pytest.raises(ToolError) as raised:
        asyncio.run(call())
    payload = json.loads(str(raised.value))
    assert payload == {
        "code": "TIMEOUT",
        "message": "simulation timeout after 10 s",
    }


def test_success_envelope_remains_structured_result() -> None:
    middleware = EnvelopeErrorMiddleware()
    context = MiddlewareContext(
        message={"name": "demo", "arguments": {}},
        method="tools/call",
    )
    expected = ToolResult(
        structured_content={
            "status": "ok",
            "data": {"value": 1},
            "warnings": [],
            "metadata": {},
            "error": None,
        }
    )

    async def next_success(_context):
        return expected

    assert asyncio.run(middleware.on_call_tool(context, next_success)) is expected


def test_all_servers_install_error_middleware() -> None:
    from mcp_ltspice.server import mcp as ltspice
    from mcp_qucs_s.server import mcp as qucs
    from mcp_rf_analysis.server import mcp as rf_analysis

    for server in (ltspice, qucs, rf_analysis):
        assert any(isinstance(item, EnvelopeErrorMiddleware) for item in server.middleware)


def test_every_public_tool_has_safety_annotations() -> None:
    from mcp_ltspice.server import mcp as ltspice
    from mcp_qucs_s.server import mcp as qucs
    from mcp_rf_analysis.server import mcp as rf_analysis

    async def inspect() -> None:
        for server in (ltspice, qucs, rf_analysis):
            tools = await server.list_tools()
            assert tools
            for tool in tools:
                assert tool.annotations is not None, tool.name
                assert tool.annotations.destructiveHint is False, tool.name

    asyncio.run(inspect())
