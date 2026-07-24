from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp_ltspice import server
from rf_mcp_common.circuit_ir import CircuitComponent, CircuitDocument, CircuitNode


def _inductor_document() -> CircuitDocument:
    return CircuitDocument(
        document_id="tool-inductor",
        source_format="generated",
        nodes=[CircuitNode(id="0", is_ground=True), CircuitNode(id="in")],
        components=[
            CircuitComponent(
                refdes="L1",
                kind="inductor",
                pins={"1": "in", "2": "0"},
                value=4.7e-9,
            )
        ],
    )


def test_component_search_and_model_attachment_tools() -> None:
    searched = server.search_component_models(
        kind="L",
        target_value=4.7e-9,
        packages=["0402 / 1005 metric"],
        availability="generic",
        min_srf_hz=4e9,
        vendors=["coilcraft_0402hp"],
        limit=1,
    )
    assert searched.status == "ok"
    assert searched.data["hits"][0]["selection_class"] == "generic"
    model = searched.data["hits"][0]["model"]

    attached = server.circuit_attach_models(
        _inductor_document().model_dump(mode="json"),
        {"L1": model},
    )
    assert attached.status == "ok"
    assert attached.data["model_hashes"]["L1"] == model["checksum_sha256"]
    assert attached.data["circuit"]["components"][0]["model"]["provider"] == "coilcraft_0402hp"


def test_circuit_export_creates_checksum_addressed_artifact() -> None:
    workspace = server.workspace_create("export")
    assert workspace.status == "ok"
    workspace_id = workspace.data["workspace_id"]
    exported = server.circuit_export(
        workspace_id,
        _inductor_document().model_dump(mode="json"),
        "spice",
    )
    assert exported.status == "ok"
    assert exported.data["resource_uri"].startswith(f"artifact://{workspace_id}/")
    payload = server.artifact_read(workspace_id, exported.data["artifact_id"])
    assert payload.status == "ok"


def test_circuit_validate_never_labels_unsupported_input_valid(tmp_path: Path) -> None:
    source = tmp_path / "unsupported.cir"
    source.write_text("R1 out 0 1k\nK1 L1 L2 0.9\n.end\n", encoding="utf-8")
    workspace = server.workspace_create("validate")
    imported = server.artifact_import(
        workspace.data["workspace_id"],
        str(source),
        include_dependencies=False,
    )

    advisory = server.circuit_validate(
        workspace.data["workspace_id"],
        imported.data["artifact_id"],
        require_lossless=False,
    )
    strict = server.circuit_validate(
        workspace.data["workspace_id"],
        imported.data["artifact_id"],
        require_lossless=True,
    )

    assert advisory.status == "ok"
    assert advisory.data["valid"] is False
    assert advisory.data["accepted"] is True
    assert strict.data["valid"] is False
    assert strict.data["accepted"] is False


def test_circuit_optimize_submit_validates_and_queues_generic_problem(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_submit(
        operation: str,
        payload: dict[str, Any],
        *,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        captured.update(
            {
                "operation": operation,
                "payload": payload,
                "workspace_id": workspace_id,
            }
        )
        return {"job_id": "a" * 32, "status": "queued"}

    monkeypatch.setattr(server._JOBS, "submit", fake_submit)
    problem = {
        "document": _inductor_document().model_dump(mode="json"),
        "variables": [
            {
                "path": "components.L1.value",
                "lower": 1e-9,
                "upper": 10e-9,
                "initial": 4.7e-9,
                "scale": "log",
            }
        ],
        "objectives": [{"metric": "gain_db", "goal": "maximize"}],
        "constraints": [{"metric": "gain_db", "operator": "ge", "limit": -3.0}],
        "iterations": 4,
        "seed": 9,
        "require_independent_backend": True,
    }
    submitted = server.circuit_optimize_submit(
        problem,
        {
            "id": "ac1",
            "kind": "ac",
            "parameters": {
                "sweep": "dec",
                "points": 10,
                "f_start_hz": 1e6,
                "f_stop_hz": 1e9,
            },
        },
        [
            {
                "name": "gain_db",
                "trace": "V(in)",
                "projection": "magnitude_db",
                "reduction": "at",
                "axis_value": 1e8,
            }
        ],
        "ngspice",
        "ltspice",
    )
    assert submitted.status == "ok"
    assert captured["operation"] == "circuit_optimization"
    assert captured["payload"]["problem"]["seed"] == 9
