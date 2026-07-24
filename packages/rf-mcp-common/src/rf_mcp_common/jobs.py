"""Durable workspace, artifact, and bounded background-job primitives."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from rf_mcp_common.simulation_workspace import SimulationWorkspace, sha256_file

JobHandler = Callable[["JobContext", dict[str, Any]], dict[str, Any]]
_TERMINAL = {"completed", "failed", "cancelled"}
_NONTERMINAL = {"created", "validating", "queued", "running", "postprocessing"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object in {path}")
    return cast(dict[str, Any], value)


class WorkspaceStore:
    """Persistent artifact store addressed only by opaque IDs."""

    def __init__(self, root: str | Path | None = None) -> None:
        configured = root or os.environ.get("RF_MCP_WORK_ROOT")
        self.root = (
            Path(configured or Path(tempfile.gettempdir()) / "rf-mcp-workbench")
            .expanduser()
            .resolve()
        )
        self.workspaces_root = self.root / "workspaces"
        self.workspaces_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def create(self, label: str | None = None) -> dict[str, Any]:
        workspace_id = uuid.uuid4().hex
        root = self.workspaces_root / workspace_id
        (root / "artifacts").mkdir(parents=True)
        manifest = {
            "schema_version": 1,
            "workspace_id": workspace_id,
            "label": label,
            "created_at": _now(),
            "updated_at": _now(),
            "artifacts": {},
        }
        _atomic_json(root / "manifest.json", manifest)
        return manifest

    def get(self, workspace_id: str) -> dict[str, Any]:
        path = self._workspace_path(workspace_id) / "manifest.json"
        if not path.is_file():
            raise KeyError(f"workspace not found: {workspace_id}")
        return _read_json_object(path)

    def import_file(
        self,
        workspace_id: str,
        source: str | Path,
        *,
        media_type: str = "application/octet-stream",
        max_bytes: int = 64 * 1024 * 1024,
    ) -> dict[str, Any]:
        source_path = Path(source).expanduser().resolve(strict=True)
        if not source_path.is_file():
            raise ValueError("artifact source must be a regular file")
        size = source_path.stat().st_size
        if size > max_bytes:
            raise ValueError(f"artifact exceeds the {max_bytes} byte import limit")
        artifact_id = uuid.uuid4().hex
        workspace = self._workspace_path(workspace_id)
        destination_dir = workspace / "artifacts" / artifact_id
        destination_dir.mkdir(parents=True)
        destination = destination_dir / source_path.name
        shutil.copy2(source_path, destination)
        record = {
            "artifact_id": artifact_id,
            "name": source_path.name,
            "media_type": media_type,
            "size_bytes": size,
            "sha256": sha256_file(destination),
            "created_at": _now(),
            "source_path": str(source_path),
            "path": str(destination),
        }
        with self._lock:
            manifest = self.get(workspace_id)
            manifest["artifacts"][artifact_id] = record
            manifest["updated_at"] = _now()
            _atomic_json(workspace / "manifest.json", manifest)
        return cast(dict[str, Any], record)

    def add_generated(
        self,
        workspace_id: str,
        source: str | Path,
        *,
        name: str | None = None,
        media_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        source_path = Path(source).resolve(strict=True)
        record = self.import_file(
            workspace_id,
            source_path,
            media_type=media_type,
        )
        if name is not None:
            record["name"] = name
            with self._lock:
                manifest = self.get(workspace_id)
                manifest["artifacts"][record["artifact_id"]] = record
                manifest["updated_at"] = _now()
                _atomic_json(
                    self._workspace_path(workspace_id) / "manifest.json",
                    manifest,
                )
        return record

    def import_spice_tree(
        self,
        workspace_id: str,
        source: str | Path,
        *,
        max_bytes: int = 64 * 1024 * 1024,
    ) -> dict[str, Any]:
        """Import a confined SPICE include graph while preserving layout."""
        self.get(workspace_id)
        artifact_id = uuid.uuid4().hex
        destination_dir = self._workspace_path(workspace_id) / "artifacts" / artifact_id
        snapshot = SimulationWorkspace.create(
            "artifact-import",
            parent=destination_dir,
        )
        main = snapshot.snapshot_simulation_tree(
            source,
            max_total_bytes=max_bytes,
        )
        inputs = snapshot.manifest["inputs"]
        record = {
            "artifact_id": artifact_id,
            "name": Path(source).name,
            "media_type": "application/x-spice",
            "size_bytes": sum(int(item["size_bytes"]) for item in inputs),
            "sha256": sha256_file(main),
            "created_at": _now(),
            "source_path": str(Path(source).expanduser().resolve()),
            "path": str(main),
            "dependency_count": max(0, len(inputs) - 1),
            "dependency_manifest": str(snapshot.manifest_path),
        }
        with self._lock:
            manifest = self.get(workspace_id)
            manifest["artifacts"][artifact_id] = record
            manifest["updated_at"] = _now()
            _atomic_json(
                self._workspace_path(workspace_id) / "manifest.json",
                manifest,
            )
        return record

    def artifact(self, workspace_id: str, artifact_id: str) -> dict[str, Any]:
        manifest = self.get(workspace_id)
        try:
            record = manifest["artifacts"][artifact_id]
        except KeyError as exc:
            raise KeyError(f"artifact not found: {artifact_id}") from exc
        path = Path(record["path"]).resolve()
        try:
            path.relative_to(self._workspace_path(workspace_id))
        except ValueError as exc:
            raise RuntimeError("artifact manifest path escaped workspace") from exc
        if not path.is_file() or sha256_file(path) != record["sha256"]:
            raise RuntimeError("artifact is missing or its checksum no longer matches")
        return cast(dict[str, Any], record)

    def read(
        self,
        workspace_id: str,
        artifact_id: str,
        *,
        max_bytes: int = 2 * 1024 * 1024,
    ) -> bytes:
        record = self.artifact(workspace_id, artifact_id)
        if record["size_bytes"] > max_bytes:
            raise ValueError(
                f"artifact is {record['size_bytes']} bytes; direct reads are limited "
                f"to {max_bytes} bytes"
            )
        return Path(record["path"]).read_bytes()

    def _workspace_path(self, workspace_id: str) -> Path:
        if len(workspace_id) != 32 or any(c not in "0123456789abcdef" for c in workspace_id):
            raise ValueError("invalid workspace ID")
        return self.workspaces_root / workspace_id


@dataclass
class JobContext:
    manager: DurableJobManager
    job_id: str
    cancel_event: threading.Event

    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def update_progress(
        self,
        completed: int,
        total: int,
        message: str | None = None,
    ) -> None:
        self.manager._update(
            self.job_id,
            progress={
                "completed": completed,
                "total": total,
                "message": message,
                "updated_at": _now(),
            },
        )


class DurableJobManager:
    """File-backed state machine with bounded in-process workers."""

    def __init__(
        self,
        store: WorkspaceStore,
        *,
        max_workers: int = 4,
    ) -> None:
        if not 1 <= max_workers <= 8:
            raise ValueError("max_workers must be in [1, 8]")
        self.store = store
        self.jobs_root = store.root / "jobs"
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="rf-mcp-job",
        )
        self._handlers: dict[str, JobHandler] = {}
        self._futures: dict[str, Future[dict[str, Any]]] = {}
        self._cancel: dict[str, threading.Event] = {}
        self._lock = threading.RLock()
        self._recover_interrupted()

    def register(self, operation: str, handler: JobHandler) -> None:
        self._handlers[operation] = handler

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def submit(
        self,
        operation: str,
        payload: dict[str, Any],
        *,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        if operation not in self._handlers:
            raise ValueError(f"unsupported job operation: {operation}")
        if workspace_id is not None:
            self.store.get(workspace_id)
        job_id = uuid.uuid4().hex
        manifest = {
            "schema_version": 1,
            "job_id": job_id,
            "workspace_id": workspace_id,
            "operation": operation,
            "status": "created",
            "created_at": _now(),
            "updated_at": _now(),
            "started_at": None,
            "completed_at": None,
            "attempt": 1,
            "progress": {"completed": 0, "total": 1, "message": "created"},
            "payload": payload,
            "result": None,
            "error": None,
            "cancel_requested": False,
            "retryable": False,
        }
        _atomic_json(self._manifest_path(job_id), manifest)
        self._update(job_id, status="validating")
        self._update(job_id, status="queued")
        self._start(job_id)
        return self.get(job_id)

    def get(self, job_id: str) -> dict[str, Any]:
        path = self._manifest_path(job_id)
        if not path.is_file():
            raise KeyError(f"job not found: {job_id}")
        return _read_json_object(path)

    def job_root(self, job_id: str) -> Path:
        """Return the confined directory for a validated opaque job ID."""
        return self._manifest_path(job_id).parent

    def cancel(self, job_id: str) -> dict[str, Any]:
        manifest = self.get(job_id)
        if manifest["status"] in _TERMINAL:
            return manifest
        event = self._cancel.setdefault(job_id, threading.Event())
        event.set()
        self._update(job_id, cancel_requested=True)
        future = self._futures.get(job_id)
        if future is not None and future.cancel():
            self._update(
                job_id,
                status="cancelled",
                completed_at=_now(),
                progress={
                    "completed": 1,
                    "total": 1,
                    "message": "cancelled before start",
                    "updated_at": _now(),
                },
            )
        return self.get(job_id)

    def wait(self, job_id: str, *, timeout_sec: float) -> dict[str, Any]:
        """Wait for a terminal manifest without losing durable state."""
        deadline = time.monotonic() + timeout_sec
        while True:
            manifest = self.get(job_id)
            if manifest["status"] in _TERMINAL:
                return manifest
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"job {job_id} did not reach a terminal state within {timeout_sec}s"
                )
            time.sleep(0.05)

    def retry(self, job_id: str) -> dict[str, Any]:
        manifest = self.get(job_id)
        if manifest["status"] not in {"failed", "cancelled"}:
            raise ValueError("only failed or cancelled jobs can be retried")
        if manifest["operation"] not in self._handlers:
            raise ValueError("job operation is unavailable in this server")
        event = threading.Event()
        self._cancel[job_id] = event
        manifest.update(
            {
                "status": "queued",
                "attempt": int(manifest["attempt"]) + 1,
                "error": None,
                "result": None,
                "completed_at": None,
                "cancel_requested": False,
                "retryable": False,
                "updated_at": _now(),
            }
        )
        _atomic_json(self._manifest_path(job_id), manifest)
        self._start(job_id)
        return self.get(job_id)

    def _start(self, job_id: str) -> None:
        event = self._cancel.setdefault(job_id, threading.Event())
        self._futures[job_id] = self._executor.submit(self._run, job_id, event)

    def _run(self, job_id: str, cancel_event: threading.Event) -> dict[str, Any]:
        manifest = self.get(job_id)
        operation = str(manifest["operation"])
        handler = self._handlers[operation]
        if cancel_event.is_set():
            self._update(job_id, status="cancelled", completed_at=_now())
            return self.get(job_id)
        self._update(job_id, status="running", started_at=_now())
        context = JobContext(self, job_id, cancel_event)
        try:
            result = handler(context, manifest["payload"])
            if cancel_event.is_set():
                self._update(job_id, status="cancelled", completed_at=_now())
                return self.get(job_id)
            self._update(job_id, status="postprocessing")
            self._update(
                job_id,
                status="completed",
                result=result,
                completed_at=_now(),
                progress={
                    "completed": 1,
                    "total": 1,
                    "message": "completed",
                    "updated_at": _now(),
                },
            )
        except Exception as exc:
            status = "cancelled" if cancel_event.is_set() else "failed"
            self._update(
                job_id,
                status=status,
                error=str(exc),
                completed_at=_now(),
                retryable=status == "failed",
            )
        return self.get(job_id)

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            manifest = self.get(job_id)
            current = manifest["status"]
            requested = changes.get("status", current)
            if current in _TERMINAL and requested != current:
                raise RuntimeError(f"cannot transition terminal job {current} -> {requested}")
            manifest.update(changes)
            manifest["updated_at"] = _now()
            _atomic_json(self._manifest_path(job_id), manifest)

    def _recover_interrupted(self) -> None:
        for path in self.jobs_root.glob("*/manifest.json"):
            try:
                manifest = _read_json_object(path)
            except (OSError, json.JSONDecodeError):
                continue
            if manifest.get("status") in _NONTERMINAL:
                manifest.update(
                    {
                        "status": "failed",
                        "error": "server restarted while job was active",
                        "completed_at": _now(),
                        "updated_at": _now(),
                        "retryable": True,
                    }
                )
                _atomic_json(path, manifest)

    def _manifest_path(self, job_id: str) -> Path:
        if len(job_id) != 32 or any(c not in "0123456789abcdef" for c in job_id):
            raise ValueError("invalid job ID")
        return self.jobs_root / job_id / "manifest.json"
