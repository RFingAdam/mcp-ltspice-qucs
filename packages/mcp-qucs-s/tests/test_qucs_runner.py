"""Tests for the Qucs-S runner detection logic."""

from __future__ import annotations

import json
import subprocess
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


def test_failed_qucs_run_does_not_accept_or_overwrite_stale_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    netlist = tmp_path / "filter.net"
    netlist.write_text("generated netlist", encoding="utf-8")
    output = tmp_path / "filter.dat"
    stale = "<Qucs Dataset 1.0.0>\nold successful data\n"
    output.write_text(stale, encoding="utf-8")

    monkeypatch.setattr("mcp_qucs_s.runner.find_qucs_s", lambda: Path("/fake/qucsator"))
    monkeypatch.setattr("mcp_qucs_s.runner.probe_executable_version", lambda *a, **k: "fake")

    def fail_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="intentional failure")

    monkeypatch.setattr("mcp_qucs_s.runner.run_process_tree", fail_run)

    with pytest.raises(RuntimeError, match="returncode=1"):
        run_qucs(netlist, output_path=output, workspace_root=tmp_path / "runs")

    assert output.read_text(encoding="utf-8") == stale
    manifest = json.loads(netlist.with_suffix(".qucs.manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "failed"
    assert manifest["returncode"] == 1
    assert not any(item["role"] == "published_output" for item in manifest["artifacts"])


def test_successful_qucs_run_publishes_fresh_validated_output_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    netlist = tmp_path / "filter.net"
    netlist.write_text("generated netlist", encoding="utf-8")
    output = tmp_path / "filter.dat"
    output.write_text("stale", encoding="utf-8")

    monkeypatch.setattr("mcp_qucs_s.runner.find_qucs_s", lambda: Path("/fake/qucsator"))
    monkeypatch.setattr(
        "mcp_qucs_s.runner.probe_executable_version", lambda *a, **k: "qucsator fake 1.0"
    )

    def successful_run(command, **kwargs):
        Path(command[4]).write_text(
            "<Qucs Dataset 1.0.0>\n<indep frequency 1>\n1e9\n</indep>\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="complete", stderr="")

    monkeypatch.setattr("mcp_qucs_s.runner.run_process_tree", successful_run)
    result = run_qucs(netlist, output_path=output, workspace_root=tmp_path / "runs")

    assert result.output_path == output
    assert "<Qucs Dataset" in output.read_text(encoding="utf-8")
    assert result.workspace_path.parent == (tmp_path / "runs").resolve()
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert manifest["backend"]["version"] == "qucsator fake 1.0"
    assert any(item["role"] == "published_output" for item in manifest["artifacts"])


def test_qucs_rejects_fresh_but_invalid_dataset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    netlist = tmp_path / "filter.net"
    netlist.write_text("generated netlist", encoding="utf-8")
    output = tmp_path / "filter.dat"
    output.write_text("stale", encoding="utf-8")

    monkeypatch.setattr("mcp_qucs_s.runner.find_qucs_s", lambda: Path("/fake/qucsator"))
    monkeypatch.setattr("mcp_qucs_s.runner.probe_executable_version", lambda *a, **k: "fake")

    def invalid_run(command, **kwargs):
        Path(command[4]).write_text("not a Qucs dataset", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("mcp_qucs_s.runner.run_process_tree", invalid_run)
    with pytest.raises(RuntimeError, match="not a recognized Qucs dataset"):
        run_qucs(netlist, output_path=output, workspace_root=tmp_path / "runs")
    assert output.read_text(encoding="utf-8") == "stale"


def test_default_qucs_results_remain_unique_across_repeated_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    netlist = tmp_path / "filter.net"
    netlist.write_text("generated netlist", encoding="utf-8")
    invocation = 0

    monkeypatch.setattr("mcp_qucs_s.runner.find_qucs_s", lambda: Path("/fake/qucsator"))
    monkeypatch.setattr("mcp_qucs_s.runner.probe_executable_version", lambda *a, **k: "fake")

    def successful_run(command, **kwargs):
        nonlocal invocation
        invocation += 1
        Path(command[4]).write_text(
            f"<Qucs Dataset 1.0.0>\nrun {invocation}\n",
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("mcp_qucs_s.runner.run_process_tree", successful_run)
    first = run_qucs(netlist, workspace_root=tmp_path / "runs")
    second = run_qucs(netlist, workspace_root=tmp_path / "runs")

    assert first.output_path != second.output_path
    assert first.manifest_path != second.manifest_path
    assert first.output_path.read_text(encoding="utf-8").endswith("run 1\n")
    assert second.output_path.read_text(encoding="utf-8").endswith("run 2\n")
