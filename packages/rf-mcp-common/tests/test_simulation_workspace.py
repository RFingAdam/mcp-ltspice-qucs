"""Tests for isolated simulator workspaces and provenance manifests."""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

from rf_mcp_common.simulation_workspace import (
    ProcessCancelledError,
    SimulationWorkspace,
    probe_executable_version,
    run_process_tree,
    sha256_file,
    subprocess_environment,
)


def test_workspace_records_complete_run_and_publishes_atomically(tmp_path: Path) -> None:
    source = tmp_path / "source.net"
    source.write_text("R1 in out 50\n", encoding="utf-8")
    workspace = SimulationWorkspace.create("fake-simulator", parent=tmp_path / "runs")

    snap = workspace.snapshot_input(source)
    env = {"PATH": "/bin", "HOME": str(tmp_path)}
    workspace.start(
        ["/bin/fake", str(snap)],
        cwd=workspace.root,
        environment=env,
        executable="/bin/fake",
        backend_version="fake 1.0",
    )
    workspace.write_streams("ok\n", "")
    staged = workspace.output_path("result.dat")
    staged.write_text("fresh result\n", encoding="utf-8")
    workspace.record_artifact(staged, role="simulator_output")

    published = tmp_path / "published.dat"
    published.write_text("stale result\n", encoding="utf-8")
    workspace.publish(staged, published, role="published_output")
    workspace.complete(returncode=0)

    manifest = json.loads(workspace.manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "completed"
    assert manifest["returncode"] == 0
    assert manifest["backend"]["version"] == "fake 1.0"
    assert manifest["environment"] == {"policy": "allowlist", "keys": ["HOME", "PATH"]}
    assert manifest["inputs"][0]["sha256"] == sha256_file(source)
    assert published.read_text(encoding="utf-8") == "fresh result\n"
    assert any(item["role"] == "published_output" for item in manifest["artifacts"])


def test_failed_workspace_keeps_diagnostics(tmp_path: Path) -> None:
    workspace = SimulationWorkspace.create("fake", parent=tmp_path)
    workspace.start(
        ["/bin/false"],
        cwd=workspace.root,
        environment={},
        executable="/bin/false",
        backend_version=None,
    )
    workspace.write_streams("", "failed\n")
    workspace.fail("simulator exited 1", returncode=1)

    manifest = workspace.manifest
    assert manifest["status"] == "failed"
    assert manifest["returncode"] == 1
    assert manifest["error"] == "simulator exited 1"
    assert manifest["completed_at"] is not None
    assert (workspace.root / "logs" / "stderr.txt").read_text(encoding="utf-8") == "failed\n"


@pytest.mark.parametrize("name", ["../escape.dat", "nested/output.dat", "/tmp/escape.dat"])
def test_workspace_rejects_non_basename_artifacts(tmp_path: Path, name: str) -> None:
    workspace = SimulationWorkspace.create("fake", parent=tmp_path)
    with pytest.raises(ValueError, match="basename"):
        workspace.output_path(name)


def test_subprocess_environment_is_allowlisted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", "/bin")
    monkeypatch.setenv("HOME", "/home/test")
    monkeypatch.setenv("SECRET_TOKEN", "do-not-leak")
    monkeypatch.setenv("BACKEND_SETTING", "allowed")
    env = subprocess_environment({"BACKEND_SETTING"})
    assert env["PATH"] == "/bin"
    assert env["BACKEND_SETTING"] == "allowed"
    assert "SECRET_TOKEN" not in env


@pytest.mark.skipif(not Path("/bin/sh").is_file(), reason="requires a POSIX shell")
def test_probe_executable_version_reads_first_line(tmp_path: Path) -> None:
    executable = tmp_path / "fake-simulator"
    executable.write_text("#!/bin/sh\nprintf 'fake 2.0\\nextra\\n'\n", encoding="utf-8")
    executable.chmod(0o755)
    assert probe_executable_version(executable, environment={}) == "fake 2.0"


@pytest.mark.skipif(not Path("/bin/sh").is_file(), reason="requires a POSIX shell")
def test_probe_executable_version_skips_banner_decoration(tmp_path: Path) -> None:
    executable = tmp_path / "decorated-simulator"
    executable.write_text(
        "#!/bin/sh\nprintf '\\n******\\n** simulator 4.2\\n******\\n'\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    assert probe_executable_version(executable, environment={}) == "simulator 4.2"


def test_snapshot_simulation_tree_preserves_relative_includes(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    models = source_dir / "models"
    models.mkdir(parents=True)
    model = models / "part.lib"
    model.write_text(".subckt PART p n\nR1 p n 50\n.ends PART\n", encoding="utf-8")
    main = source_dir / "main.cir"
    main.write_text('.include "models/part.lib"\nX1 in 0 PART\n', encoding="utf-8")
    workspace = SimulationWorkspace.create("spice", parent=tmp_path / "runs")

    snapshot = workspace.snapshot_simulation_tree(main)
    assert snapshot.read_bytes() == main.read_bytes()
    assert (workspace.root / "inputs" / "models" / "part.lib").read_bytes() == model.read_bytes()
    assert [record["role"] for record in workspace.manifest["inputs"]] == [
        "simulation_input",
        "simulation_dependency",
    ]


@pytest.mark.parametrize(
    "include_line",
    [
        '.include "../sentinel.lib"',
        '.include "/tmp/sentinel.lib"',
        '.include "{MODEL_ROOT}/part.lib"',
    ],
)
def test_snapshot_simulation_tree_rejects_include_escape(tmp_path: Path, include_line: str) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (tmp_path / "sentinel.lib").write_text("secret\n", encoding="utf-8")
    main = source_dir / "main.cir"
    main.write_text(include_line + "\n", encoding="utf-8")
    workspace = SimulationWorkspace.create("spice", parent=tmp_path / "runs")
    with pytest.raises(ValueError, match=r"include|escapes"):
        workspace.snapshot_simulation_tree(main)


def test_snapshot_simulation_tree_rejects_symlink_escape(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    sentinel = tmp_path / "sentinel.lib"
    sentinel.write_text("secret\n", encoding="utf-8")
    (source_dir / "linked.lib").symlink_to(sentinel)
    main = source_dir / "main.cir"
    main.write_text('.include "linked.lib"\n', encoding="utf-8")
    workspace = SimulationWorkspace.create("spice", parent=tmp_path / "runs")
    with pytest.raises(ValueError, match="escapes allowed root"):
        workspace.snapshot_simulation_tree(main)


@pytest.mark.skipif(not Path("/bin/sh").is_file(), reason="requires a POSIX shell")
def test_timeout_terminates_descendant_processes(tmp_path: Path) -> None:
    marker = tmp_path / "descendant-survived"
    command = [
        "/bin/sh",
        "-c",
        f"(sleep 0.35; touch '{marker}') & wait",
    ]

    with pytest.raises(subprocess.TimeoutExpired):
        run_process_tree(command, timeout_sec=0.05)

    time.sleep(0.45)
    assert not marker.exists()


@pytest.mark.skipif(not Path("/bin/sh").is_file(), reason="requires a POSIX shell")
def test_cancellation_terminates_process_tree() -> None:
    cancelled = threading.Event()
    timer = threading.Timer(0.05, cancelled.set)
    timer.start()
    try:
        with pytest.raises(ProcessCancelledError, match="cancelled"):
            run_process_tree(
                ["/bin/sh", "-c", "sleep 30 & wait"],
                timeout_sec=5.0,
                cancel_requested=cancelled.is_set,
            )
    finally:
        timer.cancel()
