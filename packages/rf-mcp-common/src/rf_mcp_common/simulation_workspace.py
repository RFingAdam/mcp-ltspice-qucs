"""Durable, provenance-rich workspaces for external simulator runs.

Simulator output is trustworthy only when it can be tied to one invocation.
This module gives every run a unique directory and records the inputs,
command, logs, outputs, hashes, timestamps, backend identity, and terminal
status in an atomically-written JSON manifest.

The workspace is intentionally backend-neutral.  Simulator adapters remain
responsible for validating the semantic contents of their output.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, cast

_MANIFEST_SCHEMA_VERSION = 1
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
_SPICE_INCLUDE = re.compile(
    r"""(?im)(?:^|!)\s*\.(?:include|inc|lib)\s+(?:"([^"]+)"|'([^']+)'|([^\s;]+))"""
)

# External simulators normally need little more than executable/library lookup,
# locale, user configuration, and a temporary directory.  Keeping this list
# explicit prevents unrelated credentials and agent configuration from leaking
# into child processes.
#
# The Windows path variables below are not optional conveniences.  Windows APIs
# expand "%SystemDrive%" / "%ProgramData%" internally; when those names are
# absent from the child environment the expansion yields the *literal* string,
# which Windows then treats as a path relative to the working directory.  A
# simulator launched with cwd inside a repository silently created a
# "%SystemDrive%/ProgramData/..." tree in the working copy.  All of these are
# fixed system locations, not secrets.
DEFAULT_SUBPROCESS_ENV_KEYS = frozenset(
    {
        "ALLUSERSPROFILE",
        "APPDATA",
        "COMMONPROGRAMFILES",
        "COMSPEC",
        "DYLD_FALLBACK_LIBRARY_PATH",
        "DYLD_LIBRARY_PATH",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "LANG",
        "LANGUAGE",
        "LC_ALL",
        "LD_LIBRARY_PATH",
        "LOCALAPPDATA",
        "LOGNAME",
        "MKL_NUM_THREADS",
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "PATH",
        "PATHEXT",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "SHELL",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USER",
        "USERPROFILE",
        "WINDIR",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
    }
)


