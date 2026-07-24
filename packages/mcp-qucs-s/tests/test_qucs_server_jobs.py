"""Durable simulation job pipeline: workspace -> simulation_submit -> job_get.

mcp-qucs-s previously had QucsatorAdapter/XyceAdapter fully implemented but
with zero callers -- no simulate-through-IR path, no circuit_validate, no
job manager. These tests drive the wired pipeline end to end without a real
simulator installed, by monkeypatching QucsatorAdapter.run to point at the
hand-written `qucs_dat` fixture (see conftest.py). A real-simulator variant
lives in test_qucs_server_jobs_live.py, gated on qucsator being installed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_qucs_s import server
from mcp_qucs_s.backend_adapters import QucsatorAdapter
from mcp_qucs_s.netlist import generate_ladder_netlist
from rf_mcp_common.backend import RawBackendResult

SPARAMS_ANALYSIS = {
    "id": "sp1",
    "kind": "sparameters",
    "parameters": {"sweep": "lin", "points": 3, "f_start_hz": 1.0e9, "f_stop_hz": 3.0e9},
}


def _import_ladder(tmp_path: Path) -> tuple[str, str]:
    net_path = generate_ladder_netlist(
        [("series_l", {"L": 1e-9})],
        tmp_path / "ladder.net",
        f_start_hz=1e6,
        f_stop_hz=2e6,
        points=3,
        sweep="lin",
    )
    workspace = server.workspace_create("job-pipeline")
    assert workspace.status == "ok"
    workspace_id = workspace.data["workspace_id"]
    imported = server.artifact_import(workspace_id, str(net_path))
    assert imported.status == "ok"
    return workspace_id, imported.data["artifact_id"]


def _fake_run_from_fixture(qucs_dat: Path):
    def fake_run(self: QucsatorAdapter, request: object) -> RawBackendResult:
        return RawBackendResult(
            backend="qucsator",
            analysis="sparameters",
            artifact_paths=[qucs_dat],
            returncode=0,
            metadata={"dataset_path": str(qucs_dat)},
        )

    return fake_run


def test_simulation_submit_runs_job_to_completion(
    tmp_path: Path, qucs_dat: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(QucsatorAdapter, "run", _fake_run_from_fixture(qucs_dat))
    workspace_id, artifact_id = _import_ladder(tmp_path)

    submitted = server.simulation_submit(workspace_id, artifact_id, analysis=SPARAMS_ANALYSIS)
    assert submitted.status == "ok", submitted.error
    job_id = submitted.data["job_id"]

    terminal = server._JOBS.wait(job_id, timeout_sec=10.0)
    assert terminal["status"] == "completed", terminal.get("error")

    got = server.job_get(job_id)
    assert got.status == "ok"
    result = got.data["result"]
    assert result["backend"] == "qucsator"
    assert result["analysis"] == "sparameters"
    assert result["validation"]["valid"] is True

    artifacts = server.job_list_artifacts(job_id)
    assert artifacts.status == "ok"
    media_types = {item["media_type"] for item in artifacts.data}
    assert "application/json" in media_types

    read = server.artifact_read(workspace_id, result["dataset_artifact_id"])
    assert read.status == "ok"
    assert read.data["encoding"] == "base64"


def test_run_failure_marks_job_failed_and_retryable_then_retry_succeeds(
    tmp_path: Path, qucs_dat: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_run(self: QucsatorAdapter, request: object) -> RawBackendResult:
        raise RuntimeError("simulated qucsator crash")

    monkeypatch.setattr(QucsatorAdapter, "run", failing_run)
    workspace_id, artifact_id = _import_ladder(tmp_path)

    submitted = server.simulation_submit(workspace_id, artifact_id, analysis=SPARAMS_ANALYSIS)
    assert submitted.status == "ok"
    job_id = submitted.data["job_id"]

    terminal = server._JOBS.wait(job_id, timeout_sec=10.0)
    assert terminal["status"] == "failed"
    assert terminal["retryable"] is True
    assert "simulated qucsator crash" in terminal["error"]

    monkeypatch.setattr(QucsatorAdapter, "run", _fake_run_from_fixture(qucs_dat))
    retried = server.job_retry(job_id)
    assert retried.status == "ok"

    terminal_after_retry = server._JOBS.wait(job_id, timeout_sec=10.0)
    assert terminal_after_retry["status"] == "completed"


def test_missing_artifact_returns_error_envelope(tmp_path: Path) -> None:
    workspace = server.workspace_create("job-pipeline-missing")
    workspace_id = workspace.data["workspace_id"]

    submitted = server.simulation_submit(workspace_id, "0" * 32, analysis=SPARAMS_ANALYSIS)
    assert submitted.status == "error"
    assert "artifact" in submitted.error.lower()


def test_unroutable_analysis_kind_is_rejected_before_a_job_is_created(tmp_path: Path) -> None:
    workspace_id, artifact_id = _import_ladder(tmp_path)

    submitted = server.simulation_submit(
        workspace_id,
        artifact_id,
        analysis={"id": "tr1", "kind": "transient", "parameters": {}},
    )
    assert submitted.status == "error"
    assert "no qucs-s backend" in submitted.error
