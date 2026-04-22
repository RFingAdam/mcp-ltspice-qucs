"""Tests for the Qucs-S runner detection logic."""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_qucs_s.runner import (
    find_qucs_s,
    find_xyce,
    is_qucs_available,
    is_xyce_available,
    run_qucs,
)


def test_finder_returns_path_or_none() -> None:
    assert find_qucs_s() is None or isinstance(find_qucs_s(), Path)
    assert find_xyce() is None or isinstance(find_xyce(), Path)


def test_availability_helpers_match_finders() -> None:
    assert is_qucs_available() == (find_qucs_s() is not None)
    assert is_xyce_available() == (find_xyce() is not None)


def test_run_qucs_with_missing_schematic_raises(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        run_qucs(tmp_path / "nope.sch")


def test_run_qucs_without_qucs_installed_raises(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("mcp_qucs_s.runner.find_qucs_s", lambda: None)
    sch = tmp_path / "fake.sch"
    sch.write_text("placeholder")
    with pytest.raises(RuntimeError, match="Qucs-S"):
        run_qucs(sch)
