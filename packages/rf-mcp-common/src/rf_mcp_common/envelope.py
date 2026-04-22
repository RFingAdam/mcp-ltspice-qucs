"""Structured response envelope for every MCP tool in the suite.

Every tool returns the same shape so agents (and humans) can rely on a
predictable contract: ``status``, ``data``, ``warnings``, and
``metadata``.  When a tool fails, ``status="error"`` and ``error`` is
populated with a human-readable message; ``data`` is ``None``.
"""

from __future__ import annotations

import time
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    """Uniform tool response.

    Tools should never raise to the MCP transport layer — they catch
    their own exceptions and convert to ``error()`` envelopes so the
    agent can reason about the failure.
    """

    status: Literal["ok", "error"]
    data: T | None = None
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None

    model_config = {"arbitrary_types_allowed": True}


def ok(
    data: T,
    *,
    warnings: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    tool_version: str = "0.1.0",
    runtime_sec: float | None = None,
) -> Envelope[T]:
    """Build a successful envelope."""
    meta: dict[str, Any] = {"tool_version": tool_version}
    if runtime_sec is not None:
        meta["runtime_sec"] = round(runtime_sec, 4)
    if metadata:
        meta.update(metadata)
    return Envelope[T](
        status="ok",
        data=data,
        warnings=warnings or [],
        metadata=meta,
    )


def error(
    message: str,
    *,
    warnings: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    tool_version: str = "0.1.0",
) -> Envelope[None]:
    """Build a failure envelope with a human-readable message."""
    return Envelope[None](
        status="error",
        data=None,
        warnings=warnings or [],
        metadata={"tool_version": tool_version, **(metadata or {})},
        error=message,
    )


class Timer:
    """Tiny wall-clock timer for tool runtime metadata."""

    def __init__(self) -> None:
        self._start = time.perf_counter()

    def elapsed(self) -> float:
        return time.perf_counter() - self._start
