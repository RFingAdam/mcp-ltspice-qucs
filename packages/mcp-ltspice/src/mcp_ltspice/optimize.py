"""Spec-driven filter optimization with E-series snap.

Wraps ``scipy.optimize.minimize`` (Nelder-Mead by default) over the
analytical S-parameter response of an LC ladder. The loss function only
penalizes negative spec margins so the optimizer stops once all
criteria are satisfied (instead of over-fitting one of them).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from scipy.optimize import minimize

from mcp_ltspice.analysis_context import (
    EvaluationMode,
    FilterAnalysisContext,
    FilterKind,
    build_spec_frequency_grid,
    evaluate_component_margins,
)
from mcp_ltspice.eval import FilterSpec
from mcp_ltspice.resource_budget import (
    MAX_COMPONENTS,
    MAX_FREQUENCY_POINTS,
    MAX_OPTIMIZER_ITERATIONS,
    require_work_budget,
)
from mcp_ltspice.synthesis import Topology
from mcp_ltspice.vendor_models import list_vendor_parts
from rf_mcp_common.ecomp import ESeries, snap_to_eseries
from rf_mcp_common.simulation_workspace import ProcessCancelledError


@dataclass
class OptimizeResult:
    initial_components: dict[str, float]
    optimized_components: dict[str, float]
    snapped_components: dict[str, float]
    initial_loss: float
    final_loss: float
    n_iterations: int
    converged: bool
    margins_initial: list[dict[str, Any]]
    margins_final: list[dict[str, Any]]
    analysis_context: dict[str, Any]
    estimated_objective_evaluations: int
    estimated_work_units: int


def _evaluate_loss(
    components: dict[str, float],
    spec: FilterSpec,
    *,
    context: FilterAnalysisContext,
    f_grid: np.ndarray,
    passband_weight: float = 5.0,
) -> tuple[float, list[dict[str, Any]]]:
    """Evaluate spec loss = weighted sum of (-margin) for failing criteria.

    Passband margins (IL + RL) get ``passband_weight`` × the weight of
    stopband margins. This biases the optimizer toward keeping passband
    healthy, which matches engineering intent: a filter that meets
    every stopband target but blows the insertion loss is useless.
    """
    evaluation = evaluate_component_margins(
        components,
        spec,
        context=context,
        f_grid=f_grid,
    )
    margins: list[dict[str, Any]] = []
    loss = 0.0
    for label, margin in evaluation.margins.items():
        margins.append(
            {
                "label": label,
                "margin_db": margin,
                "measured": evaluation.measured[label],
            }
        )
        if margin < 0:
            weight = passband_weight if label.startswith("Passband ") else 1.0
            loss += weight * (-margin)

    return loss, margins


def _snap_to_vendor(value: float, vendor: str, kind: Literal["L", "C"]) -> float:
    """Snap a continuous value to the nearest entry in the vendor's catalog."""
    catalog = list_vendor_parts(vendor)
    return min(catalog, key=lambda v: abs(v - value))


