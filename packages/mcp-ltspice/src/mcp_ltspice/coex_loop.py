"""Closed-loop coex-driven filter synthesis (issue #14).

One call closes the loop the engineer used to orchestrate by hand:

1. :func:`mcp_rf_analysis.coex_zeros.place_zeros_for_coex` positions the
   elliptic transmission zeros on the victim-weighted harmonic centroids
   (#12) — one zero per available trap.
2. ``synthesize_lc_lpf("elliptic", …)`` builds the prototype and
   ``place_transmission_zero`` aims each trap (E24-snapped parts).
3. ``substitute_real_components`` realizes vendor parts with an SRF
   margin (issue #13's 1.2 default) against the highest harmonic.
4. The realized ladder's rejection is evaluated analytically and folded
   into the coex matrix inputs: the fundamental sees the filter's
   in-band rejection, each harmonic's dBc is deepened by the rejection
   at n·f0, and GNSS victims get the rejection at *their* frequency
   injected (#15's ``filter_rejection_db``).
5. :func:`mcp_rf_analysis.coex.check_coex_matrix` (GNSS-aware) yields
   the worst-case desense; if it exceeds the target, the order steps by
   2 and the loop repeats up to ``max_order``. Non-convergence returns
   the best-so-far design with ``converged=False`` and the full
   iteration log either way.

This module is the one place mcp-ltspice depends on mcp-rf-analysis —
one-directional, no cycle (mcp-rf-analysis never imports this package).
"""

from __future__ import annotations

from typing import Any

import numpy as np

from mcp_ltspice.extract import (
    components_dict_to_elements,
    ladder_sparams_from_components,
)
from mcp_ltspice.synthesis.lc_filter import synthesize_lc_lpf
from mcp_ltspice.synthesis.zeros import place_transmission_zero
from mcp_ltspice.vendor_models import substitute_real_components

#: Raw (pre-filter) PA harmonic levels, dBc — the same broadband PA
#: assumption check_coex_matrix documents.
_RAW_PA_DBC = {2: -30.0, 3: -40.0, 4: -50.0, 5: -55.0}


def _rejection_db(components: dict[str, float], freqs_hz: list[float]) -> list[float]:
    """|S21| rejection (positive dB) of the realized lowpass ladder."""
    elements = components_dict_to_elements(components, topology="series_first", kind="lowpass")
    s = ladder_sparams_from_components(elements, np.asarray(freqs_hz, dtype=float), z0=50.0)
    return [-20.0 * float(np.log10(max(abs(s21), 1e-12))) for s21 in s[:, 1, 0]]


