"""Parameter sweep, corner analysis, sensitivity analysis.

These tools are universally useful. Every other domain (analog,
power, RF, SI) relies on the same "what happens if I vary X" question.
The implementations here use the analytical S-parameter pipeline so
they run thousands of evaluations per second without spawning a
simulator.

Three primitives:

- :func:`parameter_sweep`: vary one or more component values across a
  user-defined grid and report the spec margin at each point.
- :func:`corner_analysis`: evaluate at named corners (e.g.
  worst-case-low / typical / worst-case-high), tabulating which
  criteria fail at each corner.
- :func:`sensitivity_analysis`: perturb each component by ±δ and
  measure ∂margin/∂x for each spec criterion. Ranks components by
  total influence so you know which ones to tighten the tolerance on.
"""

from __future__ import annotations

import itertools
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np

from mcp_ltspice.analysis_context import (
    EvaluationMode,
    FilterAnalysisContext,
    FilterKind,
    build_spec_frequency_grid,
    evaluate_component_margins,
)
from mcp_ltspice.eval import FilterSpec
from mcp_ltspice.resource_budget import (
    MAX_FREQUENCY_POINTS,
    MAX_INLINE_SWEEP_POINTS,
    MAX_SWEEP_POINTS,
    require_work_budget,
)
from mcp_ltspice.synthesis import Topology
from rf_mcp_common.simulation_workspace import ProcessCancelledError, SimulationWorkspace


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
    analysis_context: dict[str, Any]
    estimated_work_units: int
    points_artifact: str | None = None
    artifact_manifest: str | None = None


def _evaluate_margins(
    components: dict[str, float],
    spec: FilterSpec,
    *,
    context: FilterAnalysisContext,
    f_grid: np.ndarray,
) -> tuple[dict[str, float], str]:
    """Compute per-criterion margin in dB and overall pass/fail."""
    evaluation = evaluate_component_margins(
        components,
        spec,
        context=context,
        f_grid=f_grid,
    )
    return evaluation.margins, evaluation.overall


def _make_freq_grid(spec: FilterSpec, n: int = 401) -> np.ndarray:
    return build_spec_frequency_grid(spec, npoints=n)


