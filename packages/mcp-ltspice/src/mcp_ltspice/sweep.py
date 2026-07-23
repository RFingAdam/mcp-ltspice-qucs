"""Parameter sweep, corner analysis, sensitivity analysis.

These tools are universally useful — every other domain (analog,
power, RF, SI) relies on the same "what happens if I vary X" question.
The implementations here use the analytical S-parameter pipeline so
they run thousands of evaluations per second without spawning a
simulator.

Three primitives:

- :func:`parameter_sweep` — vary one or more component values across a
  user-defined grid and report the spec margin at each point.
- :func:`corner_analysis` — evaluate at named corners (e.g.
  worst-case-low / typical / worst-case-high), tabulating which
  criteria fail at each corner.
- :func:`sensitivity_analysis` — perturb each component by ±δ and
  measure ∂margin/∂x for each spec criterion. Ranks components by
  total influence so you know which ones to tighten the tolerance on.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any

import numpy as np

from mcp_ltspice.eval import FilterSpec
from mcp_ltspice.extract import (
    components_dict_to_elements,
    ladder_sparams_from_components,
)


@dataclass
class SweepPoint:
    """One evaluation in a parameter sweep."""

    parameters: dict[str, float]
    margins: dict[str, float]
    overall: str  # "pass" | "fail"


@dataclass
class SweepResult:
    n_points: int
    n_passing: int
    yield_pct: float
    points: list[SweepPoint]


def _evaluate_margins(
    components: dict[str, float],
    spec: FilterSpec,
    *,
    transmission_zeros: bool,
    f_grid: np.ndarray,
    z0: float,
) -> tuple[dict[str, float], str]:
    """Compute per-criterion margin in dB and overall pass/fail."""
    elements = components_dict_to_elements(
        components, transmission_zeros=transmission_zeros, topology="series_first"
    )
    s = ladder_sparams_from_components(elements, f_grid, z0=z0)
    s21_db = 20 * np.log10(np.maximum(np.abs(s[:, 1, 0]), 1e-12))
    s11_db = 20 * np.log10(np.maximum(np.abs(s[:, 0, 0]), 1e-12))

    margins: dict[str, float] = {}
    pb = spec.passband
    pb_mask = (f_grid >= pb.f_start) & (f_grid <= pb.f_stop)
    if pb_mask.any():
        worst_il = float(-s21_db[pb_mask].min())
        worst_rl = float(-s11_db[pb_mask].max())
        margins["Passband IL"] = pb.il_max_db - worst_il
        margins["Passband RL"] = worst_rl - pb.rl_min_db

    for tgt in spec.stopband_targets:
        if tgt.freq < f_grid.min() or tgt.freq > f_grid.max():
            margins[tgt.label] = float("nan")
            continue
        s21_at = float(np.interp(tgt.freq, f_grid, s21_db))
        rejection = -s21_at
        margins[tgt.label] = rejection - tgt.rejection_min_db

    finite_margins = [m for m in margins.values() if np.isfinite(m)]
    overall = "pass" if all(m >= 0 for m in finite_margins) else "fail"
    return margins, overall


def _make_freq_grid(spec: FilterSpec, n: int = 401) -> np.ndarray:
    pb = spec.passband
    span = max(
        pb.f_stop * 5,
        max((t.freq for t in spec.stopband_targets), default=pb.f_stop * 5) * 1.5,
    )
    return np.geomspace(max(pb.f_start, 1e3), span, n)


def parameter_sweep(
    components: dict[str, float],
    sweep: dict[str, list[float]],
    spec: FilterSpec | dict[str, Any],
    *,
    z0: float = 50.0,
    transmission_zeros: bool = True,
    f_grid_npoints: int = 401,
) -> SweepResult:
    """Evaluate the spec across a Cartesian product of parameter values.

    ``sweep`` is a dict mapping refdes → list of values to try. The
    cartesian product of all listed values is evaluated. For a 2-D
    sweep of (L1: 5 values) × (C2: 7 values) you get 35 evaluation
    points.
    """
    if isinstance(spec, dict):
        spec = FilterSpec.model_validate(spec)
    f_grid = _make_freq_grid(spec, f_grid_npoints)

    refs = list(sweep.keys())
    grids = [sweep[r] for r in refs]
    points: list[SweepPoint] = []

    for combo in itertools.product(*grids):
        sampled = dict(components)
        for r, v in zip(refs, combo, strict=True):
            sampled[r] = float(v)
        margins, overall = _evaluate_margins(
            sampled,
            spec,
            transmission_zeros=transmission_zeros,
            f_grid=f_grid,
            z0=z0,
        )
        points.append(
            SweepPoint(
                parameters=dict(zip(refs, combo, strict=True)),
                margins=margins,
                overall=overall,
            )
        )

    n_pass = sum(1 for p in points if p.overall == "pass")
    return SweepResult(
        n_points=len(points),
        n_passing=n_pass,
        yield_pct=100.0 * n_pass / len(points) if points else 0.0,
        points=points,
    )


def corner_analysis(
    components: dict[str, float],
    corners: dict[str, dict[str, float]],
    spec: FilterSpec | dict[str, Any],
    *,
    z0: float = 50.0,
    transmission_zeros: bool = True,
    f_grid_npoints: int = 401,
) -> dict[str, Any]:
    """Evaluate the spec at named corners.

    Each corner is a dict of refdes → multiplier (e.g. ``{"L1": 0.95,
    "C2": 1.05}`` shifts L1 by -5% and C2 by +5%). A typical use:

    .. code-block:: python

        corners = {
            "TT": {ref: 1.0 for ref in components},      # typical
            "SS": {ref: 0.95 for ref in components},     # all -5%
            "FF": {ref: 1.05 for ref in components},     # all +5%
            "Worst RL": {"L1": 1.05, "L3": 0.95, "C2": 1.05},  # specific stress
        }
    """
    if isinstance(spec, dict):
        spec = FilterSpec.model_validate(spec)
    f_grid = _make_freq_grid(spec, f_grid_npoints)

    out: dict[str, Any] = {}
    failing_corners = 0
    for name, multipliers in corners.items():
        sampled = {ref: components[ref] * multipliers.get(ref, 1.0) for ref in components}
        margins, overall = _evaluate_margins(
            sampled,
            spec,
            transmission_zeros=transmission_zeros,
            f_grid=f_grid,
            z0=z0,
        )
        out[name] = {
            "components": sampled,
            "margins": margins,
            "overall": overall,
        }
        if overall == "fail":
            failing_corners += 1

    return {
        "n_corners": len(corners),
        "n_failing_corners": failing_corners,
        "all_corners_pass": failing_corners == 0,
        "results": out,
    }


def sensitivity_analysis(
    components: dict[str, float],
    spec: FilterSpec | dict[str, Any],
    *,
    perturbation_pct: float = 1.0,
    z0: float = 50.0,
    transmission_zeros: bool = True,
    f_grid_npoints: int = 401,
) -> dict[str, Any]:
    """For each component, perturb by ±perturbation_pct and measure
    the change in each spec margin.

    Returns a sorted list of {component, criterion, sensitivity_db_per_pct}
    where the most influential (component, criterion) pairs come first.
    Use this to decide which components need tight tolerance grading
    and which can be loose.
    """
    if isinstance(spec, dict):
        spec = FilterSpec.model_validate(spec)
    f_grid = _make_freq_grid(spec, f_grid_npoints)
    delta = perturbation_pct / 100.0

    nominal_margins, _ = _evaluate_margins(
        components,
        spec,
        transmission_zeros=transmission_zeros,
        f_grid=f_grid,
        z0=z0,
    )

    sensitivities: list[dict[str, Any]] = []
    for ref, value in components.items():
        # +δ
        plus_comps = dict(components)
        plus_comps[ref] = value * (1 + delta)
        plus_margins, _ = _evaluate_margins(
            plus_comps,
            spec,
            transmission_zeros=transmission_zeros,
            f_grid=f_grid,
            z0=z0,
        )
        # -δ
        minus_comps = dict(components)
        minus_comps[ref] = value * (1 - delta)
        minus_margins, _ = _evaluate_margins(
            minus_comps,
            spec,
            transmission_zeros=transmission_zeros,
            f_grid=f_grid,
            z0=z0,
        )

        for crit in nominal_margins:
            if not (np.isfinite(plus_margins[crit]) and np.isfinite(minus_margins[crit])):
                continue
            # Central-difference sensitivity in dB per %
            sens = (plus_margins[crit] - minus_margins[crit]) / (2 * perturbation_pct)
            sensitivities.append(
                {
                    "component": ref,
                    "criterion": crit,
                    "sensitivity_db_per_pct": sens,
                    "abs_sensitivity": abs(sens),
                    "nominal_value": value,
                }
            )

    sensitivities.sort(key=lambda d: -d["abs_sensitivity"])
    # Aggregate per-component (sum of |sensitivities| across criteria)
    per_component: dict[str, float] = {}
    for s in sensitivities:
        per_component[s["component"]] = (
            per_component.get(s["component"], 0.0) + s["abs_sensitivity"]
        )
    ranked_components = sorted(per_component.items(), key=lambda kv: -kv[1])
    return {
        "perturbation_pct": perturbation_pct,
        "nominal_margins": nominal_margins,
        "ranked_sensitivities": sensitivities,
        "per_component_total_sensitivity": dict(ranked_components),
        "most_influential_component": ranked_components[0][0] if ranked_components else None,
    }