def synthesize_for_coex_target(
    passband_hz: tuple[float, float],
    pa_power_dbm: float,
    victim_bands: list[dict[str, Any]],
    *,
    target_max_desense_db: float = 0.0,
    antenna_iso_db: float = 25.0,
    min_order: int = 5,
    max_order: int = 11,
    inductor_vendor: str = "coilcraft_0402hp",
    capacitor_vendor: str = "murata_gjm_c0g",
    ripple_db: float = 0.1,
    stopband_atten_db: float = 50.0,
    harmonics: list[int] | None = None,
) -> dict[str, Any]:
    """Iterate elliptic LPF synthesis until the coex matrix meets the
    desense target. ``victim_bands`` entries are coex-matrix RX dicts
    (``name``, ``f_center_hz``/``f_range_hz``, ``sensitivity_dbm``,
    optional ``victim_type="gnss"`` per #15). Worst-case desense is
    ``max(0, −min(desense_margin_db))`` over the matrix.
    """
    from mcp_rf_analysis.coex import check_coex_matrix
    from mcp_rf_analysis.coex_zeros import place_zeros_for_coex

    f_lo, f_hi = (float(x) for x in passband_hz)
    if not 0.0 < f_lo < f_hi:
        raise ValueError(f"passband_hz must be (low, high) with 0 < low < high; got {passband_hz}")
    if not 3 <= min_order <= max_order:
        raise ValueError(f"need 3 ≤ min_order ≤ max_order; got {min_order}..{max_order}")
    if min_order % 2 == 0:
        raise ValueError(f"min_order must be odd (elliptic synthesis); got {min_order}")
    # Zeros go on the dominant PA products (2H/3H) by default — 4H/5H sit
    # ≥50 dBc down before any filtering, and letting their broad FCC
    # landings outrank 2H/3H would also push the trap SRF spec beyond
    # what 0402 parts can realise. The coex matrix still evaluates all
    # harmonics through 5H regardless.
    orders_h = harmonics if harmonics is not None else [2, 3]

    f_center = (f_lo + f_hi) / 2.0
    cutoff_hz = 1.05 * f_hi

    iterations: list[dict[str, Any]] = []
    best: dict[str, Any] | None = None

    for order in range(min_order, max_order + 1, 2):
        n_traps = order // 2
        plan = place_zeros_for_coex(
            (f_lo, f_hi),
            orders_h,
            [
                {
                    "name": v["name"],
                    "freq_range_hz": v.get(
                        "f_range_hz",
                        [
                            v["f_center_hz"] - v.get("bandwidth_hz", 0.0) / 2.0,
                            v["f_center_hz"] + v.get("bandwidth_hz", 0.0) / 2.0,
                        ],
                    ),
                    "severity": v.get("severity", 1.0),
                }
                for v in victim_bands
            ],
            n_zeros=n_traps,
        )

        design = synthesize_lc_lpf(
            "elliptic",
            order,
            cutoff_hz,
            ripple_db=ripple_db,
            stopband_atten_db=stopband_atten_db,
            z0=50.0,
        )
        components = dict(design.components)
        for z in sorted(plan["zeros"], key=lambda z: z["target_freq_hz"]):
            hint = z["trap_index_hint"]
            if f"L{hint}" in components and f"C{hint}" in components:
                components = place_transmission_zero(
                    components, trap_index=hint, target_freq_hz=z["target_freq_hz"]
                )["components"]

        # SRF spec: parts must still behave at the highest placed zero.
        # Start at the issue's 1.2 margin and degrade gracefully — a
        # turn-key loop reports a relaxed margin instead of dying when
        # the vendor family can't provide it (e.g. a 14 nH 0402 with
        # SRF ≥ 3.3 GHz does not exist).
        from mcp_ltspice.vendor_models import SrfRejectionError

        f_srf_spec = max(z["target_freq_hz"] for z in plan["zeros"])
        substitution = None
        srf_margin_used: float = 0.0
        for margin in (1.2, 1.0, 0.0):
            try:
                substitution = substitute_real_components(
                    components,
                    inductor_vendor,
                    capacitor_vendor,
                    srf_margin=margin,
                    max_spec_freq_hz=f_srf_spec if margin > 0 else None,
                )
                srf_margin_used = margin
                break
            except SrfRejectionError:
                continue
        if substitution is None:  # pragma: no cover - margin 0 cannot SRF-reject
            raise RuntimeError("vendor substitution failed even without an SRF constraint")
        realized = {ref: info["snapped_value"] for ref, info in substitution.items()}

        harmonic_freqs = [n * f_center for n in orders_h]
        rej = _rejection_db(realized, [f_center, *harmonic_freqs])
        rej_f0, rej_harm = rej[0], dict(zip(orders_h, rej[1:], strict=True))

        tx = {
            "name": "TX",
            "f_center_hz": f_center,
            "power_dbm": pa_power_dbm,
            "filter_rejection_db": rej_f0,
            "filtered_harmonic_dbc": {
                f"{n}H": _RAW_PA_DBC.get(n, -55.0) - rej_harm[n] for n in orders_h
            },
        }
        rx_list: list[dict[str, Any]] = []
        for v in victim_bands:
            v = dict(v)
            if v.get("victim_type") == "gnss" and "filter_rejection_db" not in v:
                v["filter_rejection_db"] = _rejection_db(realized, [v["f_center_hz"]])[0]
            rx_list.append(v)

        matrix = check_coex_matrix([tx], rx_list, antenna_iso_db=antenna_iso_db)["matrix"]
        worst_entry = matrix[0] if matrix else None
        worst_desense = max(0.0, -worst_entry["desense_margin_db"]) if worst_entry else 0.0
        converged = worst_desense <= target_max_desense_db

        iteration = {
            "order": order,
            "worst_desense_db": worst_desense,
            "worst_entry": worst_entry,
            "rejection_at_f0_db": rej_f0,
            "rejection_at_harmonics_db": rej_harm,
            "n_zeros_placed": len(plan["zeros"]),
            "srf_margin_used": srf_margin_used,
            "converged": converged,
        }
        iterations.append(iteration)

        candidate = {
            "converged": converged,
            "chosen_order": order,
            "ripple_db": ripple_db,
            "stopband_atten_db": stopband_atten_db,
            "components": realized,
            "ideal_components": components,
            "substitution": substitution,
            "zeros_plan": plan,
            "coex_matrix": matrix,
            "iterations": iterations,
        }
        if best is None or worst_desense < max(
            0.0, -(best["coex_matrix"][0]["desense_margin_db"] if best["coex_matrix"] else 0.0)
        ):
            best = candidate
        if converged:
            return candidate

    assert best is not None
    best["converged"] = False
    best["iterations"] = iterations
    return best
