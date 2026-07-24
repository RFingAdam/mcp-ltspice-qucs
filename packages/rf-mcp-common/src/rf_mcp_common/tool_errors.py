"""Convert legacy error envelopes into real MCP tool execution errors."""

from __future__ import annotations

import json
from typing import Any

from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult


def classify_tool_error(message: str) -> str:
    """Map a human diagnostic to a stable coarse error code."""
    lowered = message.lower()
    if any(token in lowered for token in ("timeout", "timed out", "within ")):
        return "TIMEOUT"
    if any(
        token in lowered
        for token in (
            "not installed",
            "not found on path",
            "simulator found",
            "sandbox is unavailable",
            "unavailable",
        )
    ):
        return "CAPABILITY_UNAVAILABLE"
    if any(
        token in lowered
        for token in (
            "did not produce",
            "corrupt",
            "invalid artifact",
            "parse",
            "decode",
        )
    ):
        return "ARTIFACT_INVALID"
    if any(
        token in lowered
        for token in (
            "must ",
            "requires ",
            "invalid",
            "unsupported",
            "outside",
            "unknown ",
            "bad ",
            "no component",
            "not found:",
        )
    ):
        return "INVALID_INPUT"
    return "TOOL_EXECUTION_FAILED"


class EnvelopeErrorMiddleware(Middleware):
    """Raise ``ToolError`` for ``Envelope(status='error')`` over MCP."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[Any],
        call_next: CallNext[Any, ToolResult],
    ) -> ToolResult:
        result = await call_next(context)
        structured = result.structured_content
        if isinstance(structured, dict) and structured.get("status") == "error":
            message = str(structured.get("error") or "tool execution failed")
            raise ToolError(
                json.dumps(
                    {
                        "code": classify_tool_error(message),
                        "message": message,
                    },
                    separators=(",", ":"),
                )
            )
        return result
