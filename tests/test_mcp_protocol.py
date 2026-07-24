from __future__ import annotations

import asyncio
import json
import os
import queue
import subprocess
import sys
import threading
from typing import Any, TextIO

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from mcp_ltspice.server import mcp as ltspice
from mcp_qucs_s.server import mcp as qucs
from mcp_rf_analysis.server import mcp as rf_analysis


def test_in_memory_initialize_list_and_call_for_every_server() -> None:
    async def exercise() -> None:
        cases = [
            (ltspice, "list_vendor_parts", {"vendor": "coilcraft_0402hp"}),
            (qucs, "list_substrate_presets_tool", {}),
            (rf_analysis, "list_ism_bands_tool", {}),
        ]
        for server, name, arguments in cases:
            async with Client(server) as client:
                tools = await asyncio.wait_for(client.list_tools(), 5)
                assert name in {tool.name for tool in tools}
                result = await asyncio.wait_for(
                    client.call_tool(name, arguments),
                    5,
                )
                assert result.is_error is False
                assert result.data is not None

    asyncio.run(exercise())


def test_error_envelope_is_protocol_tool_error() -> None:
    async def exercise() -> None:
        async with Client(ltspice) as client:
            with pytest.raises(ToolError, match="Unknown vendor"):
                await asyncio.wait_for(
                    client.call_tool(
                        "list_vendor_parts",
                        {"vendor": "not-a-vendor"},
                    ),
                    5,
                )

    asyncio.run(exercise())


def _send(process: subprocess.Popen[str], message: dict[str, Any]) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
    process.stdin.flush()


def _stream_lines(stdout: TextIO, output_queue: queue.Queue[str | None]) -> None:
    """Forward stdout lines to a queue so reads can be timeout-bounded.

    `selectors`/`select.select` cannot register a subprocess pipe on native
    Windows (`select()` there only accepts sockets), so a background thread
    plus a queue is the portable way to bound a stdio read with a timeout.
    """
    try:
        for line in iter(stdout.readline, ""):
            output_queue.put(line)
    finally:
        output_queue.put(None)


def _receive(
    output_queue: queue.Queue[str | None],
    process: subprocess.Popen[str],
    *,
    timeout: float = 8.0,
) -> dict[str, Any]:
    try:
        line = output_queue.get(timeout=timeout)
    except queue.Empty as exc:
        raise TimeoutError("stdio MCP server produced no response") from exc
    if line is None:
        raise RuntimeError(f"stdio MCP server exited with {process.poll()}")
    value = json.loads(line)
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize(
    ("module", "server_name", "tool_name", "arguments"),
    [
        (
            "mcp_ltspice.server",
            "mcp-ltspice",
            "list_vendor_parts",
            {"vendor": "coilcraft_0402hp"},
        ),
        (
            "mcp_qucs_s.server",
            "mcp-qucs-s",
            "list_substrate_presets_tool",
            {},
        ),
        (
            "mcp_rf_analysis.server",
            "mcp-rf-analysis",
            "list_ism_bands_tool",
            {},
        ),
    ],
)
def test_real_stdio_initialize_list_and_call_round_trip(
    module: str,
    server_name: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> None:
    environment = os.environ.copy()
    environment["MPLCONFIGDIR"] = "/tmp/rf-mcp-matplotlib"
    environment["FASTMCP_SHOW_CLI_BANNER"] = "false"
    process = subprocess.Popen(
        [sys.executable, "-m", module],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        env=environment,
    )
    assert process.stdout is not None
    output_queue: queue.Queue[str | None] = queue.Queue()
    reader = threading.Thread(
        target=_stream_lines, args=(process.stdout, output_queue), daemon=True
    )
    reader.start()
    try:
        _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "contract-test", "version": "1"},
                },
            },
        )
        initialized = _receive(output_queue, process)
        assert initialized["result"]["serverInfo"]["name"] == server_name
        _send(
            process,
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
        )
        _send(
            process,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        listed = _receive(output_queue, process)
        assert listed["result"]["tools"]
        _send(
            process,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
        )
        called = _receive(output_queue, process)
        assert called["result"]["isError"] is False
    finally:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
