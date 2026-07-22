"""Tests for structured logger + tool timer."""

from __future__ import annotations

import io
import json
import logging
import sys
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


# ---------------------------------------------------------------------------
# RF_MCP_LOG_LEVEL
#
# get_logger() runs at module scope in all three servers, so anything that
# raises here is an import-time crash for the whole MCP server.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("DEBUG", logging.DEBUG),
        ("debug", logging.DEBUG),  # lowercase is the natural thing to type
        ("  Warning ", logging.WARNING),
        ("10", logging.DEBUG),  # numeric levels are valid for setLevel
    ],
)
def test_log_level_env_var_accepted(monkeypatch, value, expected) -> None:
    monkeypatch.setenv("RF_MCP_LOG_LEVEL", value)
    logger = get_logger(f"test_level_{value.strip().lower()}")
    assert logger.level == expected


def test_invalid_log_level_falls_back_instead_of_crashing(monkeypatch, capsys) -> None:
    """A bad level must not take the server down at import."""
    monkeypatch.setenv("RF_MCP_LOG_LEVEL", "verbose")
    logger = get_logger("test_level_invalid")
    assert logger.level == logging.INFO
    assert "not a valid log level" in capsys.readouterr().err


def test_unset_log_level_defaults_to_info(monkeypatch) -> None:
    monkeypatch.delenv("RF_MCP_LOG_LEVEL", raising=False)
    assert get_logger("test_level_unset").level == logging.INFO


def test_handler_writes_to_stderr_not_stdout() -> None:
    """stdout carries the MCP protocol stream; a log line there corrupts it."""
    logger = get_logger("test_stream_target")
    handler = next(h for h in logger.handlers if isinstance(h, logging.StreamHandler))
    assert handler.stream is sys.stderr
