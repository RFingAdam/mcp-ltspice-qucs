"""Protocol execution helpers for synchronous engineering functions."""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
from contextlib import asynccontextmanager
from typing import Any, cast

import anyio
from fastmcp import FastMCP
from fastmcp.server.context import reset_transport, set_transport
from mcp import types
from mcp.server.lowlevel.server import NotificationOptions
from mcp.shared.message import SessionMessage


def prepare_protocol_tools(server: FastMCP[Any]) -> None:
    """Expose registered synchronous tools as protocol-native coroutines.

    Direct Python functions remain synchronous for library users. Only the
    registered MCP callable is replaced. Expensive simulator and optimization
    work is routed through the bounded durable job manager by those tools.
    """
    provider = server._local_provider
    components = provider._components
    for component in components.values():
        mutable_component = cast(Any, component)
        function = getattr(mutable_component, "fn", None)
        if function is None or inspect.iscoroutinefunction(function):
            continue

        async def execute(
            *args: Any,
            __function: Any = function,
            **kwargs: Any,
        ) -> Any:
            return __function(*args, **kwargs)

        mutable_component.fn = execute


@asynccontextmanager
async def _asyncio_stdio_transport() -> Any:
    """MCP stdio streams without worker-thread file bridges.

    POSIX pipe readiness is integrated directly with the asyncio selector.
    This avoids worker-thread wakeup regressions in some CPython/AnyIO
    combinations. Windows uses FastMCP's native transport in the entry point.
    """
    read_sender, read_stream = anyio.create_memory_object_stream[SessionMessage | Exception](0)
    write_stream, write_receiver = anyio.create_memory_object_stream[SessionMessage](0)
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    reader_protocol = asyncio.StreamReaderProtocol(reader)
    read_transport, _ = await loop.connect_read_pipe(
        lambda: reader_protocol,
        sys.stdin.buffer,
    )

    async def stdin_reader() -> None:
        async with read_sender:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    message = types.JSONRPCMessage.model_validate_json(line)
                except Exception as exc:
                    await read_sender.send(exc)
                else:
                    await read_sender.send(SessionMessage(message))

    async def stdout_writer() -> None:
        async with write_receiver:
            async for message in write_receiver:
                encoded = message.message.model_dump_json(
                    by_alias=True,
                    exclude_none=True,
                )
                payload = (encoded + "\n").encode("utf-8")
                written = 0
                output_fd = sys.stdout.fileno()
                while written < len(payload):
                    written += os.write(output_fd, payload[written:])

    try:
        async with anyio.create_task_group() as task_group:
            task_group.start_soon(stdin_reader)
            task_group.start_soon(stdout_writer)
            yield read_stream, write_stream
    finally:
        read_transport.close()


async def run_stdio_server_async(server: FastMCP[Any]) -> None:
    """Run a prepared FastMCP server over the robust stdio transport."""
    token = set_transport("stdio")
    try:
        async with (
            server._lifespan_manager(),
            _asyncio_stdio_transport() as (read_stream, write_stream),
        ):
            await server._mcp_server.run(
                read_stream,
                write_stream,
                server._mcp_server.create_initialization_options(
                    notification_options=NotificationOptions(
                        tools_changed=True,
                    ),
                ),
            )
    finally:
        reset_transport(token)


def run_stdio_server(server: FastMCP[Any]) -> None:
    """Synchronous console entry point for :func:`run_stdio_server_async`."""
    if os.name == "nt":  # pragma: no cover - exercised by Windows CI
        server.run()
    else:
        asyncio.run(run_stdio_server_async(server))
