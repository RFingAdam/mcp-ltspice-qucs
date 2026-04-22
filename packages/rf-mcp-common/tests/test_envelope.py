"""Tests for the response envelope contract."""

from __future__ import annotations

import json

from rf_mcp_common.envelope import Envelope, error, ok


def test_ok_envelope_contains_status_and_data() -> None:
    env = ok({"value": 42}, metadata={"foo": "bar"})
    assert env.status == "ok"
    assert env.data == {"value": 42}
    assert env.metadata["foo"] == "bar"
    assert env.metadata["tool_version"] == "0.1.0"
    assert env.error is None
    assert env.warnings == []


def test_ok_envelope_records_runtime_when_provided() -> None:
    env = ok("payload", runtime_sec=1.234567)
    assert env.metadata["runtime_sec"] == 1.2346  # rounded


def test_error_envelope_carries_message() -> None:
    env = error("simulator missing")
    assert env.status == "error"
    assert env.data is None
    assert env.error == "simulator missing"


def test_envelope_round_trips_through_json() -> None:
    env = ok([1, 2, 3], warnings=["careful"], runtime_sec=0.5)
    raw = env.model_dump_json()
    parsed = json.loads(raw)
    rebuilt = Envelope[list].model_validate(parsed)
    assert rebuilt.data == [1, 2, 3]
    assert rebuilt.warnings == ["careful"]
    assert rebuilt.metadata["runtime_sec"] == 0.5
