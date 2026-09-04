"""Executable readiness probes for SPICE backends."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mcp_ltspice.asc_io import generate_lpf_asc
from mcp_ltspice.runner import (
    Simulator,
    _needs_wine,
    bubblewrap_ready,
    find_ltspice,
    find_ngspice,
    find_wine,
    ltspice_first_run_pending,
    run_simulation,
)
from rf_mcp_common.simulation_workspace import (
    SimulationWorkspace,
    probe_executable_version,
    subprocess_environment,
)

_LAST_PROBES: dict[str, dict[str, Any]] = {}
SUPPORTED_ANALYSES = {
    "ltspice": ("ac", "transient", "dc", "noise"),
    "ngspice": ("ac", "transient", "dc", "noise"),
}


def probe_spice_backend(
    backend: str,
    *,
    validate: bool = True,
    timeout_sec: float = 20.0,
) -> dict[str, Any]:
    """Distinguish installed, launchable, and known-answer validated states."""
    try:
        selected = Simulator(backend)
    except ValueError as exc:
        raise ValueError("backend must be 'ltspice' or 'ngspice'") from exc
    executable = find_ngspice() if selected == Simulator.NGSPICE else find_ltspice()
    result: dict[str, Any] = {
        "backend": selected.value,
        "installed": executable is not None,
        "launchable": False,
        "validated": False,
        "state": "unavailable",
        "executable": str(executable) if executable is not None else None,
        "version": None,
        "supported_analyses": list(SUPPORTED_ANALYSES[selected.value]),
        "last_probe_time": datetime.now(UTC).isoformat(),
        "diagnostic": None,
        "sandbox_profile": (
            {
                "name": "bubblewrap-no-network",
                "available": bubblewrap_ready(),
            }
            if selected == Simulator.NGSPICE
            else {
                "name": None,
                "available": False,
                "diagnostic": "No verified LTspice/Wine sandbox profile.",
            }
        ),
    }
    if executable is None:
        result["diagnostic"] = f"{selected.value} executable not found"
        _LAST_PROBES[selected.value] = result
        return result

    environment = subprocess_environment({"LTSPICE_PATH", "WINEPREFIX", "MCP_LTSPICE_SIMULATOR"})
    result["version"] = probe_executable_version(
        executable, environment=environment, timeout_sec=min(timeout_sec, 5.0)
    )
    if selected == Simulator.LTSPICE:
        # _needs_wine() rather than a bare '.exe' test: on native Windows a .exe
        # launches directly, and requiring Wine there reported the platform
        # LTspice actually ships for as unavailable. run_simulation() already
        # gates on _needs_wine; this probe has to agree with it or it reports a
        # working install as broken.
        if _needs_wine(executable) and find_wine() is None:
            result["diagnostic"] = "Windows LTspice binary found but Wine is unavailable"
            _LAST_PROBES[selected.value] = result
            return result
        if ltspice_first_run_pending(executable):
            result["diagnostic"] = "LTspice first-run consent/configuration is incomplete"
            _LAST_PROBES[selected.value] = result
            return result
    result["launchable"] = True
    result["state"] = "launchable"
    if not validate:
        cached = _LAST_PROBES.get(selected.value)
        if cached and cached.get("validated"):
            result["validated"] = True
            result["state"] = "validated"
            result["last_validation_time"] = cached["last_probe_time"]
        _LAST_PROBES[selected.value] = result
        return result

    workspace = SimulationWorkspace.create(f"probe-{selected.value}")
    try:
        if selected == Simulator.NGSPICE:
            source = workspace.write_input_text(
                "known_answer.cir",
                ("V1 in 0 AC 1\nR1 in out 50\nR2 out 0 50\n.ac lin 3 1k 3k\n.save V(out)\n.end\n"),
            )
        else:
            source = generate_lpf_asc(
                {"L1": 10e-9, "C2": 10e-12, "L3": 10e-9},
                workspace.root / "inputs" / "known_answer.asc",
                f_start_hz=1e6,
                f_stop_hz=10e6,
                npoints_per_decade=3,
            )
        simulation = run_simulation(
            source,
            prefer=selected,
            timeout=timeout_sec,
            sandbox=False,
        )
        if not simulation.raw_path.is_file() or simulation.raw_path.stat().st_size == 0:
            raise RuntimeError("known-answer run produced no non-empty raw artifact")
        result["validated"] = True
        result["state"] = "validated"
        result["diagnostic"] = "known-answer simulation completed"
    except Exception as exc:
        result["diagnostic"] = f"known-answer validation failed: {exc}"
    _LAST_PROBES[selected.value] = result
    return result


def spice_capabilities() -> dict[str, Any]:
    """Cheap capability snapshot; preserves the last known validation state."""
    return {
        backend: probe_spice_backend(backend, validate=False) for backend in ("ltspice", "ngspice")
    }
