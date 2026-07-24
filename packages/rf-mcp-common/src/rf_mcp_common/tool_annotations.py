"""Conservative MCP behavior annotations shared by public tools."""

from __future__ import annotations

from typing import Any

# Tools may create workspace artifacts or consult installed executables. The
# suite therefore uses conservative defaults instead of falsely advertising
# every calculation as pure/read-only.
DEFAULT_TOOL_ANNOTATIONS: dict[str, Any] = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}

READ_ONLY_TOOL_ANNOTATIONS: dict[str, Any] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
