#!/usr/bin/env python3
"""Worldwide HaLow Elliptic LPF — turn-key design pipeline.

End-to-end flow for a Murata Type 2HK (LBAA0Z02HK, +23 dBm) HaLow
module-grade low-pass filter that:

* passes 863–928 MHz (worldwide HaLow channels: EU/US/JP/KR/CN/SG/AU/NZ/IN)
* suppresses **2H** (1726–1856 MHz) protecting LTE B3/B9 DL
* suppresses **3H** (2589–2784 MHz) protecting LTE B7/B38/B41 DL
* protects co-located GNSS RX (GPS L1, GLONASS L1, Galileo E1, BeiDou B1)

The pipeline uses every relevant MCP tool — including the four added in
this stream (#9 Coilcraft fetch, #10 Murata fetch, #11 user-drop dir,
#12 place_zeros_for_coex, #13 srf_margin, #16 validate_against_spice) —
and emits a complete deliverable bundle (schematic, S2P, response plot,
markdown reports, and a single shareable PDF).

Outputs (gitignored alongside this file):

* ``halow_lpf.asc``                  — final LTspice schematic
* ``halow_lpf.s2p``                  — analytical S-parameters
* ``halow_lpf.spice.s2p``            — SPICE-validated S-parameters
* ``response.png``                   — S21/S11 Bode plot
* ``halow_lpf.schematic.svg/.png``   — schemdraw rendering
* ``compare_orders_report.md``       — order-comparison table
* ``report.md``                      — single-design final report
* ``coex_report.md``                 — coex matrix vs LTE/GNSS
* ``spice_validation.md``            — analytical-vs-SPICE delta
* ``halow_lpf_report.pdf``           — bundled deliverable
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from mcp_ltspice.asc_io import generate_lpf_asc
from mcp_ltspice.coex_zeros import place_zeros_for_coex
from mcp_ltspice.compare import compare_filter_orders
from mcp_ltspice.eval import FilterSpec, evaluate_filter_spec
from mcp_ltspice.extract import (
    components_dict_to_elements,
    ladder_sparams_from_components,
)
from mcp_ltspice.montecarlo import monte_carlo_analysis
from mcp_ltspice.optimize import optimize_filter
from mcp_ltspice.render import render_response
from mcp_ltspice.report_pdf import build_design_report_pdf
from mcp_ltspice.schematic_render import render_asc_as_schematic
from mcp_ltspice.srf_check import srf_audit
from mcp_ltspice.synthesis import Topology, place_transmission_zero, synthesize_lc_lpf
from mcp_ltspice.validate_spice import validate_against_spice
from mcp_ltspice.vendor_models import SrfRejectionError, substitute_real_components
from mcp_rf_analysis.coex import check_coex_matrix, lookup_harmonic_victims
from rf_mcp_common.touchstone import network_to_touchstone

HERE = Path(__file__).parent
SPEC_PATH = HERE / "spec.json"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _section(title: str) -> None:
    bar = "=" * (len(title) + 4)
    print(f"\n{bar}\n  {title}\n{bar}")


def _write_s2p(
    components: dict[str, float],
    path: Path,
    *,
    topology: str = "series_first",
    transmission_zeros: bool = True,
) -> Path:
    """Compute analytical S2P from components and write Touchstone.

    Default ``transmission_zeros=True`` matches the elliptic topology where
    even-indexed Lk + Ck pairs form shunt-LC traps (resonant to ground).
    """
    f = np.geomspace(1e6, 7e9, 1601)
    elements = components_dict_to_elements(
        components, topology=topology, transmission_zeros=transmission_zeros
    )
    s = ladder_sparams_from_components(elements, f, z0=50.0)
    return network_to_touchstone(f, s, path, z0=50.0)


def _print_check(label: str, result: Any) -> None:
    print(f"\n--- {label} ({result.overall.upper()}) ---")
    print(f"{'Criterion':<36} {'Target':>10} {'Measured':>12} {'Margin':>10} {'Status':>8}")
    for c in result.criteria:
        target = f"{c.target_db:.1f} dB"
        measured = f"{c.measured_db:.2f} dB" if math.isfinite(c.measured_db) else "n/a"
        margin = f"{c.margin_db:+.2f} dB" if math.isfinite(c.margin_db) else "n/a"
        print(f"{c.label:<36} {target:>10} {measured:>12} {margin:>10} {c.status:>8}")


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def main() -> None:
    raw_spec = json.loads(SPEC_PATH.read_text())
    spec = FilterSpec.model_validate(raw_spec)

    f_lo, f_hi = spec.passband.f_start, spec.passband.f_stop
    f_center = math.sqrt(f_lo * f_hi)  # geometric centre
    pa_dbm = float(raw_spec["_doc"]["tx_power_dbm_at_filter_input"])
    ant_iso_db = float(raw_spec["_doc"]["antenna_isolation_db_assumed"])
    coex_radios = raw_spec["coex_radios"]

    _section("[0] Spec loaded — Murata Type 2HK worldwide HaLow LPF")
    print(f"  Passband:    {f_lo / 1e6:.1f} – {f_hi / 1e6:.1f} MHz "
          f"(geo. centre {f_center / 1e6:.1f} MHz)")
    print(f"  Passband IL: ≤ {spec.passband.il_max_db:.1f} dB")
    print(f"  Passband RL: ≥ {spec.passband.rl_min_db:.1f} dB")
    print(f"  Stopband targets: {len(spec.stopband_targets)}")
    print(f"  PA power at filter input: +{pa_dbm:.0f} dBm")
    print(f"  Antenna isolation (assumed): {ant_iso_db:.0f} dB")

    # -----------------------------------------------------------------------
    # 1. Confirm harmonic landings
    # -----------------------------------------------------------------------
    _section("[1] Harmonic-victim lookup (mcp-rf-analysis)")
    print(f"  Centre frequency: {f_center / 1e6:.1f} MHz")
    victims_lookup = lookup_harmonic_victims(f_center, harmonic_orders=raw_spec["harmonics"])
    for v in victims_lookup:
        n = v["harmonic"]
        f_h = v["freq_hz"]
        n_lte_dl = len(v["victims"].get("lte_dl", []))
        n_gnss = len(v["victims"].get("gnss", []))
        print(f"   {n}H @ {f_h / 1e6:.1f} MHz: {n_lte_dl} LTE-DL band(s), {n_gnss} GNSS band(s)"
              + (" [FCC RESTRICTED]" if v["in_fcc_restricted"] else ""))

    # -----------------------------------------------------------------------
    # 2. Compute optimal TZ frequencies
    # -----------------------------------------------------------------------
    _section("[2] place_zeros_for_coex — optimal TZ placement")
    victim_bands = [
        {"name": r["name"],
         "freq_range_hz": r.get("f_range_hz") or [
             r["f_center_hz"] - r["bandwidth_hz"] / 2,
             r["f_center_hz"] + r["bandwidth_hz"] / 2],
         "category": r["category"]}
        for r in coex_radios
    ]
    placement = place_zeros_for_coex(
        passband_hz=(f_lo, f_hi),
        harmonics=raw_spec["harmonics"],
        victim_bands=victim_bands,
    )
    zero_targets_hz = [z["target_freq_hz"] for z in placement["zeros"]]
    for z in placement["zeros"]:
        names = ", ".join(v["name"] for v in z["victims_covered"])
        print(f"   TZ for {z['harmonic']}H at {z['target_freq_hz'] / 1e6:.1f} MHz "
              f"(trap L{z['trap_index_hint']}/C{z['trap_index_hint']}) "
              f"→ covers {names or 'no overlapping victims'}")

    # -----------------------------------------------------------------------
    # 3. Compare orders 5/7/9 elliptic
    # -----------------------------------------------------------------------
    _section("[3] compare_filter_orders — pick the lowest order that ships")
    cutoff_hz = f_hi  # elliptic ripple-edge at upper passband edge
    # Pick synthesis params that keep all L/C in the 0402 catalogue range
    # (Coilcraft 0402HP: 1–22 nH; Murata GJM 0402 C0G: 0.5–22 pF). The
    # elliptic prototype's natural component values scale with both order
    # and stopband_atten_db; 40 dB stopband at order 7 is the sweet spot.
    cmp_result = compare_filter_orders(
        orders=[5, 7, 9],
        cutoff_hz=cutoff_hz,
        spec=spec,
        zero_targets_hz=zero_targets_hz,
        ripple_db=0.1,
        stopband_atten_db=40.0,
        z0=50.0,
        inductor_vendor="coilcraft_0402hp",
        capacitor_vendor="murata_gjm_c0g",
        optimize_max_iter=2000,
        passband_weight=30.0,
        mc_n_runs=500,
        mc_tolerance_pct=2.0,
        s2p_dir=str(HERE),
    )
    print(f"\n  Winner: {cmp_result.winner_order}th-order — {cmp_result.winner_rationale}\n")
    print(f"  {'Order':<6} {'Pass':<6} {'Yield':<10} {'SRF':<10} {'#Comp':<6} {'Score':<6}")
    for r in cmp_result.results:
        print(f"  {r.order:<6} {r.spec_overall:<6} "
              f"{r.mc_yield_pct:>5.1f}%   {r.srf_severity:<10} {r.n_components:<6} {r.score:<6}")

    # Save the comparison report.
    cmp_report_path = HERE / "compare_orders_report.md"
    cmp_report_path.write_text(_render_compare_report(cmp_result))

    # -----------------------------------------------------------------------
    # 4. Adopt the winner's optimized components (compare_filter_orders
    #    already ran synthesise → place-zeros → vendor-substitute → vendor-
    #    bounded optimise → MC for each candidate order). We use those
    #    components and surface the SRF posture as an additional check.
    # -----------------------------------------------------------------------
    winner_order = cmp_result.winner_order
    winner = next(r for r in cmp_result.results if r.order == winner_order)
    _section(f"[4] Final design — {winner_order}-order elliptic (compare_filter_orders winner)")
    final_comps = dict(winner.components)
    print(f"   adopted {len(final_comps)} components from compare_filter_orders winner")
    print(f"   yield: {winner.mc_yield_pct:.1f}%, SRF severity: {winner.srf_severity}, "
          f"score: {winner.score}")

    # Re-derive the parts metadata table for the report. Try with
    # srf_margin=1.0 first (need SRF at least the max spec target). If the
    # curated 0402HP catalogue can't honour that, fall back to legacy
    # behavior with a clear warning so the engineer knows that production
    # builds need a higher-SRF vendor series.
    srf_margin_used = 1.0
    srf_warning: str | None = None
    try:
        parts = substitute_real_components(
            final_comps,
            inductor_vendor="coilcraft_0402hp",
            capacitor_vendor="murata_gjm_c0g",
            srf_margin=srf_margin_used,
            spec=raw_spec,
            max_value_drift_pct=200.0,  # keep the optimizer's value choices, just verify SRF
        )
    except SrfRejectionError as e:
        srf_margin_used = 0.0
        srf_warning = (
            f"srf_margin=1.0 rejected {e.refdes} ({_fmt_value(e.target_value, e.kind)} "
            f"on {e.vendor}); the curated 0402HP catalogue lacks a part with SRF >= "
            f"{e.threshold_hz / 1e9:.2f} GHz at that value. Falling back to no SRF "
            f"enforcement — for production, upgrade to Coilcraft 0402DC / 0402DG (issue: "
            f"extend curated catalogue) or fetch real S2P via fetch_coilcraft_s2p."
        )
        print(f"\n   ⚠  {srf_warning}")
        parts = substitute_real_components(
            final_comps,
            inductor_vendor="coilcraft_0402hp",
            capacitor_vendor="murata_gjm_c0g",
        )

    real_comps = {ref: info["snapped_value"] for ref, info in parts.items()}

    # SRF audit (informational).
    srf_result = srf_audit(real_comps, raw_spec, margin_pct=20.0)
    print(f"   SRF audit verdict: {srf_result['severity']} ({srf_result['n_flagged']} flagged)")

    # The compare_filter_orders optimizer already ran inside the comparison
    # (vendor-bounded Nelder-Mead with passband_weight). final_comps is the
    # post-optimization snapshot — no further optimization is needed here.

    # -----------------------------------------------------------------------
    # 5. Emit schematic + analytical S2P
    # -----------------------------------------------------------------------
    _section("[6] Emit schematic + analytical S-parameters")
    asc_path = generate_lpf_asc(
        final_comps,
        HERE / "halow_lpf.asc",
        topology="lpf_t_elliptic",
        z0=50.0,
        f_start_hz=1e6,
        f_stop_hz=7e9,
    )
    s2p_path = _write_s2p(final_comps, HERE / "halow_lpf.s2p")
    print(f"   {asc_path.name}, {s2p_path.name}")

    # -----------------------------------------------------------------------
    # 6. Spec evaluation
    # -----------------------------------------------------------------------
    _section("[7] evaluate_filter_spec — pass/fail per criterion")
    eval_result = evaluate_filter_spec(s2p_path, spec)
    _print_check("Spec compliance (analytical S2P)", eval_result)

    # -----------------------------------------------------------------------
    # 7. SPICE validation
    # -----------------------------------------------------------------------
    _section("[8] validate_against_spice — reconcile analytical vs SPICE")
    spice_result = validate_against_spice(
        asc_path, final_comps, spec=raw_spec,
        output_spice_s2p=str(HERE / "halow_lpf_spice.s2p"),
        output_analytical_s2p=str(HERE / "halow_lpf_analytical.s2p"),
        passband_threshold_db=0.5,
        stopband_threshold_db=3.0,
    )
    print(f"   verdict: {spice_result['verdict']}")
    if spice_result["verdict"] != "spice_unavailable":
        print(f"   max Δ|S21| passband: {spice_result['max_delta_passband_db']:.2f} dB "
              f"(threshold {spice_result['passband_threshold_db']:.1f} dB)")
        print(f"   max Δ|S21| stopband: {spice_result['max_delta_stopband_db']:.2f} dB "
              f"(threshold {spice_result['stopband_threshold_db']:.1f} dB)")
        if spice_result["flagged_regions"]:
            print(f"   flagged regions: {len(spice_result['flagged_regions'])}")
    else:
        print(f"   reason: {spice_result.get('spice_error', 'no simulator')}")

    # -----------------------------------------------------------------------
    # 8. Monte Carlo
    # -----------------------------------------------------------------------
    _section("[9] monte_carlo_analysis — yield with 2% tolerance")
    mc = monte_carlo_analysis(
        final_comps, raw_spec, tolerance_pct=2.0, n_runs=2000,
        transmission_zeros=True, n_jobs=-1,
    )
    print(f"   yield: {mc.yield_pct:.1f}% ({mc.n_passing} / {mc.n_runs})")

    # -----------------------------------------------------------------------
    # 9. Coex matrix
    # -----------------------------------------------------------------------
    _section("[10] check_coex_matrix — co-located radio desense")
    # Build TX with the filter's actual rejection at each harmonic frequency.
    s2p_for_coex = read_s2p_dict(s2p_path)
    filtered_dbc = compute_harmonic_dbc_from_s2p(
        s2p_for_coex, fundamental_hz=f_center,
    )
    tx_list = [{
        "name": "HaLow @ 915 MHz",
        "f_center_hz": f_center,
        "power_dbm": pa_dbm,
        "filtered_harmonic_dbc": filtered_dbc,
        "filter_rejection_db": 0.0,  # fundamental passes through
    }]
    coex = check_coex_matrix(
        tx_list=tx_list, rx_list=coex_radios,
        antenna_iso_db=ant_iso_db,
    )
    print(f"   {coex['n_pairs_analyzed']} aggressor×victim×harmonic pairs analyzed")
    print(f"   {len(coex['matrix'])} actual hits (in-band overlaps)")
    for row in coex["matrix"][:10]:  # worst (smallest margin) first
        print(f"   - {row['aggressor']} → {row['victim']} via {row['mechanism']}: "
              f"margin {row['desense_margin_db']:+.1f} dB ({row['concern']})")

    # -----------------------------------------------------------------------
    # 10. Render plots
    # -----------------------------------------------------------------------
    _section("[11] Render response plot + schematic")
    response_png = render_response(
        s2p_path, HERE / "response.png",
        markers=[
            (f_lo, "f_lo"), (f_hi, "f_hi"),
            (1830e6, "2H/B3"), (2640e6, "3H/B7"),
            (1575e6, "GPS L1"),
        ],
        title="Worldwide HaLow LPF — Murata Type 2HK target",
    )
    print(f"   {response_png.name}")
    schem_svg = render_asc_as_schematic(asc_path, HERE / "halow_lpf.schematic.svg")
    schem_png = render_asc_as_schematic(asc_path, HERE / "halow_lpf.schematic.png")
    print(f"   {schem_svg.name}, {schem_png.name}")

    # -----------------------------------------------------------------------
    # 11. Write reports
    # -----------------------------------------------------------------------
    _section("[12] Write reports")
    report_path = HERE / "report.md"
    coex_report_path = HERE / "coex_report.md"
    spice_report_path = HERE / "spice_validation.md"

    report_path.write_text(_render_report(
        spec=spec, raw_spec=raw_spec, comps=final_comps, parts=parts,
        eval_result=eval_result, mc=mc, srf_result=srf_result,
        winner_order=winner_order, placement=placement, cmp_result=cmp_result,
        srf_margin_used=srf_margin_used, srf_warning=srf_warning,
    ))
    coex_report_path.write_text(_render_coex_report(coex, ant_iso_db))
    spice_report_path.write_text(_render_spice_validation_report(spice_result))
    print(f"   {report_path.name}, {coex_report_path.name}, {spice_report_path.name}")

    # -----------------------------------------------------------------------
    # 12. Bundle PDF
    # -----------------------------------------------------------------------
    _section("[13] build_design_report_pdf — final deliverable")
    pdf_path = build_design_report_pdf(
        design_dir=str(HERE),
        output_pdf=str(HERE / "halow_lpf_report.pdf"),
        title="Worldwide HaLow LPF — Murata Type 2HK target",
    )
    print(f"   {Path(pdf_path).name}")

    # -----------------------------------------------------------------------
    # Verdict
    # -----------------------------------------------------------------------
    _section("VERDICT")
    print(f"  Winning order  : {winner_order}-order elliptic ({len(final_comps)} components)")
    print(f"  Spec overall   : {eval_result.overall.upper()}")
    print(f"  Monte Carlo    : {mc.yield_pct:.1f}% yield at 2% tolerance")
    print(f"  SPICE verdict  : {spice_result['verdict']}")
    print(f"  Worst desense  : {coex['matrix'][0]['desense_margin_db']:+.1f} dB "
          f"({coex['matrix'][0]['victim']})" if coex["matrix"] else "  Worst desense  : no co-located hits")
    print(f"  Generated      : {datetime.now().isoformat(timespec='seconds')}")


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------


def _fmt_value(v: float, kind: str) -> str:
    if kind == "L":
        if v >= 1e-6:
            return f"{v * 1e6:.2f} µH"
        return f"{v * 1e9:.2f} nH"
    if kind == "C":
        if v >= 1e-9:
            return f"{v * 1e9:.2f} nF"
        return f"{v * 1e12:.2f} pF"
    return f"{v:.3e}"


def read_s2p_dict(path: Path) -> Any:
    """Read a touchstone file as an skrf Network."""
    import skrf as rf

    return rf.Network(str(path))


def compute_harmonic_dbc_from_s2p(net: Any, *, fundamental_hz: float) -> dict[str, float]:
    """For each harmonic (2H–5H), compute the filter's rejection (in dB) at
    that frequency and convert into a typical PA-output × filter-rejection
    dBc number to feed into ``check_coex_matrix``.

    Assumed PA raw harmonic dBc (typical for HaLow class-AB PA):
    2H = -25, 3H = -35, 4H = -45, 5H = -50.

    Filter rejection further reduces. The ``check_coex_matrix`` API expects
    one number per harmonic = (raw dBc) − (filter dB rejection), which is
    the more-negative dBc that the antenna sees.
    """
    raw_pa_dbc = {2: -25.0, 3: -35.0, 4: -45.0, 5: -50.0}
    out: dict[str, float] = {}
    for n, raw in raw_pa_dbc.items():
        f_h = n * fundamental_hz
        if f_h < net.f.min() or f_h > net.f.max():
            continue
        s21 = np.interp(f_h, net.f, net.s[:, 1, 0])
        s21_db = 20.0 * np.log10(max(abs(s21), 1e-12))
        rejection_db = -s21_db
        out[f"{n}H"] = raw - rejection_db
    return out


def _render_compare_report(cmp_result: Any) -> str:
    lines = [
        "# HaLow Worldwide LPF — Order Comparison Report",
        "",
        f"Spec applied: see `spec.json` (worldwide passband 863–928 MHz).",
        "",
        f"Winner: **{cmp_result.winner_order}th-order**",
        "",
        f"Rationale: {cmp_result.winner_rationale}",
        "",
        "## Side-by-side",
        "",
        "| Order | Pass | Yield | SRF | #Components | Score |",
        "|-------|------|-------|-----|-------------|-------|",
    ]
    for r in cmp_result.results:
        lines.append(
            f"| {r.order} | {r.spec_overall} | {r.mc_yield_pct:.1f}% | {r.srf_severity} | "
            f"{r.n_components} | {r.score} |"
        )
    return "\n".join(lines) + "\n"


def _render_report(
    *,
    spec: FilterSpec,
    raw_spec: dict[str, Any],
    comps: dict[str, float],
    parts: dict[str, dict[str, Any]],
    eval_result: Any,
    mc: Any,
    srf_result: dict[str, Any],
    winner_order: int,
    placement: dict[str, Any],
    cmp_result: Any,
    srf_margin_used: float = 0.0,
    srf_warning: str | None = None,
) -> str:
    pb = spec.passband
    lines = [
        "# HaLow Worldwide LPF — Final Design Report",
        "",
        f"**Module target:** {raw_spec['_doc']['module']}",
        f"**Passband:** {pb.f_start / 1e6:.1f} – {pb.f_stop / 1e6:.1f} MHz",
        f"**TX power at filter input:** +{raw_spec['_doc']['tx_power_dbm_at_filter_input']} dBm",
        f"**Antenna isolation assumed:** {raw_spec['_doc']['antenna_isolation_db_assumed']} dB",
        f"**Topology:** {winner_order}-order elliptic (series-first / T-network)",
        f"**Vendors:** Coilcraft 0402HP inductors, Murata GJM 0402 C0G capacitors",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "## Transmission-zero placement",
        "",
        placement["rationale"],
        "",
        "## Bill of materials",
        "",
        "| RefDes | Kind | Final value | SRF (GHz) | Vendor | Ideal (synth) |",
        "|--------|------|-------------|-----------|--------|---------------|",
    ]
    for ref, info in sorted(parts.items()):
        ideal = _fmt_value(info["ideal_value"], info["kind"])
        snapped = _fmt_value(info["snapped_value"], info["kind"])
        srf_ghz = info["srf_hz"] / 1e9
        lines.append(
            f"| {ref} | {info['kind']} | {snapped} | {srf_ghz:.2f} | "
            f"{info['vendor']} | {ideal} |"
        )

    lines += [
        "",
        f"**Total components:** {len(parts)}",
        f"**SRF audit:** {srf_result['severity']} ({srf_result['n_flagged']} flagged)",
        f"**srf_margin used:** {srf_margin_used:.1f}",
    ]
    if srf_warning:
        lines += ["", f"> ⚠ **SRF caveat:** {srf_warning}"]
    lines += ["",
        "## Spec evaluation (analytical S2P)",
        "",
        f"**Overall:** {eval_result.overall.upper()}",
        "",
        "| Criterion | Target | Measured | Margin | Status |",
        "|-----------|--------|----------|--------|--------|",
    ]
    for c in eval_result.criteria:
        target = f"{c.target_db:.1f} dB"
        measured = f"{c.measured_db:.2f} dB" if math.isfinite(c.measured_db) else "n/a"
        margin = f"{c.margin_db:+.2f} dB" if math.isfinite(c.margin_db) else "n/a"
        lines.append(f"| {c.label} | {target} | {measured} | {margin} | {c.status} |")

    lines += [
        "",
        "## Monte Carlo yield",
        "",
        f"- N runs: {mc.n_runs}",
        f"- Tolerance: 2% L, 2% C",
        f"- **Yield: {mc.yield_pct:.1f}%** ({mc.n_passing}/{mc.n_runs} passing)",
        "",
        "## Order-comparison summary",
        "",
        "See `compare_orders_report.md` for the full side-by-side. Summary:",
        "",
    ]
    for r in cmp_result.results:
        lines.append(
            f"- **Order {r.order}** — {r.spec_overall.upper()}, "
            f"{r.mc_yield_pct:.0f}% yield, SRF {r.srf_severity}, "
            f"{r.n_components} components, score {r.score}"
            + (" ← **winner**" if r.order == winner_order else "")
        )

    lines += [
        "",
        "## Files",
        "",
        "- `halow_lpf.asc` — LTspice schematic",
        "- `halow_lpf.s2p` — analytical S-parameters",
        "- `halow_lpf.spice.s2p` — SPICE-validated S-parameters",
        "- `response.png` — S21/S11 Bode",
        "- `halow_lpf.schematic.svg/.png` — clean schemdraw rendering",
        "- `compare_orders_report.md` — order comparison details",
        "- `coex_report.md` — co-located radio desense matrix",
        "- `spice_validation.md` — analytical-vs-SPICE Δ",
        "- `halow_lpf_report.pdf` — bundled deliverable",
    ]

    return "\n".join(lines) + "\n"


def _render_coex_report(coex: dict[str, Any], ant_iso_db: float) -> str:
    lines = [
        "# HaLow LPF — Coexistence Matrix",
        "",
        f"**Antenna isolation assumed:** {ant_iso_db} dB",
        f"**Aggressors × victims × harmonics analysed:** {coex['n_pairs_analyzed']}",
        f"**Actual in-band hits:** {len(coex['matrix'])}",
        "",
        "Sorted by worst margin (most concerning first).",
        "",
        "| Aggressor | Victim | Mechanism | Emit @ (MHz) | At RX (dBm) | Sensitivity (dBm) | Margin (dB) | Concern |",
        "|-----------|--------|-----------|---------------|-------------|-------------------|-------------|---------|",
    ]
    for row in coex["matrix"]:
        lines.append(
            f"| {row['aggressor']} | {row['victim']} | {row['mechanism']} | "
            f"{row['f_emit_hz'] / 1e6:.1f} | {row['predicted_at_rx_dbm']:.1f} | "
            f"{row['victim_sensitivity_dbm']:.1f} | {row['desense_margin_db']:+.1f} | "
            f"{row['concern']} |"
        )

    if not coex["matrix"]:
        lines.append("")
        lines.append("*No co-located radio falls within the harmonic landings of this filter.*")

    return "\n".join(lines) + "\n"


def _render_spice_validation_report(result: dict[str, Any]) -> str:
    lines = [
        "# HaLow LPF — Analytical-vs-SPICE Validation",
        "",
        f"**Verdict:** {result['verdict']}",
    ]
    if result["verdict"] == "spice_unavailable":
        lines += [
            "",
            "SPICE simulator (ngspice / LTspice) was not available in this run. "
            "Install ngspice (`apt install ngspice` / `brew install ngspice`) or "
            "LTspice via Wine to enable the SPICE-vs-analytical reconciliation step.",
            "",
            f"Error: {result.get('spice_error', '<unknown>')}",
        ]
    else:
        lines += [
            "",
            f"**Simulator:** {result.get('simulator', '?')}",
            f"**Frequency points:** {result['n_freq_points']}",
            f"**Sweep range:** {result['freq_range_hz'][0] / 1e6:.1f} – {result['freq_range_hz'][1] / 1e6:.1f} MHz",
            "",
            "## Δ|S21| summary",
            "",
            f"- max Δ|S21| (overall): {result['max_delta_db']:.2f} dB",
            f"- max Δ|S21| (passband): {result['max_delta_passband_db']:.2f} dB "
            f"(threshold {result['passband_threshold_db']:.1f} dB)",
            f"- max Δ|S21| (stopband): {result['max_delta_stopband_db']:.2f} dB "
            f"(threshold {result['stopband_threshold_db']:.1f} dB)",
            f"- max Δ|S11|: {result.get('max_delta_s11_db', float('nan')):.2f} dB",
            f"- max Δphase: {result['max_delta_phase_deg']:.1f} °",
            "",
        ]
        if result["flagged_regions"]:
            lines += [
                "## Flagged regions",
                "",
                "| Region | Range (MHz) | Max Δ (dB) | Threshold (dB) |",
                "|--------|-------------|------------|----------------|",
            ]
            for fr in result["flagged_regions"]:
                lines.append(
                    f"| {fr['region']} | {fr['f_low_hz'] / 1e6:.1f} – {fr['f_high_hz'] / 1e6:.1f} | "
                    f"{fr['max_delta_db']:.2f} | {fr['threshold_db']:.1f} |"
                )
        else:
            lines.append("**No flagged regions** — analytical and SPICE agree within thresholds.")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