def parameter_sweep(
    components: dict[str, float],
    sweep: dict[str, list[float]],
    spec: FilterSpec | dict[str, Any],
    *,
    z0: float = 50.0,
    transmission_zeros: bool | None = None,
    kind: FilterKind | str = "lowpass",
    topology: Topology | str = Topology.SERIES_FIRST,
    evaluation_mode: EvaluationMode = "analytical",
    model_fidelity: str = "ideal_lumped",
    provenance: dict[str, Any] | None = None,
    component_substitution: dict[str, dict[str, Any]] | None = None,
    transmission_zeros_hz: list[float] | None = None,
    f_grid_npoints: int = 401,
    max_points: int = 5_000,
    result_mode: Literal["auto", "inline", "artifact"] = "auto",
    artifact_parent: str | Path | None = None,
    cancel_requested: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> SweepResult:
    """Evaluate the spec across a Cartesian product of parameter values.

    ``sweep`` is a dict mapping refdes → list of values to try. The
    cartesian product of all listed values is evaluated. For a 2-D
    sweep of (L1: 5 values) × (C2: 7 values) you get 35 evaluation
    points.
    """
    if isinstance(spec, dict):
        spec = FilterSpec.model_validate(spec)
    if max_points < 1 or max_points > MAX_SWEEP_POINTS:
        raise ValueError("max_points must be in [1, 10,000]")
    if not 8 <= f_grid_npoints <= MAX_FREQUENCY_POINTS:
        raise ValueError("f_grid_npoints must be in [8, 10,000]")
    if not sweep or any(not values for values in sweep.values()):
        raise ValueError("sweep must contain at least one non-empty value grid")
    n_requested = math.prod(len(values) for values in sweep.values())
    if n_requested > max_points:
        raise ValueError(
            f"parameter sweep requests {n_requested} points, exceeding max_points={max_points}"
        )
    if result_mode == "inline" and n_requested > MAX_INLINE_SWEEP_POINTS:
        raise ValueError(
            f"inline sweep results are limited to {MAX_INLINE_SWEEP_POINTS} points; "
            "use result_mode='artifact' or 'auto'"
        )
    estimated_work_units = require_work_budget(
        evaluations=n_requested,
        frequency_points=f_grid_npoints,
        label="parameter sweep",
    )
    context = FilterAnalysisContext.create(
        kind=kind,
        topology=topology,
        z0=z0,
        transmission_zeros=transmission_zeros,
        evaluation_mode=evaluation_mode,
        model_fidelity=model_fidelity,
        provenance=provenance,
        component_substitution=component_substitution,
    )
    f_grid = build_spec_frequency_grid(
        spec,
        npoints=f_grid_npoints,
        transmission_zeros_hz=transmission_zeros_hz or (),
    )

    refs = list(sweep.keys())
    grids = [sweep[r] for r in refs]
    points: list[SweepPoint] = []
    use_artifact = result_mode == "artifact" or (
        result_mode == "auto" and n_requested > MAX_INLINE_SWEEP_POINTS
    )
    workspace = (
        SimulationWorkspace.create("parameter-sweep", parent=artifact_parent)
        if use_artifact
        else None
    )
    artifact = workspace.output_path("sweep_points.jsonl") if workspace is not None else None
    artifact_handle = artifact.open("w", encoding="utf-8") if artifact is not None else None

    n_pass = 0
    try:
        for index, combo in enumerate(itertools.product(*grids), start=1):
            if cancel_requested is not None and cancel_requested():
                raise ProcessCancelledError("parameter sweep was cancelled")
            sampled = dict(components)
            for r, v in zip(refs, combo, strict=True):
                sampled[r] = float(v)
            margins, overall = _evaluate_margins(
                sampled,
                spec,
                context=context,
                f_grid=f_grid,
            )
            point = SweepPoint(
                parameters=dict(zip(refs, combo, strict=True)),
                margins=margins,
                overall=overall,
            )
            n_pass += int(overall == "pass")
            if artifact_handle is None:
                points.append(point)
            else:
                artifact_handle.write(
                    json.dumps(
                        {
                            "parameters": point.parameters,
                            "margins": point.margins,
                            "overall": point.overall,
                        }
                    )
                    + "\n"
                )
            if progress is not None and (
                index == n_requested or index % min(100, n_requested) == 0
            ):
                progress(index, n_requested)
    finally:
        if artifact_handle is not None:
            artifact_handle.close()
    if workspace is not None and artifact is not None:
        workspace.record_artifact(artifact, role="sweep_points")
        workspace.complete(returncode=0)
    return SweepResult(
        n_points=n_requested,
        n_passing=n_pass,
        yield_pct=100.0 * n_pass / n_requested,
        points=points,
        analysis_context=context.as_dict(),
        estimated_work_units=estimated_work_units,
        points_artifact=str(artifact) if artifact is not None else None,
        artifact_manifest=(str(workspace.manifest_path) if workspace is not None else None),
    )


def corner_analysis(
    components: dict[str, float],
    corners: dict[str, dict[str, float]],
    spec: FilterSpec | dict[str, Any],
    *,
    z0: float = 50.0,
    transmission_zeros: bool | None = None,
    kind: FilterKind | str = "lowpass",
    topology: Topology | str = Topology.SERIES_FIRST,
    evaluation_mode: EvaluationMode = "analytical",
    model_fidelity: str = "ideal_lumped",
    provenance: dict[str, Any] | None = None,
    component_substitution: dict[str, dict[str, Any]] | None = None,
    transmission_zeros_hz: list[float] | None = None,
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
    context = FilterAnalysisContext.create(
        kind=kind,
        topology=topology,
        z0=z0,
        transmission_zeros=transmission_zeros,
        evaluation_mode=evaluation_mode,
        model_fidelity=model_fidelity,
        provenance=provenance,
        component_substitution=component_substitution,
    )
    f_grid = build_spec_frequency_grid(
        spec,
        npoints=f_grid_npoints,
        transmission_zeros_hz=transmission_zeros_hz or (),
    )

    out: dict[str, Any] = {}
    failing_corners = 0
    for name, multipliers in corners.items():
        sampled = {ref: components[ref] * multipliers.get(ref, 1.0) for ref in components}
        margins, overall = _evaluate_margins(
            sampled,
            spec,
            context=context,
            f_grid=f_grid,
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
        "analysis_context": context.as_dict(),
    }


def sensitivity_analysis(
    components: dict[str, float],
    spec: FilterSpec | dict[str, Any],
    *,
    perturbation_pct: float = 1.0,
    z0: float = 50.0,
    transmission_zeros: bool | None = None,
    kind: FilterKind | str = "lowpass",
    topology: Topology | str = Topology.SERIES_FIRST,
    evaluation_mode: EvaluationMode = "analytical",
    model_fidelity: str = "ideal_lumped",
    provenance: dict[str, Any] | None = None,
    component_substitution: dict[str, dict[str, Any]] | None = None,
    transmission_zeros_hz: list[float] | None = None,
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
    context = FilterAnalysisContext.create(
        kind=kind,
        topology=topology,
        z0=z0,
        transmission_zeros=transmission_zeros,
        evaluation_mode=evaluation_mode,
        model_fidelity=model_fidelity,
        provenance=provenance,
        component_substitution=component_substitution,
    )
    f_grid = build_spec_frequency_grid(
        spec,
        npoints=f_grid_npoints,
        transmission_zeros_hz=transmission_zeros_hz or (),
    )
    delta = perturbation_pct / 100.0

    nominal_margins, _ = _evaluate_margins(
        components,
        spec,
        context=context,
        f_grid=f_grid,
    )

    sensitivities: list[dict[str, Any]] = []
    for ref, value in components.items():
        # +δ
        plus_comps = dict(components)
        plus_comps[ref] = value * (1 + delta)
        plus_margins, _ = _evaluate_margins(
            plus_comps,
            spec,
            context=context,
            f_grid=f_grid,
        )
        # -δ
        minus_comps = dict(components)
        minus_comps[ref] = value * (1 - delta)
        minus_margins, _ = _evaluate_margins(
            minus_comps,
            spec,
            context=context,
            f_grid=f_grid,
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
        "analysis_context": context.as_dict(),
    }
