"""Reconcile a SPICE-simulated response against the analytical preview.

The whole pipeline — synthesise, place zeros, substitute real vendor parts,
optimise, Monte Carlo — can run end to end on the closed-form
``ladder_sparams_from_components`` path without a single SPICE run. That is
the right default for a fast inner loop, but it silently drops everything a
real simulation captures: vendor ``.lib`` subcircuits, DC-bias and
temperature behaviour, ground and supply non-idealities, and measured vendor
S-parameters pulled in via ``.include``.

:func:`validate_against_spice` closes that gap. It runs the schematic through
a real simulator, extracts the S-parameters, computes the analytical
response for the same components, and reports where the two diverge — so a
reported yield number can be backed by an actual SPICE run rather than a
lumped-element approximation of one.

Passband and stopband are told apart by the analytical response itself
(``|S21|`` above vs below a boundary), so no external spec is required; the
divergence that matters is judged against the appropriate threshold for each.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np
import skrf as rf
from numpy.typing import NDArray

from mcp_ltspice.extract import (
    components_dict_to_elements,
    extract_sparams_from_raw,
    ladder_sparams_from_components,
)
from mcp_ltspice.runner import Simulator, detect_simulator, run_simulation
from rf_mcp_common.logging import get_logger

log = get_logger("mcp_ltspice.validate")


class Verdict(StrEnum):
    AGREE = "agree"
    MINOR_DISAGREEMENT = "minor_disagreement"
    DISAGREE = "disagree"
    SPICE_UNAVAILABLE = "spice_unavailable"


@dataclass
class ValidationResult:
    verdict: Verdict
    analytical_network: rf.Network | None = None
    spice_network: rf.Network | None = None
    freq_hz: NDArray[np.float64] | None = None
    delta_s21_db: NDArray[np.float64] | None = None
    delta_phase_deg: NDArray[np.float64] | None = None
    max_delta_passband_db: float | None = None
    max_delta_stopband_db: float | None = None
    flagged_regions: list[dict[str, float]] = field(default_factory=list)
    simulator: str | None = None
    note: str | None = None


def _phase_deg(s: NDArray[np.complex128]) -> NDArray[np.float64]:
    return np.asarray(np.degrees(np.unwrap(np.angle(s))))


def _flag_regions(
    freq_hz: NDArray[np.float64],
    exceeded: NDArray[np.bool_],
    delta_db: NDArray[np.float64],
) -> list[dict[str, float]]:
    """Coalesce over-threshold points into contiguous frequency spans."""
    regions: list[dict[str, float]] = []
    start: int | None = None
    for i, over in enumerate(exceeded):
        if over and start is None:
            start = i
        elif not over and start is not None:
            regions.append(_region(freq_hz, delta_db, start, i - 1))
            start = None
    if start is not None:
        regions.append(_region(freq_hz, delta_db, start, len(exceeded) - 1))
    return regions


def _region(
    freq_hz: NDArray[np.float64], delta_db: NDArray[np.float64], lo: int, hi: int
) -> dict[str, float]:
    span = slice(lo, hi + 1)
    worst = lo + int(np.argmax(np.abs(delta_db[span])))
    return {
        "f_start_hz": float(freq_hz[lo]),
        "f_stop_hz": float(freq_hz[hi]),
        "worst_freq_hz": float(freq_hz[worst]),
        "worst_delta_db": float(delta_db[worst]),
    }


def analytical_network(
    components: dict[str, float],
    freq_hz: NDArray[np.float64],
    *,
    topology: str = "series_first",
    kind: str = "lowpass",
    z0: float = 50.0,
) -> rf.Network:
    """Closed-form response for ``components`` on the given frequency grid."""
    elements = components_dict_to_elements(components, topology=topology, kind=kind)
    s = ladder_sparams_from_components(elements, freq_hz, z0=z0)
    return rf.Network(
        frequency=rf.Frequency.from_f(freq_hz, unit="Hz"), s=s, z0=z0, name="analytical"
    )


def validate_against_spice(
    asc_path: str | Path,
    components: dict[str, float],
    *,
    topology: str = "series_first",
    kind: str = "lowpass",
    z0: float = 50.0,
    passband_boundary_db: float = -3.0,
    passband_threshold_db: float = 0.5,
    stopband_threshold_db: float = 3.0,
    prefer: Simulator | str | None = None,
    port_map: dict[int, str] | None = None,
    timeout_sec: float = 120.0,
) -> ValidationResult:
    """Run SPICE on ``asc_path`` and reconcile it against the analytical S2P.

    ``components`` / ``topology`` / ``kind`` must describe the same circuit
    the ``.asc`` draws; they drive the analytical comparison. Passband and
    stopband are separated by ``passband_boundary_db`` on the analytical
    ``|S21|``, and each is judged against its own threshold.

    If no simulator is installed the analytical network is still returned
    with ``verdict = spice_unavailable`` rather than raising, so a caller can
    fall back to the preview.
    """
    if isinstance(prefer, str):
        prefer = Simulator(prefer)
    port_map = port_map or {1: "p1", 2: "p2"}

    if detect_simulator() is None and prefer is None:
        # Nothing to run SPICE with. Report the analytical view over a default
        # grid so the caller still gets an answer.
        grid = np.logspace(6, 10, 401)
        return ValidationResult(
            verdict=Verdict.SPICE_UNAVAILABLE,
            analytical_network=analytical_network(
                components, grid, topology=topology, kind=kind, z0=z0
            ),
            note="No SPICE simulator found; returning the analytical response only.",
        )

    try:
        run = run_simulation(asc_path, prefer=prefer, timeout=timeout_sec)
    except Exception as e:
        grid = np.logspace(6, 10, 401)
        return ValidationResult(
            verdict=Verdict.SPICE_UNAVAILABLE,
            analytical_network=analytical_network(
                components, grid, topology=topology, kind=kind, z0=z0
            ),
            note=f"SPICE run failed ({e}); returning the analytical response only.",
        )

    spice = extract_sparams_from_raw(run.raw_path, port_map=port_map, z0=z0)
    freq = np.asarray(spice.f, dtype=np.float64)
    analytical = analytical_network(components, freq, topology=topology, kind=kind, z0=z0)

    a_s21 = analytical.s[:, 1, 0]
    s_s21 = spice.s[:, 1, 0]
    a_s21_db = 20.0 * np.log10(np.maximum(np.abs(a_s21), 1e-12))
    s_s21_db = 20.0 * np.log10(np.maximum(np.abs(s_s21), 1e-12))

    delta_db = s_s21_db - a_s21_db
    delta_phase = _phase_deg(s_s21) - _phase_deg(a_s21)

    passband = a_s21_db >= passband_boundary_db
    stopband = ~passband

    max_pass = float(np.max(np.abs(delta_db[passband]))) if passband.any() else 0.0
    max_stop = float(np.max(np.abs(delta_db[stopband]))) if stopband.any() else 0.0

    exceeded = np.zeros_like(delta_db, dtype=bool)
    exceeded[passband] = np.abs(delta_db[passband]) > passband_threshold_db
    exceeded[stopband] = np.abs(delta_db[stopband]) > stopband_threshold_db
    flagged = _flag_regions(freq, exceeded, delta_db)

    # Verdict: clean agreement, a marginal miss (within 2x threshold, or only
    # in the stopband where deep nulls are numerically touchy), or a real
    # disagreement in the passband.
    pass_over = max_pass > passband_threshold_db
    stop_over = max_stop > stopband_threshold_db
    if not pass_over and not stop_over:
        verdict = Verdict.AGREE
    elif pass_over and max_pass > 2.0 * passband_threshold_db:
        verdict = Verdict.DISAGREE
    else:
        verdict = Verdict.MINOR_DISAGREEMENT

    return ValidationResult(
        verdict=verdict,
        analytical_network=analytical,
        spice_network=spice,
        freq_hz=freq,
        delta_s21_db=delta_db,
        delta_phase_deg=delta_phase,
        max_delta_passband_db=max_pass,
        max_delta_stopband_db=max_stop,
        flagged_regions=flagged,
        simulator=run.simulator.value,
    )


def result_to_payload(result: ValidationResult, *, top_n_points: int = 0) -> dict[str, Any]:
    """Flatten a :class:`ValidationResult` for an MCP envelope."""
    payload: dict[str, Any] = {
        "verdict": result.verdict.value,
        "simulator": result.simulator,
        "max_delta_passband_db": result.max_delta_passband_db,
        "max_delta_stopband_db": result.max_delta_stopband_db,
        "flagged_regions": result.flagged_regions,
    }
    if result.note:
        payload["note"] = result.note
    if (
        top_n_points
        and result.freq_hz is not None
        and result.delta_s21_db is not None
        and result.delta_phase_deg is not None
    ):
        freq_hz = result.freq_hz
        delta_db = result.delta_s21_db
        delta_phase = result.delta_phase_deg
        worst = np.argsort(np.abs(delta_db))[::-1][:top_n_points]
        payload["worst_points"] = [
            {
                "freq_hz": float(freq_hz[i]),
                "delta_s21_db": float(delta_db[i]),
                "delta_phase_deg": float(delta_phase[i]),
            }
            for i in sorted(worst)
        ]
    return payload
