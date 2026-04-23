"""Filter-order comparison: run synthesize → place zeros → vendor-snap →
optimize → MC for several orders, score them, and return the most
shippable.

Generalises the HaLow design-compare workflow into a reusable tool any
filter design can call. The score function is intentionally simple
(pass/fail × yield × SRF severity − component count) but exposed so
callers can override it for their own definition of "shippable".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import numpy as np

from mcp_ltspice.eval import FilterSpec, evaluate_filter_spec
from mcp_ltspice.extract import (
    components_dict_to_elements,
    ladder_sparams_from_components,
)
from mcp_ltspice.find_zeros import find_transmission_zeros
from mcp_ltspice.montecarlo import monte_carlo_analysis
from mcp_ltspice.optimize import optimize_filter
from mcp_ltspice.srf_check import srf_audit
from mcp_ltspice.sweep import sensitivity_analysis
from mcp_ltspice.synthesis import (
    Topology,
    place_transmission_zero,
    synthesize_lc_lpf,
)
from mcp_ltspice.vendor_models import substitute_real_components
from rf_mcp_common.touchstone import network_to_touchstone


@dataclass
class OrderResult:
    """One filter order's full design + evaluation."""

    order: int
    n_components: int
    n_traps_used: int
    components: dict[str, float]
    spec_overall: Literal["pass", "fail"]
    criteria: list[dict[str, Any]]
    srf_severity: Literal["ok", "caution", "critical"]
    n_srf_flagged: int
    mc_yield_pct: float
    mc_failures: dict[str, int]
    most_sensitive_component: str | None
    transmission_zeros: list[dict[str, float]]
    score: int
    rationale: str
    s2p_path: str | None = None


@dataclass
class CompareResult:
    """Side-by-side comparison of multiple filter orders."""

    orders_evaluated: list[int]
    results: list[OrderResult]
    winner_order: int
    winner_rationale: str
    spec_used: dict[str, Any] = field(default_factory=dict)


def _score(result_dict: dict[str, Any]) -> tuple[int, str]:
    """Default shippable-design score. Higher = more shippable."""
    score = 0
    reasons: list[str] = []

    if result_dict["spec_overall"] == "pass":
        score += 100
        reasons.append("all criteria pass")
    else:
        n_pass = sum(1 for c in result_dict["criteria"] if c["status"] == "pass")
        score += n_pass * 5
        reasons.append(f"{n_pass}/{len(result_dict['criteria'])} criteria pass")

    yield_pct = result_dict["mc_yield_pct"]
    score += int(yield_pct)
    if yield_pct >= 90:
        reasons.append(f"yield {yield_pct:.0f}% (excellent)")
    elif yield_pct >= 80:
        reasons.append(f"yield {yield_pct:.0f}% (production-acceptable)")
    elif yield_pct >= 50:
        reasons.append(f"yield {yield_pct:.0f}% (marginal)")
    else:
        reasons.append(f"yield {yield_pct:.0f}% (NOT production-acceptable)")

    sev = result_dict["srf_severity"]
    if sev == "ok":
        score += 30
        reasons.append("SRF: no concerns")
    elif sev == "caution":
        score += 15
        reasons.append("SRF: 1-2 components near limit")
    else:
        reasons.append(f"SRF: {result_dict['n_srf_flagged']} components flagged (critical)")

    n_comp = result_dict["n_components"]
    score -= n_comp * 2
    reasons.append(f"{n_comp} components")

    return score, " | ".join(reasons)


