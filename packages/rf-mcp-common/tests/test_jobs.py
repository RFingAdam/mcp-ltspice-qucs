from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from rf_mcp_common.jobs import DurableJobManager, WorkspaceStore


def test_workspace_artifacts_are_opaque_hashed_and_bounded(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "store")
    workspace = store.create("demo")
    source = tmp_path / "input.cir"
    source.write_text("R1 in 0 50\n", encoding="utf-8")

    record = store.import_file(workspace["workspace_id"], source)

    assert len(record["artifact_id"]) == 32
    assert record["sha256"]
    assert store.read(workspace["workspace_id"], record["artifact_id"]) == source.read_bytes()
    with pytest.raises(ValueError, match="invalid workspace ID"):
        store.get("../escape")


def test_spice_artifact_import_copies_dependency_graph(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "store")
    workspace = store.create()
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    (source_dir / "part.lib").write_text(".model D D\n", encoding="utf-8")
    main = source_dir / "main.cir"
    main.write_text('.include "part.lib"\nR1 in 0 50\n', encoding="utf-8")

    record = store.import_spice_tree(workspace["workspace_id"], main)

    assert record["dependency_count"] == 1
    imported = Path(record["path"])
    assert (imported.parent / "part.lib").is_file()


def test_durable_job_completes_with_progress(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "store")
    manager = DurableJobManager(store, max_workers=1)

    def handler(context, payload):
        context.update_progress(1, 2, "half")
        return {"answer": payload["value"] * 2}

    manager.register("double", handler)
    job = manager.submit("double", {"value": 21})
    terminal = manager.wait(job["job_id"], timeout_sec=2)
    manager.shutdown()

    assert terminal["status"] == "completed"
    assert terminal["result"] == {"answer": 42}
    assert terminal["progress"]["message"] == "completed"


def test_running_job_can_be_cancelled(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "store")
    manager = DurableJobManager(store, max_workers=1)

    def handler(context, _payload):
        while not context.cancelled():
            time.sleep(0.01)
        return {}

    manager.register("wait", handler)
    job = manager.submit("wait", {})
    manager.cancel(job["job_id"])
    terminal = manager.wait(job["job_id"], timeout_sec=2)
    manager.shutdown()

    assert terminal["status"] == "cancelled"
    assert terminal["cancel_requested"] is True


def test_interrupted_manifest_is_recovered_as_retryable_failure(tmp_path: Path) -> None:
    store = WorkspaceStore(tmp_path / "store")
    job_id = "a" * 32
    path = store.root / "jobs" / job_id / "manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "job_id": job_id,
                "operation": "demo",
                "status": "running",
                "attempt": 1,
            }
        ),
        encoding="utf-8",
    )

    manager = DurableJobManager(store, max_workers=1)
    recovered = manager.get(job_id)
    manager.shutdown()

    assert recovered["status"] == "failed"
    assert recovered["retryable"] is True
    assert "restarted" in recovered["error"]
