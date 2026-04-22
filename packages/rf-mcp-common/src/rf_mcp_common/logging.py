"""Structured JSON logging + per-tool-call timing context manager."""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


class JsonFormatter(logging.Formatter):
    """One JSON object per log line so logs are machine-parseable."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # Merge any extras attached via logger.info("msg", extra={...})
        for k, v in record.__dict__.items():
            if k in {
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process",
                "taskName", "message", "asctime",
            }:
                continue
            payload[k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def get_logger(name: str) -> logging.Logger:
    """Get a logger configured with the JSON formatter (idempotent)."""
    logger = logging.getLogger(name)
    if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        logger.addHandler(handler)
        logger.setLevel(os.environ.get("RF_MCP_LOG_LEVEL", "INFO"))
        logger.propagate = False
    return logger


@contextmanager
def tool_timer(logger: logging.Logger, tool_name: str) -> Iterator[dict[str, Any]]:
    """Time a tool invocation and record runtime + status in metadata.

    Use as::

        with tool_timer(logger, "synthesize_lc_filter") as meta:
            ... do work ...
            meta["custom_field"] = "x"
        # logger.info auto-emitted with runtime_sec and status

    The yielded dict can be read after the block to retrieve runtime.
    """
    meta: dict[str, Any] = {"tool": tool_name}
    start = time.perf_counter()
    status = "ok"
    try:
        yield meta
    except Exception:
        status = "error"
        raise
    finally:
        runtime = time.perf_counter() - start
        meta["runtime_sec"] = round(runtime, 4)
        meta["status"] = status
        logger.info(f"{tool_name} done", extra=meta)
