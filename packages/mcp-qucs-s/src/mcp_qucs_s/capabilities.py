"""Readiness probes for Qucsator and Xyce."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mcp_qucs_s.harmonic_balance import build_hb_netlist, run_xyce
from mcp_qucs_s.netlist import generate_ladder_netlist
from mcp_qucs_s.runner import find_qucs_s, find_xyce, run_qucs
from rf_mcp_common.simulation_workspace import SimulationWorkspace

_LAST_PROBES: dict[str, dict[str, Any]] = {}
SUPPORTED_ANALYSES = {
    "qucsator": ("sparameters", "noise"),
    "xyce": ("harmonic_balance",),
}


def _version(executable: Path, timeout_sec: float) -> tuple[bool, str | None]:
    for argument in ("--version", "-v"):
        try:
            process = subprocess.run(
                [str(executable), argument],
                capture_output=True,
                text=True,
                timeout=min(timeout_sec, 5.0),
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        text = (process.stdout or process.stderr).strip()
        if process.returncode == 0:
            return True, text.splitlines()[0][:500] if text else None
    return False, None


def probe_qucs_backend(
    backend: str,
    *,
    validate: bool = True,
    timeout_sec: float = 20.0,
) -> dict[str, Any]:
    """Probe executable launch and optionally run a tiny known-answer analysis."""
    normalized = backend.lower()
    if normalized not in {"qucsator", "xyce"}:
        raise ValueError("backend must be 'qucsator' or 'xyce'")
    executable = find_qucs_s() if normalized == "qucsator" else find_xyce()
    result: dict[str, Any] = {
        "backend": normalized,
        "installed": executable is not None,
        "launchable": False,
        "validated": False,
        "state": "unavailable",
        "executable": str(executable) if executable else None,
        "version": None,
        "supported_analyses": list(SUPPORTED_ANALYSES[normalized]),
        "last_probe_time": datetime.now(UTC).isoformat(),
        "diagnostic": None,
        "sandbox_profile": {
            "name": None,
            "available": False,
            "diagnostic": "No verified OS sandbox profile for this backend.",
        },
    }
    if executable is None:
        result["diagnostic"] = f"{normalized} executable not found"
        _LAST_PROBES[normalized] = result
        return result
    launchable, version = _version(executable, timeout_sec)
    result["launchable"] = launchable
    result["version"] = version
    result["state"] = "launchable" if launchable else "installed"
    if not launchable:
        result["diagnostic"] = "version probe could not launch executable successfully"
        _LAST_PROBES[normalized] = result
        return result
    if not validate:
        cached = _LAST_PROBES.get(normalized)
        if cached and cached.get("validated"):
            result["validated"] = True
            result["state"] = "validated"
            result["last_validation_time"] = cached["last_probe_time"]
        _LAST_PROBES[normalized] = result
        return result

    workspace = SimulationWorkspace.create(f"probe-{normalized}")
    try:
        if normalized == "qucsator":
            netlist = generate_ladder_netlist(
                [("series_l", {"L": 1e-9})],
                workspace.root / "inputs" / "known_answer.net",
                f_start_hz=1e6,
                f_stop_hz=2e6,
                points=3,
                sweep="lin",
            )
            simulation = run_qucs(
                netlist,
                output_path=workspace.root / "outputs" / "known_answer.dat",
                timeout_sec=timeout_sec,
                workspace_root=workspace.root / "runs",
            )
            if not simulation.output_path.is_file():
                raise RuntimeError("known-answer Qucs dataset missing")
        else:
            netlist_text = build_hb_netlist(
                ["Rdut in out 10"],
                fundamentals_hz=[1e6],
                harmonics=2,
                input_power_dbm=-20.0,
            )
            output = run_xyce(
                netlist_text,
                workdir=workspace.root / "outputs",
                timeout_sec=timeout_sec,
            )
            if not output.is_file():
                raise RuntimeError("known-answer Xyce HB dataset missing")
        result["validated"] = True
        result["state"] = "validated"
        result["diagnostic"] = "known-answer simulation completed"
    except Exception as exc:
        result["diagnostic"] = f"known-answer validation failed: {exc}"
    _LAST_PROBES[normalized] = result
    return result


def qucs_capabilities() -> dict[str, Any]:
    return {
        backend: probe_qucs_backend(backend, validate=False) for backend in ("qucsator", "xyce")
    }