def optimize_filter(
    initial_components: dict[str, float],
    spec: FilterSpec | dict[str, Any],
    *,
    tune: list[str] | None = None,
    transmission_zeros: bool | None = None,
    kind: FilterKind | str = "lowpass",
    topology: Topology | str = Topology.SERIES_FIRST,
    z0: float = 50.0,
    evaluation_mode: EvaluationMode = "analytical",
    model_fidelity: str = "ideal_lumped",
    provenance: dict[str, Any] | None = None,
    component_substitution: dict[str, dict[str, Any]] | None = None,
    transmission_zeros_hz: list[float] | None = None,
    method: Literal["Nelder-Mead", "Powell", "L-BFGS-B"] = "Nelder-Mead",
    max_iter: int = 500,
    snap_series: ESeries | str | None = ESeries.E24,
    bound_to_vendor: bool = False,
    inductor_vendor: str = "coilcraft_0402hp",
    capacitor_vendor: str = "murata_gjm_c0g",
    passband_weight: float = 5.0,
    f_grid_npoints: int = 801,
    cancel_requested: Callable[[], bool] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> OptimizeResult:
    """Optimize component values to satisfy a filter spec.

    - ``initial_components``: starting refdes → value dict
    - ``spec``: FilterSpec or dict
    - ``tune``: refdes whitelist; if ``None`` all components are tuned
    - ``transmission_zeros``: True for elliptic-style ladders with LC traps

    Returns final snapped components + per-criterion margins before/after.
    """
    if isinstance(spec, dict):
        spec = FilterSpec.model_validate(spec)
    if not 1 <= max_iter <= MAX_OPTIMIZER_ITERATIONS:
        raise ValueError("max_iter must be in [1, 5,000]")
    if not 8 <= f_grid_npoints <= MAX_FREQUENCY_POINTS:
        raise ValueError("f_grid_npoints must be in [8, 10,000]")
    if not initial_components or len(initial_components) > MAX_COMPONENTS:
        raise ValueError(f"initial_components must contain between 1 and {MAX_COMPONENTS} entries")
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

    refs = list(initial_components.keys()) if tune is None else list(tune)
    unknown_refs = sorted(set(refs) - set(initial_components))
    if unknown_refs:
        raise ValueError(f"tune contains unknown components: {', '.join(unknown_refs)}")
    objective_evaluations = (
        (max(50, max_iter // 10) + 1) * 15 * max(1, len(refs)) if bound_to_vendor else max_iter
    )
    estimated_work_units = require_work_budget(
        evaluations=objective_evaluations,
        frequency_points=f_grid_npoints,
        label="optimization",
    )
    x0 = np.asarray([initial_components[r] for r in refs], dtype=float)

    f_grid = build_spec_frequency_grid(
        spec,
        npoints=f_grid_npoints,
        transmission_zeros_hz=transmission_zeros_hz or (),
    )

    initial_loss, margins_initial = _evaluate_loss(
        initial_components,
        spec,
        context=context,
        f_grid=f_grid,
    )
    callback_iterations = 0

    def _callback(*_args: Any) -> None:
        nonlocal callback_iterations
        callback_iterations += 1
        if progress is not None:
            progress(min(callback_iterations, max_iter), max_iter)
        if cancel_requested is not None and cancel_requested():
            raise ProcessCancelledError("optimization was cancelled")

    def _loss(x: np.ndarray) -> float:
        if np.any(x <= 0):
            return 1e6
        comps = dict(initial_components)
        for r, v in zip(refs, x, strict=True):
            comps[r] = float(v)
        loss, _ = _evaluate_loss(
            comps,
            spec,
            context=context,
            f_grid=f_grid,
            passband_weight=passband_weight,
        )
        return loss

    if bound_to_vendor:
        # Constrain the search to the convex hull of the vendor catalog
        # for each component. This stops the optimizer wandering outside
        # the realizable range (e.g. a 0.6 nH inductor when the smallest
        # 0402HP is 1 nH).
        from scipy.optimize import differential_evolution

        bounds: list[tuple[float, float]] = []
        for r in refs:
            kind = "L" if r.startswith("L") else "C"
            vendor = inductor_vendor if kind == "L" else capacitor_vendor
            catalog = list_vendor_parts(vendor)
            bounds.append((min(catalog), max(catalog)))

        de_res = differential_evolution(
            _loss,
            bounds,
            maxiter=max(50, max_iter // 10),
            popsize=15,
            seed=0,
            tol=1e-6,
            polish=True,
            callback=_callback,
        )
        res = de_res
    else:
        res = minimize(
            _loss,
            x0,
            method=method,
            options={
                "maxiter": max_iter,
                "xatol": 1e-15,
                "fatol": 1e-4,
                "adaptive": True,
            },
            callback=_callback,
        )
    optimized = dict(initial_components)
    for r, v in zip(refs, res.x, strict=True):
        optimized[r] = float(v)

    snapped = dict(optimized)
    if bound_to_vendor:
        # Snap each tuned component to the nearest vendor catalog value.
        # This guarantees the final values are actually purchasable, at
        # the cost of slightly worse spec margins than continuous opt.
        for r in refs:
            component_kind: Literal["L", "C"] = "L" if r.startswith("L") else "C"
            vendor = inductor_vendor if component_kind == "L" else capacitor_vendor
            snapped[r] = _snap_to_vendor(optimized[r], vendor, component_kind)
    elif snap_series is not None:
        for r, v in optimized.items():
            if r in refs:
                snapped[r] = snap_to_eseries(v, snap_series).snapped

    final_loss, margins_final = _evaluate_loss(
        snapped,
        spec,
        context=context,
        f_grid=f_grid,
    )

    return OptimizeResult(
        initial_components=initial_components,
        optimized_components=optimized,
        snapped_components=snapped,
        initial_loss=initial_loss,
        final_loss=final_loss,
        n_iterations=int(getattr(res, "nit", 0)),
        converged=bool(getattr(res, "success", False)),
        margins_initial=margins_initial,
        margins_final=margins_final,
        analysis_context=context.as_dict(),
        estimated_objective_evaluations=objective_evaluations,
        estimated_work_units=estimated_work_units,
    )
