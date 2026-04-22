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
from rf_mcp_common.ecomp import ESeries, snap_to_eseries
from scipy.optimize import minimize

from mcp_ltspice.eval import FilterSpec
from mcp_ltspice.extract import (
    components_dict_to_elements,
    ladder_sparams_from_components,
)


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
) -> tuple[float, list[dict[str, Any]]]:
    """Evaluate spec loss = sum of (-margin) for failing criteria only."""
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
            loss += -il_margin
        if rl_margin < 0:
            loss += -rl_margin

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
    span = max(pb.f_stop * 5, max((t.freq for t in spec.stopband_targets), default=pb.f_stop * 5) * 1.5)
    f_grid = np.geomspace(max(pb.f_start, 1e3), span, f_grid_npoints)

    initial_loss, margins_initial = _evaluate_loss(
        initial_components, spec,
        transmission_zeros=transmission_zeros, f_grid=f_grid, z0=z0,
    )

    def _loss(x: np.ndarray) -> float:
        if np.any(x <= 0):
            return 1e6
        comps = dict(initial_components)
        for r, v in zip(refs, x, strict=True):
            comps[r] = float(v)
        loss, _ = _evaluate_loss(
            comps, spec, transmission_zeros=transmission_zeros, f_grid=f_grid, z0=z0,
        )
        return loss

    res = minimize(
        _loss, x0, method=method,
        options={"maxiter": max_iter, "xatol": 1e-15, "fatol": 1e-4, "adaptive": True},
    )
    optimized = dict(initial_components)
    for r, v in zip(refs, res.x, strict=True):
        optimized[r] = float(v)

    snapped = dict(optimized)
    if snap_series is not None:
        for r, v in optimized.items():
            if r in refs:
                snapped[r] = snap_to_eseries(v, snap_series).snapped

    final_loss, margins_final = _evaluate_loss(
        snapped, spec, transmission_zeros=transmission_zeros, f_grid=f_grid, z0=z0,
    )

    return OptimizeResult(
        initial_components=initial_components,
        optimized_components=optimized,
        snapped_components=snapped,
        initial_loss=initial_loss,
        final_loss=final_loss,
        n_iterations=int(res.nit),
        converged=bool(res.success),
        margins_initial=margins_initial,
        margins_final=margins_final,
    )
