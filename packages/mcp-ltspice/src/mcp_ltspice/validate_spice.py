"""Run actual SPICE on a substituted-real-vendor schematic and reconcile
against the analytical S2P preview.

The analytical path (:func:`extract.ladder_sparams_from_components`) is
fast and good for inner loops, but it only models the ABCD chain of
ideal Ls and Cs plus the curated parasitic Cp/Ls/Rs from
:mod:`vendor_models`. It does **not** model:

- Vendor SPICE subcircuits with full DC-bias / temperature / non-linear
  behaviour (Murata GRM/GJM C0G is mostly linear, but X7R / X8R is not).
- Ground-coupling and supply non-idealities.
- Real S-parameter files (Touchstone) loaded via SPICE ``.include``.

When real-vendor data is wired into the schematic, only an actual SPICE
run captures the difference between curated and real. This module
provides a single entry point that runs SPICE, extracts the resulting
S2P, computes the analytical S2P from the same components, and returns
the per-frequency Δ|S21| / Δphase plus a verdict.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import skrf as rf

from mcp_ltspice.extract import (
    components_dict_to_elements,
    extract_sparams_from_raw,
    ladder_sparams_from_components,
)
from mcp_ltspice.runner import RunResult, Simulator, run_simulation
from rf_mcp_common.touchstone import network_to_touchstone, read_touchstone


def _interp_complex(
    f_target: np.ndarray, f_src: np.ndarray, s_src: np.ndarray
) -> np.ndarray:
    """Linear interpolation in real / imaginary space (skrf's default)."""
    real = np.interp(f_target, f_src, s_src.real)
    imag = np.interp(f_target, f_src, s_src.imag)
    return real + 1j * imag


def _delta_db_phase(
    f: np.ndarray, s_a: np.ndarray, s_b: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Return |Δ| in dB and Δphase in degrees on the same freq axis."""
    mag_a = np.abs(s_a)
    mag_b = np.abs(s_b)
    eps = 1e-15
    delta_db = 20.0 * np.log10(np.maximum(mag_a, eps) / np.maximum(mag_b, eps))
    delta_phase = np.degrees(np.angle(s_a) - np.angle(s_b))
    delta_phase = ((delta_phase + 180.0) % 360.0) - 180.0
    return delta_db, delta_phase


def validate_against_spice(
    asc_path: str | Path,
    components: dict[str, float],
    *,
    spec: dict | None = None,
    output_spice_s2p: str | Path | None = None,
    output_analytical_s2p: str | Path | None = None,
    passband_threshold_db: float = 0.5,
    stopband_threshold_db: float = 3.0,
    prefer: str | None = None,
    timeout_sec: float = 180.0,
    z0: float = 50.0,
    f_eval: tuple[float, float, int] = (1e6, 5e9, 801),
    topology: str = "series_first",
) -> dict[str, Any]:
    """Run SPICE on ``asc_path`` and compare to the analytical S2P.

    Parameters
    ----------
    asc_path
        Schematic to simulate. Must have nodes ``p1`` (input, port 1)
        and ``p2`` (output, port 2) — standard for schematics emitted
        by :func:`asc_io.generate_lpf_asc`.
    components
        Component values used to compute the analytical reference S2P.
    spec
        Optional FilterSpec dict. If provided, ``passband.f_start /
        f_stop`` define the passband region for threshold checking; the
        rest of the sweep is treated as stopband. Without a spec, the
        whole sweep uses ``stopband_threshold_db``.
    output_spice_s2p, output_analytical_s2p
        Optional Touchstone output paths.
    passband_threshold_db, stopband_threshold_db
        Δ|S21| thresholds for the verdict. Default ≤ 0.5 dB passband,
        ≤ 3 dB stopband.
    prefer
        Force ``"ltspice"`` or ``"ngspice"``. Default: auto-detect.
    timeout_sec
        SPICE invocation timeout.
    z0
        Reference impedance.
    f_eval
        ``(start_hz, stop_hz, n_points)`` for analytical evaluation. The
        analytical S2P is interpolated to the SPICE frequency grid before
        comparison.
    topology
        Ladder topology; passes through to :func:`components_dict_to_elements`.

    Returns
    -------
    dict
        ``spice_s2p_path``, ``analytical_s2p_path``, per-frequency Δ
        arrays summarised as max/mean dB & phase, ``verdict`` ∈
        ``{"agree", "minor_disagreement", "disagree", "spice_unavailable"}``,
        and ``flagged_regions`` describing where Δ|S21| exceeds threshold.
    """
    asc_path = Path(asc_path).expanduser().resolve()
    if not asc_path.exists():
        raise FileNotFoundError(f"Schematic not found: {asc_path}")

    # 1. Run SPICE — fall back gracefully if no simulator is installed.
    try:
        prefer_enum = Simulator(prefer) if prefer else None
        result: RunResult = run_simulation(asc_path, prefer=prefer_enum, timeout=timeout_sec)
    except Exception as exc:  # pragma: no cover - exercised in env without sim
        # Compute analytical-only S2P so the caller still has something.
        f_axis = np.geomspace(*f_eval) if f_eval[0] > 0 else np.linspace(*f_eval)
        elements = components_dict_to_elements(components, topology=topology)
        s_anal = ladder_sparams_from_components(elements, f_axis, z0=z0)
        anal_path: Path | None = None
        if output_analytical_s2p:
            anal_path = network_to_touchstone(
                f_axis, s_anal, Path(output_analytical_s2p), z0=z0
            )
        return {
            "verdict": "spice_unavailable",
            "spice_error": str(exc),
            "spice_s2p_path": None,
            "analytical_s2p_path": str(anal_path) if anal_path else None,
            "max_delta_db": float("nan"),
            "max_delta_phase_deg": float("nan"),
            "max_delta_passband_db": float("nan"),
            "max_delta_stopband_db": float("nan"),
            "flagged_regions": [],
            "n_freq_points": int(f_axis.size),
        }

    # 2. Extract SPICE → S2P on the SPICE frequency grid.
    if output_spice_s2p is None:
        output_spice_s2p = asc_path.with_suffix(".spice.s2p")
    spice_net: rf.Network = extract_sparams_from_raw(
        result.raw_path, port_map={1: "p1", 2: "p2"}, z0=z0
    )
    spice_s2p_path = network_to_touchstone(
        spice_net.f, spice_net.s, Path(output_spice_s2p), z0=z0
    )

    # 3. Compute analytical S2P at the SPICE frequencies (or a chosen grid).
    f_axis = spice_net.f.copy()
    elements = components_dict_to_elements(components, topology=topology)
    s_anal = ladder_sparams_from_components(elements, f_axis, z0=z0)
    if output_analytical_s2p is None:
        output_analytical_s2p = asc_path.with_suffix(".analytical.s2p")
    analytical_s2p_path = network_to_touchstone(
        f_axis, s_anal, Path(output_analytical_s2p), z0=z0
    )

    # 4. Compute deltas.
    delta_s21_db, delta_s21_phase = _delta_db_phase(
        f_axis, spice_net.s[:, 1, 0], s_anal[:, 1, 0]
    )
    delta_s11_db, _ = _delta_db_phase(
        f_axis, spice_net.s[:, 0, 0], s_anal[:, 0, 0]
    )

    # 5. Region threshold check.
    if spec is not None and "passband" in spec:
        pb_lo = float(spec["passband"]["f_start"])
        pb_hi = float(spec["passband"]["f_stop"])
        pb_mask = (f_axis >= pb_lo) & (f_axis <= pb_hi)
        sb_mask = ~pb_mask
    else:
        pb_mask = np.zeros_like(f_axis, dtype=bool)
        sb_mask = np.ones_like(f_axis, dtype=bool)

    max_delta_pb = (
        float(np.nanmax(np.abs(delta_s21_db[pb_mask]))) if pb_mask.any() else float("nan")
    )
    max_delta_sb = (
        float(np.nanmax(np.abs(delta_s21_db[sb_mask]))) if sb_mask.any() else float("nan")
    )
    max_delta_overall = float(np.nanmax(np.abs(delta_s21_db)))
    max_delta_phase = float(np.nanmax(np.abs(delta_s21_phase)))

    # 6. Flag continuous regions where threshold is breached.
    flagged_regions: list[dict[str, Any]] = []
    if pb_mask.any():
        flagged_regions.extend(
            _find_breach_regions(
                f_axis, np.abs(delta_s21_db), pb_mask, passband_threshold_db, "passband"
            )
        )
    if sb_mask.any():
        flagged_regions.extend(
            _find_breach_regions(
                f_axis, np.abs(delta_s21_db), sb_mask, stopband_threshold_db, "stopband"
            )
        )

    # 7. Verdict.
    pb_ok = (
        np.isnan(max_delta_pb) or max_delta_pb <= passband_threshold_db
    )
    sb_ok = (
        np.isnan(max_delta_sb) or max_delta_sb <= stopband_threshold_db
    )
    if pb_ok and sb_ok:
        verdict = "agree"
    elif (pb_mask.any() and max_delta_pb > 2 * passband_threshold_db) or (
        sb_mask.any() and max_delta_sb > 2 * stopband_threshold_db
    ):
        verdict = "disagree"
    else:
        verdict = "minor_disagreement"

    return {
        "verdict": verdict,
        "simulator": result.simulator.value,
        "spice_s2p_path": str(spice_s2p_path),
        "analytical_s2p_path": str(analytical_s2p_path),
        "n_freq_points": int(f_axis.size),
        "freq_range_hz": [float(f_axis[0]), float(f_axis[-1])],
        "max_delta_db": max_delta_overall,
        "max_delta_phase_deg": max_delta_phase,
        "max_delta_passband_db": max_delta_pb,
        "max_delta_stopband_db": max_delta_sb,
        "max_delta_s11_db": float(np.nanmax(np.abs(delta_s11_db))),
        "passband_threshold_db": passband_threshold_db,
        "stopband_threshold_db": stopband_threshold_db,
        "flagged_regions": flagged_regions,
    }


def _find_breach_regions(
    f: np.ndarray,
    delta_db: np.ndarray,
    region_mask: np.ndarray,
    threshold_db: float,
    region_label: str,
) -> list[dict[str, Any]]:
    """Walk a region-masked delta array and return contiguous breach intervals."""
    breach = (delta_db > threshold_db) & region_mask
    if not breach.any():
        return []
    out: list[dict[str, Any]] = []
    in_run = False
    run_start = 0
    for i in range(len(breach)):
        if breach[i] and not in_run:
            in_run = True
            run_start = i
        elif not breach[i] and in_run:
            in_run = False
            out.append(
                {
                    "region": region_label,
                    "f_low_hz": float(f[run_start]),
                    "f_high_hz": float(f[i - 1]),
                    "max_delta_db": float(delta_db[run_start:i].max()),
                    "threshold_db": threshold_db,
                }
            )
    if in_run:
        out.append(
            {
                "region": region_label,
                "f_low_hz": float(f[run_start]),
                "f_high_hz": float(f[-1]),
                "max_delta_db": float(delta_db[run_start:].max()),
                "threshold_db": threshold_db,
            }
        )
    return out
