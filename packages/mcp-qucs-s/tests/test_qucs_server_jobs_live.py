"""Known-answer simulation through the real durable job pipeline.

Gated on qucsator being installed (see conftest.py's `qucs` marker). Proves
the wired QucsatorAdapter actually drives a real qucsator process end to end
through simulation_submit -> job_get -> artifact_read, not just against a
monkeypatched run(). Numerical cross-backend agreement with ngspice is
already covered by tests/test_cross_backend_known_answer.py; this test only
exercises the job/artifact plumbing.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from mcp_qucs_s import server
from mcp_qucs_s.netlist import generate_ladder_netlist

SPARAMS_ANALYSIS = {
    "id": "sp1",
    "kind": "sparameters",
    "parameters": {"sweep": "log", "points": 21, "f_start_hz": 1.0e7, "f_stop_hz": 3.0e9},
}


@pytest.mark.qucs
@pytest.mark.integration
def test_known_answer_ladder_simulates_through_the_job_pipeline(tmp_path: Path) -> None:
    net_path = generate_ladder_netlist(
        [("series_l", {"L": 10e-9}), ("shunt_c", {"C": 2e-12})],
        tmp_path / "ladder.net",
        f_start_hz=1e7,
        f_stop_hz=3e9,
        points=21,
        sweep="log",
    )
    workspace = server.workspace_create("job-pipeline-live")
    assert workspace.status == "ok"
    workspace_id = workspace.data["workspace_id"]
    imported = server.artifact_import(workspace_id, str(net_path))
    assert imported.status == "ok"

    submitted = server.simulation_submit(
        workspace_id, imported.data["artifact_id"], analysis=SPARAMS_ANALYSIS
    )
    assert submitted.status == "ok", submitted.error
    job_id = submitted.data["job_id"]

    terminal = server._JOBS.wait(job_id, timeout_sec=60.0)
    assert terminal["status"] == "completed", terminal.get("error")

    result = terminal["result"]
    assert result["backend"] == "qucsator"
    assert result["returncode"] == 0
    assert result["validation"]["valid"] is True

    read = server.artifact_read(workspace_id, result["dataset_artifact_id"])
    assert read.status == "ok"
    dataset = json.loads(base64.b64decode(read.data["content"]))
    assert dataset["backend"] == "qucsator"
    assert len(dataset["axis"]["values"]) == 21
    assert set(dataset["traces"]) == {"S[1,1]", "S[1,2]", "S[2,1]", "S[2,2]"}
