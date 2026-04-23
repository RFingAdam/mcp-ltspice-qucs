"""Spec-driven filter optimization with E-series snap.

Wraps ``scipy.optimize.minimize`` (Nelder-Mead by default) over the
analytical S-parameter response of an LC ladder. The loss function only
penalizes negative spec margins so the optimizer stops once all
criteria are satisfied (instead of over-fitting one of them).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
from scipy.optimize import minimize

from mcp_ltspice.eval import FilterSpec
from mcp_ltspice.extract import (
    components_dict_to_elements,
    ladder_sparams_from_components,
)
from mcp_ltspice.vendor_models import list_vendor_parts
from rf_mcp_common.ecomp import ESeries, snap_to_eseries


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


def _evaluate_loss(
    components: dict[str, float],
    spec: FilterSpec,
    *,
    transmission_zeros: bool,
    f_grid: np.ndarray,
    z0: float,
    passband_weight: float = 5.0,
) -> tuple[float, list[dict[str, Any]]]:
    """Evaluate spec loss = weighted sum of (-margin) for failing criteria.

    Passband margins (IL + RL) get ``passband_weight`` × the weight of
    stopband margins. This biases the optimizer toward keeping passband
    healthy, which matches engineering intent: a filter that meets
    every stopband target but blows the insertion loss is useless.
    """
    elements = components_dict_to_elements(
        components, transmission_zeros=transmission_zeros, topology="series_first"
    )
    s = ladder_sparams_from_components(elements, f_grid, z0=z0)
    s21_db = 20 * np.log10(np.maximum(np.abs(s[:, 1, 0]), 1e-12))
    s11_db = 20 * np.log10(np.maximum(np.abs(s[:, 0, 0]), 1e-12))

    margins: list[dict[str, Any]] = []
    loss = 0.0

    pb = spec.passband
    pb_mask = (f_grid >= pb.f_start) & (f_grid <= pb.f_stop)
    if pb_mask.any():
        worst_il = float(-s21_db[pb_mask].min())
        worst_rl = float(-s11_db[pb_mask].max())
        il_margin = pb.il_max_db - worst_il
        rl_margin = worst_rl - pb.rl_min_db
        margins.append({"label": "Passband IL", "margin_db": il_margin, "measured": worst_il})
        margins.append({"label": "Passband RL", "margin_db": rl_margin, "measured": worst_rl})
        if il_margin < 0:
            loss += passband_weight * (-il_margin)
        if rl_margin < 0:
            loss += passband_weight * (-rl_margin)

    for tgt in spec.stopband_targets:
        if tgt.freq < f_grid.min() or tgt.freq > f_grid.max():
            continue
        s21_at = float(np.interp(tgt.freq, f_grid, s21_db))
        rejection = -s21_at
        margin = rejection - tgt.rejection_min_db
        margins.append({"label": tgt.label, "margin_db": margin, "measured": rejection})
        if margin < 0:
            loss += -margin

    return loss, margins


def _snap_to_vendor(value: float, vendor: str, kind: Literal["L", "C"]) -> float:
    """Snap a continuous value to the nearest entry in the vendor's catalog."""
    catalog = list_vendor_parts(vendor)
    return min(catalog, key=lambda v: abs(v - value))


def optimize_filter(
    initial_components: dict[str, float],
    spec: FilterSpec | dict,
    *,
    tune: list[str] | None = None,
    transmission_zeros: bool = True,
    z0: float = 50.0,
    method: Literal["Nelder-Mead", "Powell", "L-BFGS-B"] = "Nelder-Mead",
    max_iter: int = 500,
    snap_series: ESeries | str | None = ESeries.E24,
    bound_to_vendor: bool = False,
    inductor_vendor: str = "coilcraft_0402hp",
    capacitor_vendor: str = "murata_gjm_c0g",
    passband_weight: float = 5.0,
    f_grid_npoints: int = 801,
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

    refs = list(initial_components.keys()) if tune is None else list(tune)
    x0 = np.asarray([initial_components[r] for r in refs], dtype=float)

    pb = spec.passband
    span = max(
        pb.f_stop * 5, max((t.freq for t in spec.stopband_targets), default=pb.f_stop * 5) * 1.5
    )
    f_grid = np.geomspace(max(pb.f_start, 1e3), span, f_grid_npoints)

    initial_loss, margins_initial = _evaluate_loss(
        initial_components,
        spec,
        transmission_zeros=transmission_zeros,
        f_grid=f_grid,
        z0=z0,
    )

    def _loss(x: np.ndarray) -> float:
        if np.any(x <= 0):
            return 1e6
        comps = dict(initial_components)
        for r, v in zip(refs, x, strict=True):
            comps[r] = float(v)
        loss, _ = _evaluate_loss(
            comps,
            spec,
            transmission_zeros=transmission_zeros,
            f_grid=f_grid,
            z0=z0,
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
            kind = "L" if r.startswith("L") else "C"
            vendor = inductor_vendor if kind == "L" else capacitor_vendor
            snapped[r] = _snap_to_vendor(optimized[r], vendor, kind)  # type: ignore[arg-type]
    elif snap_series is not None:
        for r, v in optimized.items():
            if r in refs:
                snapped[r] = snap_to_eseries(v, snap_series).snapped

    final_loss, margins_final = _evaluate_loss(
        snapped,
        spec,
        transmission_zeros=transmission_zeros,
        f_grid=f_grid,
        z0=z0,
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
    )