def _design_one(
    *,
    order: int,
    cutoff_hz: float,
    ripple_db: float,
    stopband_atten_db: float,
    z0: float,
    zero_targets: list[float],
    spec: FilterSpec,
    inductor_vendor: str,
    capacitor_vendor: str,
    optimize_max_iter: int,
    passband_weight: float,
    mc_n_runs: int,
    mc_tolerance_pct: float,
    s2p_dir: str | None,
) -> OrderResult:
    """Synthesize → place zeros → vendor-substitute → optimize → audit."""
    design = synthesize_lc_lpf(
        "elliptic",
        order=order,
        cutoff_hz=cutoff_hz,
        ripple_db=ripple_db,
        stopband_atten_db=stopband_atten_db,
        z0=z0,
        topology=Topology.SERIES_FIRST,
    )
    n_traps = (order - 1) // 2

    # Place zero_targets up to the number of available traps
    comps = dict(design.components)
    targets_used = zero_targets[:n_traps]
    for trap_local_idx, freq in enumerate(targets_used):
        trap_index = 2 * (trap_local_idx + 1)
        if f"L{trap_index}" not in comps or f"C{trap_index}" not in comps:
            continue
        result = place_transmission_zero(
            comps,
            trap_index=trap_index,
            target_freq_hz=freq,
            preserve_ratio=True,
            snap_series=None,
        )
        comps = result["components"]

    # Vendor substitute
    parts = substitute_real_components(comps)
    real_comps = {ref: info["snapped_value"] for ref, info in parts.items()}

    # Vendor-bounded optimization with passband weight
    opt = optimize_filter(
        real_comps,
        spec,
        transmission_zeros=True,
        z0=z0,
        max_iter=optimize_max_iter,
        bound_to_vendor=True,
        inductor_vendor=inductor_vendor,
        capacitor_vendor=capacitor_vendor,
        passband_weight=passband_weight,
    )
    final_comps = opt.snapped_components

    # Write Touchstone if requested
    s2p_path: str | None = None
    if s2p_dir is not None:
        from pathlib import Path

        out_dir = Path(s2p_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        f_grid = np.geomspace(10e6, max(20e9, cutoff_hz * 50), 1001)
        elements = components_dict_to_elements(
            final_comps, transmission_zeros=True, topology="series_first"
        )
        s = ladder_sparams_from_components(elements, f_grid, z0=z0)
        s2p_target = out_dir / f"order{order}_final.s2p"
        s2p_path = str(network_to_touchstone(f_grid, s, s2p_target, z0=z0))

    # Evaluate
    if s2p_path is None:
        # Need a Touchstone for evaluate_filter_spec; write a temp one
        import tempfile
        from pathlib import Path

        f_grid = np.geomspace(10e6, max(20e9, cutoff_hz * 50), 1001)
        elements = components_dict_to_elements(
            final_comps, transmission_zeros=True, topology="series_first"
        )
        s = ladder_sparams_from_components(elements, f_grid, z0=z0)
        with tempfile.NamedTemporaryFile(
            suffix=".s2p", delete=False, dir=tempfile.gettempdir()
        ) as f:
            tmp_path = Path(f.name)
        s2p_eval_path = network_to_touchstone(f_grid, s, tmp_path, z0=z0)
    else:
        s2p_eval_path = s2p_path  # type: ignore[assignment]

    check = evaluate_filter_spec(s2p_eval_path, spec)
    audit = srf_audit(
        final_comps, spec, inductor_vendor=inductor_vendor, capacitor_vendor=capacitor_vendor
    )
    sens = sensitivity_analysis(
        final_comps,
        spec,
        perturbation_pct=2.0,
        transmission_zeros=True,
    )
    mc = monte_carlo_analysis(
        final_comps,
        spec,
        tolerance_pct=mc_tolerance_pct,
        n_runs=mc_n_runs,
        transmission_zeros=True,
        n_jobs=-1,
    )
    found_zeros = find_transmission_zeros(s2p_eval_path, min_depth_db=15)

    result_dict = {
        "spec_overall": check.overall,
        "criteria": [
            {
                "label": c.label,
                "target_db": c.target_db,
                "measured_db": c.measured_db,
                "margin_db": c.margin_db,
                "status": c.status,
            }
            for c in check.criteria
        ],
        "mc_yield_pct": mc.yield_pct,
        "srf_severity": audit["severity"],
        "n_srf_flagged": audit["n_flagged"],
        "n_components": len(final_comps),
    }
    score, rationale = _score(result_dict)

    return OrderResult(
        order=order,
        n_components=len(final_comps),
        n_traps_used=n_traps,
        components=final_comps,
        spec_overall=check.overall,
        criteria=result_dict["criteria"],
        srf_severity=audit["severity"],
        n_srf_flagged=audit["n_flagged"],
        mc_yield_pct=mc.yield_pct,
        mc_failures=dict(mc.failing_criteria_counts),
        most_sensitive_component=sens.get("most_influential_component"),
        transmission_zeros=[
            {"freq_hz": z["freq_hz"], "depth_db": z["depth_db"]} for z in found_zeros
        ],
        score=score,
        rationale=rationale,
        s2p_path=s2p_path,
    )


def compare_filter_orders(
    *,
    orders: list[int],
    cutoff_hz: float,
    spec: FilterSpec | dict,
    zero_targets_hz: list[float],
    ripple_db: float = 0.1,
    stopband_atten_db: float = 50.0,
    z0: float = 50.0,
    inductor_vendor: str = "coilcraft_0402hp",
    capacitor_vendor: str = "murata_gjm_c0g",
    optimize_max_iter: int = 1500,
    passband_weight: float = 30.0,
    mc_n_runs: int = 1000,
    mc_tolerance_pct: float = 2.0,
    s2p_dir: str | None = None,
) -> CompareResult:
    """Run the full design-compare workflow for several orders.

    For each order in ``orders``:
        1. Synthesize the elliptic prototype at fc = ``cutoff_hz``
        2. Place transmission zeros at ``zero_targets_hz`` (up to the
           number of traps the order supports)
        3. Substitute Coilcraft + Murata real parts
        4. Optimize with vendor-bounded DE and passband weight
        5. Evaluate against ``spec``
        6. Run SRF audit + sensitivity analysis + Monte Carlo
        7. Score (default: pass/fail × yield × SRF − components)

    The order with the highest score is returned as the winner.
    """
    if isinstance(spec, dict):
        spec = FilterSpec.model_validate(spec)
    if not orders:
        raise ValueError("Need at least one order to compare")

    results: list[OrderResult] = []
    for order in orders:
        results.append(
            _design_one(
                order=order,
                cutoff_hz=cutoff_hz,
                ripple_db=ripple_db,
                stopband_atten_db=stopband_atten_db,
                z0=z0,
                zero_targets=zero_targets_hz,
                spec=spec,
                inductor_vendor=inductor_vendor,
                capacitor_vendor=capacitor_vendor,
                optimize_max_iter=optimize_max_iter,
                passband_weight=passband_weight,
                mc_n_runs=mc_n_runs,
                mc_tolerance_pct=mc_tolerance_pct,
                s2p_dir=s2p_dir,
            )
        )

    winner = max(results, key=lambda r: r.score)
    return CompareResult(
        orders_evaluated=orders,
        results=results,
        winner_order=winner.order,
        winner_rationale=winner.rationale,
        spec_used=spec.model_dump(),
    )