class ProcessCancelledError(RuntimeError):
    """Raised after a simulator process tree was terminated by cancellation."""


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    """Terminate a process group and escalate to a hard kill if needed."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        # taskkill is the only standard way to include descendants on Windows.
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        return

    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)


def run_process_tree(
    command: Sequence[str],
    *,
    cwd: str | Path | None = None,
    environment: Mapping[str, str] | None = None,
    timeout_sec: float,
    cancel_requested: Callable[[], bool] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command in its own process group with timeout/cancel tree cleanup."""
    if timeout_sec <= 0:
        raise ValueError("timeout_sec must be positive")
    popen_kwargs: dict[str, Any] = {}
    if os.name == "nt":
        popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
    else:
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(
        list(command),
        cwd=cwd,
        env=dict(environment) if environment is not None else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        **popen_kwargs,
    )
    deadline = time.monotonic() + timeout_sec
    while True:
        if cancel_requested is not None and cancel_requested():
            _terminate_process_tree(process)
            stdout, stderr = process.communicate()
            raise ProcessCancelledError(
                f"process was cancelled: {' '.join(command)}"
                + (f"; stderr={stderr[-500:]!r}" if stderr else "")
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate_process_tree(process)
            stdout, stderr = process.communicate()
            raise subprocess.TimeoutExpired(
                command,
                timeout_sec,
                output=stdout,
                stderr=stderr,
            )
        try:
            stdout, stderr = process.communicate(
                timeout=min(remaining, 0.2 if cancel_requested is not None else remaining)
            )
            return subprocess.CompletedProcess(
                list(command),
                process.returncode,
                stdout,
                stderr,
            )
        except subprocess.TimeoutExpired:
            if cancel_requested is None and time.monotonic() < deadline:
                # The full timeout was passed to communicate; this path is
                # normally reached only at the deadline. Loop to centralize
                # process-tree cleanup and consistent TimeoutExpired output.
                continue


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256_file(path: str | Path) -> str:
    """Return the SHA-256 digest of ``path`` without loading it into memory."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def subprocess_environment(extra_keys: Iterable[str] = ()) -> dict[str, str]:
    """Return a minimal inherited environment for a simulator subprocess."""
    allowed = DEFAULT_SUBPROCESS_ENV_KEYS | frozenset(extra_keys)
    return {key: value for key, value in os.environ.items() if key in allowed}


def probe_executable_version(
    executable: str | Path,
    *,
    environment: dict[str, str] | None = None,
    timeout_sec: float = 5.0,
) -> str | None:
    """Best-effort one-line ``--version`` probe for manifest provenance."""
    try:
        proc = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    text = (proc.stdout or proc.stderr).strip()
    if not text:
        return None
    # Several simulators frame their version banner with lines such as
    # ``******``.  Record the first informative line, not banner decoration.
    informative = next(
        (line.strip() for line in text.splitlines() if re.search(r"[A-Za-z0-9]", line)),
        None,
    )
    return informative.strip("*=- ")[:500] if informative else None


@dataclass
class SimulationWorkspace:
    """One immutable-input, isolated-output simulator invocation."""

    root: Path
    run_id: str
    backend: str
    manifest_path: Path
    _manifest: dict[str, Any] = field(repr=False)

    @classmethod
    def create(
        cls,
        backend: str,
        *,
        parent: str | Path | None = None,
    ) -> SimulationWorkspace:
        """Create a unique run directory and initial manifest."""
        safe_backend = _SAFE_NAME.sub("-", backend).strip("-") or "simulator"
        base = (
            Path(parent).expanduser().resolve()
            if parent is not None
            else Path(tempfile.gettempdir()).resolve() / "rf-mcp-runs"
        )
        base.mkdir(parents=True, exist_ok=True)
        run_id = uuid.uuid4().hex
        root = Path(tempfile.mkdtemp(prefix=f"{safe_backend}-{run_id[:8]}-", dir=base)).resolve()
        for directory in ("inputs", "outputs", "logs"):
            (root / directory).mkdir()

        manifest_path = root / "manifest.json"
        manifest: dict[str, Any] = {
            "schema_version": _MANIFEST_SCHEMA_VERSION,
            "run_id": run_id,
            "backend": {"name": backend},
            "status": "created",
            "created_at": _utc_now(),
            "started_at": None,
            "completed_at": None,
            "command": None,
            "cwd": None,
            "environment": None,
            "returncode": None,
            "error": None,
            "inputs": [],
            "artifacts": [],
        }
        workspace = cls(
            root=root,
            run_id=run_id,
            backend=backend,
            manifest_path=manifest_path,
            _manifest=manifest,
        )
        workspace._write_manifest()
        return workspace

    @property
    def manifest(self) -> dict[str, Any]:
        """Return a JSON-safe snapshot of the current manifest."""
        return cast(dict[str, Any], json.loads(json.dumps(self._manifest)))

    def snapshot_input(
        self,
        source: str | Path,
        *,
        name: str | None = None,
    ) -> Path:
        """Copy an input into the workspace and record its source and hash."""
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"Simulation input not found: {source_path}")
        destination = self._child_path("inputs", name or source_path.name)
        if destination.exists():
            raise FileExistsError(f"Workspace input already exists: {destination}")
        shutil.copy2(source_path, destination)
        self._manifest["inputs"].append(
            self._file_record(destination)
            | {
                "source_path": str(source_path),
                "role": "simulation_input",
            }
        )
        self._write_manifest()
        return destination

    def snapshot_simulation_tree(
        self,
        source: str | Path,
        *,
        allowed_root: str | Path | None = None,
        max_files: int = 256,
        max_total_bytes: int = 64 * 1024 * 1024,
    ) -> Path:
        """Snapshot a SPICE input and its statically resolvable include graph.

        The default trust boundary is the source file's lexical parent.
        Absolute includes, traversal, symlink escapes, dynamic include
        expressions, cycles beyond the already-seen set, and resource-limit
        excesses are rejected. Relative layout is preserved inside
        ``inputs/`` so copied include directives remain valid.
        """
        lexical = Path(source).expanduser()
        if not lexical.is_absolute():
            lexical = (Path.cwd() / lexical).absolute()
        root = (
            Path(allowed_root).expanduser().resolve()
            if allowed_root is not None
            else lexical.parent.resolve()
        )
        resolved_source = lexical.resolve(strict=True)
        try:
            resolved_source.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"simulation input escapes allowed root {root}: {lexical}") from exc

        seen: set[Path] = set()
        total_bytes = 0
        copied: dict[Path, Path] = {}

        def visit(path: Path) -> None:
            nonlocal total_bytes
            resolved = path.resolve(strict=True)
            try:
                relative = resolved.relative_to(root)
            except ValueError as exc:
                raise ValueError(
                    f"simulation dependency escapes allowed root {root}: {path}"
                ) from exc
            if resolved in seen:
                return
            if not resolved.is_file():
                raise ValueError(f"simulation dependency is not a file: {resolved}")
            if len(seen) >= max_files:
                raise ValueError(f"simulation dependency graph exceeds {max_files} files")
            size = resolved.stat().st_size
            total_bytes += size
            if total_bytes > max_total_bytes:
                raise ValueError(f"simulation dependency graph exceeds {max_total_bytes} bytes")
            seen.add(resolved)

            destination = (self.root / "inputs" / relative).resolve()
            inputs_root = (self.root / "inputs").resolve()
            try:
                destination.relative_to(inputs_root)
            except ValueError as exc:  # pragma: no cover - relative_to(root) already protects this
                raise ValueError("workspace input destination escaped confinement") from exc
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(resolved, destination)
            copied[resolved] = destination
            self._manifest["inputs"].append(
                self._file_record(destination)
                | {
                    "source_path": str(resolved),
                    "role": (
                        "simulation_input"
                        if resolved == resolved_source
                        else "simulation_dependency"
                    ),
                }
            )

            if resolved.suffix.lower() not in {
                ".asc",
                ".cir",
                ".net",
                ".sp",
                ".spice",
                ".lib",
                ".mod",
                ".model",
            }:
                return
            try:
                text = resolved.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = resolved.read_text(encoding="utf-16")
            for match in _SPICE_INCLUDE.finditer(text):
                token = next(group for group in match.groups() if group is not None)
                if any(marker in token for marker in ("{", "}", "$(", "`")):
                    raise ValueError(f"dynamic SPICE include is not allowed: {token!r}")
                include = Path(token).expanduser()
                # Reject a token that is absolute under EITHER path convention,
                # not just the host's native one: a POSIX-rooted token such as
                # `/etc/passwd` has no drive and is therefore not "absolute" by
                # Windows semantics, which would otherwise let it slip past
                # this guard and combine with the confined parent's own drive.
                if (
                    include.is_absolute()
                    or PurePosixPath(token).is_absolute()
                    or PureWindowsPath(token).is_absolute()
                ):
                    raise ValueError(f"absolute SPICE include is not allowed: {token!r}")
                visit(resolved.parent / include)

        visit(resolved_source)
        self._write_manifest()
        return copied[resolved_source]

    def write_input_text(self, name: str, content: str) -> Path:
        """Write a generated text input and record it in the manifest."""
        destination = self._child_path("inputs", name)
        if destination.exists():
            raise FileExistsError(f"Workspace input already exists: {destination}")
        destination.write_text(content, encoding="utf-8")
        self._manifest["inputs"].append(
            self._file_record(destination)
            | {
                "source_path": None,
                "role": "generated_simulation_input",
            }
        )
        self._write_manifest()
        return destination

    def output_path(self, name: str) -> Path:
        """Return a confined path under the workspace output directory."""
        return self._child_path("outputs", name)

    def log_path(self, name: str) -> Path:
        """Return a confined path under the workspace log directory."""
        return self._child_path("logs", name)

    def start(
        self,
        command: Iterable[str | Path],
        *,
        cwd: str | Path,
        environment: dict[str, str],
        executable: str | Path,
        backend_version: str | None,
    ) -> None:
        """Record the exact execution contract before launching the process."""
        self._manifest["status"] = "running"
        self._manifest["started_at"] = _utc_now()
        self._manifest["command"] = [str(arg) for arg in command]
        self._manifest["cwd"] = str(Path(cwd).resolve())
        self._manifest["environment"] = {
            "policy": "allowlist",
            "keys": sorted(environment),
        }
        self._manifest["backend"] |= {
            "executable": str(Path(executable).expanduser().resolve()),
            "version": backend_version,
        }
        self._write_manifest()

    def write_streams(self, stdout: str, stderr: str) -> tuple[Path, Path]:
        """Persist process streams and register both as artifacts."""
        stdout_path = self.log_path("stdout.txt")
        stderr_path = self.log_path("stderr.txt")
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        self.record_artifact(stdout_path, role="simulator_stdout")
        self.record_artifact(stderr_path, role="simulator_stderr")
        return stdout_path, stderr_path

    def record_artifact(self, path: str | Path, *, role: str) -> None:
        """Record a fresh artifact and its digest."""
        artifact = Path(path).expanduser().resolve()
        if not artifact.is_file():
            raise FileNotFoundError(f"Artifact not found: {artifact}")
        self._manifest["artifacts"].append(self._file_record(artifact) | {"role": role})
        self._write_manifest()

    def publish(
        self,
        source: str | Path,
        target: str | Path,
        *,
        role: str,
    ) -> Path:
        """Atomically publish a validated workspace artifact to ``target``."""
        source_path = Path(source).expanduser().resolve()
        if not source_path.is_file():
            raise FileNotFoundError(f"Cannot publish missing artifact: {source_path}")
        target_path = Path(target).expanduser().resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        staging = target_path.parent / f".{target_path.name}.{self.run_id}.tmp"
        try:
            shutil.copy2(source_path, staging)
            os.replace(staging, target_path)
        finally:
            if staging.exists():
                staging.unlink()
        self._manifest["artifacts"].append(
            self._file_record(target_path)
            | {
                "role": role,
                "published_from": self._relative_path(source_path),
            }
        )
        self._write_manifest()
        return target_path

    def complete(self, *, returncode: int) -> None:
        """Mark the run completed after all output validation succeeds."""
        self._manifest["status"] = "completed"
        self._manifest["returncode"] = returncode
        self._manifest["completed_at"] = _utc_now()
        self._write_manifest()

    def fail(self, error: str, *, returncode: int | None = None) -> None:
        """Mark the run failed while preserving its diagnostic artifacts."""
        self._manifest["status"] = "failed"
        self._manifest["returncode"] = returncode
        self._manifest["error"] = error
        self._manifest["completed_at"] = _utc_now()
        self._write_manifest()

    def publish_manifest(self, target: str | Path) -> Path:
        """Atomically publish the current manifest without registering itself."""
        target_path = Path(target).expanduser().resolve()
        target_path.parent.mkdir(parents=True, exist_ok=True)
        staging = target_path.parent / f".{target_path.name}.{self.run_id}.tmp"
        try:
            shutil.copy2(self.manifest_path, staging)
            os.replace(staging, target_path)
        finally:
            if staging.exists():
                staging.unlink()
        return target_path

    def _child_path(self, directory: str, name: str) -> Path:
        candidate = (self.root / directory / name).resolve()
        expected_parent = (self.root / directory).resolve()
        if candidate.parent != expected_parent:
            raise ValueError(f"Workspace artifact name must be a basename: {name!r}")
        return candidate

    def _relative_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    def _file_record(self, path: Path) -> dict[str, Any]:
        return {
            "path": self._relative_path(path),
            "size_bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }

    def _write_manifest(self) -> None:
        staging = self.manifest_path.with_name(f".manifest.{self.run_id}.tmp")
        try:
            staging.write_text(
                json.dumps(self._manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            os.replace(staging, self.manifest_path)
        finally:
            if staging.exists():
                staging.unlink()
