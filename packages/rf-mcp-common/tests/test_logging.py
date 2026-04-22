"""Tests for structured logger + tool timer."""

from __future__ import annotations

import io
import json
import logging
import time

import pytest

from rf_mcp_common.logging import JsonFormatter, get_logger, tool_timer


def test_json_formatter_emits_valid_json() -> None:
    formatter = JsonFormatter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="x.py",
        lineno=1,
        msg="hello %s",
        args=("world",),
        exc_info=None,
    )
    out = formatter.format(record)
    parsed = json.loads(out)
    assert parsed["msg"] == "hello world"
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "test"


def test_get_logger_is_idempotent() -> None:
    a = get_logger("rf_mcp_common.test")
    b = get_logger("rf_mcp_common.test")
    assert a is b
    assert len(a.handlers) == 1


def test_tool_timer_records_runtime() -> None:
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("rf_mcp_common.timer_test")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    with tool_timer(logger, "fake_tool") as meta:
        time.sleep(0.01)
        meta["extra"] = "info"

    line = buf.getvalue().strip()
    parsed = json.loads(line)
    assert parsed["tool"] == "fake_tool"
    assert parsed["status"] == "ok"
    assert parsed["runtime_sec"] >= 0.01
    assert parsed["extra"] == "info"


def test_tool_timer_marks_error_on_exception() -> None:
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("rf_mcp_common.timer_err")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    with pytest.raises(RuntimeError), tool_timer(logger, "bad_tool"):
        raise RuntimeError("boom")

    parsed = json.loads(buf.getvalue().strip())
    assert parsed["status"] == "error"
    assert parsed["tool"] == "bad_tool"
