"""Headless Qucs-S invocation.

Detection-first design: tools that need Qucs-S installed return clean
error envelopes when it's missing instead of crashing. This keeps the
synthesis tools (which don't need a simulator) usable on any machine.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rf_mcp_common.simulation_workspace import (
    SimulationWorkspace,
    probe_executable_version,
    run_process_tree,
    subprocess_environment,
)


@dataclass
class QucsRunResult:
    output_path: Path
    published_output_path: Path
    log_path: Path
    workspace_path: Path
    manifest_path: Path
    published_manifest_path: Path
    returncode: int
    stdout: str
    stderr: str


#: Headless simulation engines, best first. ``qucs-s`` is deliberately
#: absent: it is the Qt GUI, and handing it ``-i netlist -o dat`` opens a
#: window and blocks forever on a headless box instead of simulating.
QUCS_ENGINE_BINARIES = ("qucsator_rf", "qucsator")

#: The GUI. Only used to tell "nothing installed" apart from "GUI installed
#: but the engine was never built", which is what a clone missing
#: ``--recurse-submodules`` leaves behind.
QUCS_GUI_BINARIES = ("qucs-s",)


def find_qucs_gui() -> Path | None:
    """Locate the Qucs-S GUI, for diagnostics only — it cannot simulate."""
    for cand in QUCS_GUI_BINARIES:
        p = shutil.which(cand)
        if p:
            return Path(p)
    for c in (Path.home() / ".local/bin/qucs-s", Path("/usr/local/bin/qucs-s")):
        if c.is_file():
            return c
    return None


def find_qucs_s() -> Path | None:
    """Locate the qucsator simulation engine.

    Checks the ``QUCS_S_PATH`` env var, then ``$PATH``, then standard
    install locations. Returns the *engine*, never the GUI.
    """
    env = os.environ.get("QUCS_S_PATH")
    if env and Path(env).is_file():
        return Path(env)

    for cand in QUCS_ENGINE_BINARIES:
        p = shutil.which(cand)
        if p:
            return Path(p)

    home = Path.home()
    candidates = [
        home / ".local/bin/qucsator_rf",
        home / ".local/bin/qucsator",
        Path("/usr/local/bin/qucsator_rf"),
        Path("/usr/local/bin/qucsator"),
        Path("/Applications/Qucs-S.app/Contents/MacOS/qucsator_rf"),
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _missing_engine_message() -> str:
    gui = find_qucs_gui()
    if gui is not None:
        return (
            f"Found the Qucs-S GUI at {gui}, but not the qucsator simulation "
            f"engine ({' or '.join(QUCS_ENGINE_BINARIES)}), which is what runs "
            "headless netlists. The usual cause is cloning qucs_s without "
            "--recurse-submodules, so the qucsator-RF submodule was never "
            "built. See docs/installation.md."
        )
    return (
        "Qucs-S / qucsator not found. Install Qucs-S from source "
        "(see docs/installation.md) or set $QUCS_S_PATH to the qucsator binary."
    )


def find_xyce() -> Path | None:
    p = shutil.which("xyce") or shutil.which("Xyce")
    return Path(p) if p else None


def is_qucs_available() -> bool:
    return find_qucs_s() is not None


def is_xyce_available() -> bool:
    return find_xyce() is not None


def run_qucs(
    netlist_path: str | Path,
    *,
    output_path: str | Path | None = None,
    timeout_sec: float = 300.0,
    workspace_root: str | Path | None = None,
) -> QucsRunResult:
    """Invoke qucsator headlessly: ``qucsator -i in.net -o out.dat``.

    Takes a qucsator *netlist*, not the GUI's ``.sch`` file — the Qucs GUI
    netlists a schematic before handing it to the engine. Generate one
    with :func:`mcp_qucs_s.netlist.generate_ladder_netlist`.
    """
    sch = Path(netlist_path).expanduser().resolve()
    if not sch.is_file():
        raise FileNotFoundError(f"Netlist not found: {sch}")

    exe = find_qucs_s()
    if exe is None:
        raise RuntimeError(_missing_engine_message())

    requested_output = (
        Path(output_path).expanduser().resolve() if output_path else sch.with_suffix(".dat")
    )
    published_log = sch.with_suffix(".qucs.log")
    published_manifest = sch.with_suffix(".qucs.manifest.json")

    workspace = SimulationWorkspace.create("qucsator", parent=workspace_root)
    staged_input = workspace.snapshot_input(sch)
    staged_output = workspace.output_path("result.dat")
    staged_log = workspace.log_path("qucs.log")
    environment = subprocess_environment({"QT_PLUGIN_PATH", "QUCSATOR_PATH", "QUCS_S_PATH"})
    cmd = [str(exe), "-i", str(staged_input), "-o", str(staged_output)]
    workspace.start(
        cmd,
        cwd=workspace.root,
        environment=environment,
        executable=exe,
        backend_version=probe_executable_version(exe, environment=environment),
    )

    proc: subprocess.CompletedProcess[str] | None = None
    try:
        proc = run_process_tree(
            cmd,
            cwd=workspace.root,
            environment=environment,
            timeout_sec=timeout_sec,
        )
        workspace.write_streams(proc.stdout, proc.stderr)
        staged_log.write_text(
            f"$ {' '.join(cmd)}\nreturncode: {proc.returncode}\n"
            f"=== STDOUT ===\n{proc.stdout}\n=== STDERR ===\n{proc.stderr}",
            encoding="utf-8",
        )
        workspace.record_artifact(staged_log, role="simulator_log")

        if proc.returncode != 0:
            raise RuntimeError(
                f"Qucs-S exited with returncode={proc.returncode}. stderr={proc.stderr[-500:]!r}"
            )
        _validate_qucs_dataset(staged_output)
        workspace.record_artifact(staged_output, role="simulator_output")
        published = workspace.publish(staged_output, requested_output, role="published_output")
        # The workspace artifact is the canonical result for the default path:
        # it cannot be overwritten by a concurrent invocation using the same
        # input netlist.  An explicit output_path remains an opt-in shared
        # publication target and is returned for backward compatibility.
        out = published if output_path is not None else staged_output
        log = workspace.publish(staged_log, published_log, role="published_log")
        workspace.complete(returncode=proc.returncode)
        manifest = workspace.publish_manifest(published_manifest)
        return QucsRunResult(
            output_path=out,
            published_output_path=published,
            log_path=log,
            workspace_path=workspace.root,
            manifest_path=workspace.manifest_path,
            published_manifest_path=manifest,
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _timeout_stream(exc.stdout)
        stderr = _timeout_stream(exc.stderr)
        workspace.write_streams(stdout, stderr)
        staged_log.write_text(
            f"$ {' '.join(cmd)}\ntimeout_sec: {timeout_sec}\n"
            f"=== STDOUT ===\n{stdout}\n=== STDERR ===\n{stderr}",
            encoding="utf-8",
        )
        workspace.record_artifact(staged_log, role="simulator_log")
        workspace.fail(f"Qucs-S timed out after {timeout_sec} seconds")
        workspace.publish(staged_log, published_log, role="published_log")
        manifest = workspace.publish_manifest(published_manifest)
        raise RuntimeError(
            f"Qucs-S timed out after {timeout_sec} seconds. Manifest: {manifest}"
        ) from exc
    except Exception as exc:
        returncode = proc.returncode if proc is not None else None
        workspace.fail(str(exc), returncode=returncode)
        if staged_log.is_file():
            workspace.publish(staged_log, published_log, role="published_log")
        manifest = workspace.publish_manifest(published_manifest)
        raise RuntimeError(f"{exc}. Manifest: {manifest}") from exc


def _validate_qucs_dataset(path: Path) -> None:
    """Reject missing, empty, or non-Qucs output before publishing it."""
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f"Qucs-S did not produce a non-empty dataset at {path}")
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        header = handle.read(4096)
    if "<Qucs Dataset" not in header:
        raise RuntimeError(f"Qucs-S output is not a recognized Qucs dataset: {path}")


def _timeout_stream(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value
