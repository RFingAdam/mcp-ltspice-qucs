#!/usr/bin/env python3
"""End-to-end HaLow LPF design using only the mcp-ltspice MCP tools.

Runs the 8-step workflow from the project README headline demo:

  1. Load coex spec
  2. Synthesize 7th-order elliptic LPF
  3. Place 3 transmission zeros at the 2f0 EU / 2f0 NA / 3f0 NA targets
  4. Substitute Coilcraft 0402HP + Murata GJM C0G real-world parts
  5. Compute Touchstone analytically (no simulator required)
  6. Check against the spec
  7. If failing, optimize the worst margins
  8. Monte Carlo yield analysis at 5% component tolerance

Outputs land next to this file:

  - ``starting_point.s2p``: synthesized prototype
  - ``after_zero_placement.s2p``: after relocating notches
  - ``with_real_parts.s2p``: after vendor part substitution
  - ``final.s2p``: post-optimization
  - ``response.png``: S21 / S11 Bode plot with marker lines
  - ``report.md``: pass/fail spec table + Monte Carlo yield
  - ``final.asc``: LTspice schematic ready for further design work
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from mcp_ltspice.asc_io import generate_lpf_asc
from mcp_ltspice.eval import FilterSpec, evaluate_filter_spec
from mcp_ltspice.extract import (
    components_dict_to_elements,
    ladder_sparams_from_components,
)
from mcp_ltspice.find_zeros import find_transmission_zeros
from mcp_ltspice.montecarlo import monte_carlo_analysis
from mcp_ltspice.optimize import optimize_filter
from mcp_ltspice.render import render_response
from mcp_ltspice.synthesis import (
    Topology,
    place_transmission_zero,
    synthesize_lc_lpf,
)
from mcp_ltspice.vendor_models import substitute_real_components
from rf_mcp_common.touchstone import network_to_touchstone

HERE = Path(__file__).parent
SPEC_PATH = HERE / "spec.json"


def _write_s2p(components: dict[str, float], path: Path, *, transmission_zeros: bool = True) -> Path:
    """Render LC ladder analytically and dump as Touchstone."""
    f = np.geomspace(10e6, 5e9, 1001)
    elements = components_dict_to_elements(
        components, topology="series_first",
        transmission_zeros=transmission_zeros,
    )
    s = ladder_sparams_from_components(elements, f, z0=50.0)
    return network_to_touchstone(f, s, path, z0=50.0)


def _print_check(label: str, result) -> None:
    print(f"\n=== {label} ({result.overall.upper()}) ===")
    print(f"{'Criterion':<30} {'Target':>10} {'Measured':>12} {'Margin':>10} {'Status':>8}")
    for c in result.criteria:
        target = f"{c.target_db:.1f} dB"
        measured = f"{c.measured_db:.2f} dB" if math.isfinite(c.measured_db) else "n/a"
        margin = f"{c.margin_db:+.2f} dB" if math.isfinite(c.margin_db) else "n/a"
        print(
            f"{c.label:<30} {target:>10} {measured:>12} {margin:>10} {c.status:>8}"
        )


def main() -> None:
    spec = FilterSpec.model_validate(json.loads(SPEC_PATH.read_text()))
    print("Spec loaded:", SPEC_PATH.name)

    # ------------------------------------------------------------------
    # Step 2: Synthesize prototype (9th-order elliptic, fc=1.0 GHz)
    # 9th order gives ~10 dB extra rejection over 7th, which is what we
    # need to hit 55 dB on both 2f0 targets with E96-snap-friendly margins.
    # ------------------------------------------------------------------
    print("\n[2] Synthesizing 9th-order elliptic LPF (fc=1.0 GHz, 0.1 dB ripple, 50 dB stopband)")
    design = synthesize_lc_lpf(
        "elliptic", order=9, cutoff_hz=1.0e9,
        ripple_db=0.1, stopband_atten_db=50,
        z0=50.0, topology=Topology.SERIES_FIRST,
    )
    print(f"   {len(design.components)} components synthesized")
    for ref, val in sorted(design.components.items()):
        print(f"     {ref}: {val:.3e}")
    print(f"   Synthesized transmission zeros (Hz): {[f'{z/1e6:.1f} MHz' for z in design.transmission_zeros_hz]}")
    starting = _write_s2p(design.components, HERE / "starting_point.s2p")
    print(f"   --> {starting.name}")

    # ------------------------------------------------------------------
    # Step 3: Place zeros at the EU 2f0 / NA 2f0 / NA 3f0 targets
    # ------------------------------------------------------------------
    # 9th-order elliptic in T-topology has 4 traps: L2/C2, L4/C4, L6/C6, L8/C8.
    # Place them at the four coex targets we most care about (EU 2f0, NA 2f0,
    # GPS L1 protection, NA 3f0). GPS L1 is in the FCC restricted band so
    # extra rejection there is genuinely valuable.
    print("\n[3] Placing transmission zeros at coex targets (1.575 / 1.73 / 1.87 / 2.78 GHz)")
    comps = dict(design.components)
    zero_targets = [(2, 1.575e9), (4, 1.73e9), (6, 1.87e9), (8, 2.78e9)]
    for trap_idx, freq in zero_targets:
        result = place_transmission_zero(
            comps, trap_index=trap_idx, target_freq_hz=freq,
            preserve_ratio=True, snap_series=None,
        )
        comps = result["components"]
        print(
            f"   Trap L{trap_idx}/C{trap_idx} -> {freq/1e9:.3f} GHz "
            f"(achieved {result['achieved_freq_hz']/1e9:.3f} GHz, "
            f"err {result['freq_error_pct']:+.2f}%)"
        )
    after_zeros = _write_s2p(comps, HERE / "after_zero_placement.s2p")
    print(f"   --> {after_zeros.name}")
    check1 = evaluate_filter_spec(after_zeros, spec)
    _print_check("After zero placement", check1)

    # ------------------------------------------------------------------
    # Step 4: Substitute Coilcraft + Murata real-world parts
    # ------------------------------------------------------------------
    print("\n[4] Substituting Coilcraft 0402HP inductors + Murata GJM C0G capacitors")
    parts = substitute_real_components(comps)
    real_comps = {ref: info["snapped_value"] for ref, info in parts.items()}
    for ref, info in sorted(parts.items()):
        delta_pct = (info["snapped_value"] - info["ideal_value"]) / info["ideal_value"] * 100
        srf_str = f"SRF={info['srf_hz']/1e9:.2f} GHz" if "srf_hz" in info else ""
        print(
            f"   {ref}: ideal {info['ideal_value']:.3e} -> "
            f"vendor {info['snapped_value']:.3e} ({delta_pct:+.1f}%) [{srf_str}]"
        )
    real_s2p = _write_s2p(real_comps, HERE / "with_real_parts.s2p")
    print(f"   --> {real_s2p.name}")
    check2 = evaluate_filter_spec(real_s2p, spec)
    _print_check("With real parts", check2)

    # ------------------------------------------------------------------
    # Step 5: Optimize against any failing criteria
    # ------------------------------------------------------------------
    print("\n[5] Optimizing for spec compliance (Nelder-Mead, 3000 iter, E96 snap)")
    opt_result = optimize_filter(
        real_comps, spec,
        transmission_zeros=True, z0=50.0,
        max_iter=3000, snap_series="E96",
    )
    print(f"   loss: {opt_result.initial_loss:.3f} -> {opt_result.final_loss:.3f}")
    print(f"   converged: {opt_result.converged} ({opt_result.n_iterations} iterations)")
    final_comps = opt_result.snapped_components
    for ref, val in sorted(final_comps.items()):
        delta = (val - real_comps[ref]) / real_comps[ref] * 100
        print(f"     {ref}: {val:.3e} ({delta:+.1f}% from real-parts start)")
    final_s2p = _write_s2p(final_comps, HERE / "final.s2p")
    print(f"   --> {final_s2p.name}")
    check3 = evaluate_filter_spec(final_s2p, spec)
    _print_check("Final (after optimization + E24 snap)", check3)

    # ------------------------------------------------------------------
    # Step 6: Find achieved transmission zeros
    # ------------------------------------------------------------------
    found_zeros = find_transmission_zeros(final_s2p, min_depth_db=15)
    print("\n[6] Detected transmission zeros in final design:")
    for z in found_zeros:
        print(f"     {z['freq_hz']/1e9:.3f} GHz, depth {z['depth_db']:.1f} dB, Q~={z['q_factor']:.0f}")

    # ------------------------------------------------------------------
    # Step 7: Generate the .asc schematic
    # ------------------------------------------------------------------
    asc = generate_lpf_asc(
        final_comps, HERE / "final.asc",
        topology="lpf_t_elliptic", z0=50.0,
        f_start_hz=10e6, f_stop_hz=5e9,
    )
    print(f"\n[7] Wrote LTspice schematic --> {asc.name}")

    # ------------------------------------------------------------------
    # Step 8: Render plot
    # ------------------------------------------------------------------
    png = render_response(
        final_s2p, HERE / "response.png",
        markers=[
            (928e6, "passband edge"),
            (1575e6, "GPS L1"),
            (1730e6, "EU 2f0"),
            (1853e6, "NA 2f0"),
            (2400e6, "ISM low"),
            (2484e6, "ISM high"),
            (2780e6, "NA 3f0"),
        ],
        title="HaLow LPF -- final design (post-optimization, E24 snapped)",
    )
    print(f"   Wrote response plot --> {png.name}")

    # ------------------------------------------------------------------
    # Step 9: Monte Carlo yield analysis
    # ------------------------------------------------------------------
    # 2% tolerance is realistic for E96 RF inductors (Coilcraft ±2% std grade)
    mc_tol_pct = 2.0
    mc_n_runs = 1000
    print(f"\n[8] Monte Carlo yield ({mc_tol_pct}% tolerance, {mc_n_runs} runs, parallel)")
    mc = monte_carlo_analysis(
        final_comps, spec,
        tolerance_pct=mc_tol_pct, n_runs=mc_n_runs,
        transmission_zeros=True, n_jobs=-1,
    )
    print(f"   yield: {mc.yield_pct:.1f}% ({mc.n_passing}/{mc.n_runs} passing)")
    print("   top failing criteria:")
    for label, count in sorted(mc.failing_criteria_counts.items(), key=lambda kv: -kv[1])[:5]:
        print(f"     - {label}: {count} failures ({100*count/mc.n_runs:.1f}%)")

    # ------------------------------------------------------------------
    # Step 10: Write report.md
    # ------------------------------------------------------------------
    report_path = HERE / "report.md"
    _write_report(
        report_path, check1, check2, check3, mc, final_comps, parts, found_zeros,
        mc_tol_pct=mc_tol_pct,
    )
    print(f"\n[9] Wrote report --> {report_path.name}")

    # ------------------------------------------------------------------
    # Final pass/fail summary
    # ------------------------------------------------------------------
    print(f"\n{'='*60}")
    print(f"FINAL VERDICT: {check3.overall.upper()}")
    print(f"Monte Carlo yield: {mc.yield_pct:.1f}%")
    print(f"{'='*60}")


def _write_report(
    path, check1, check2, check_final, mc, comps, parts, zeros,
    *, mc_tol_pct: float = 5.0,
) -> None:
    lines = [
        "# HaLow LPF Final Design Report",
        "",
        "End-to-end design driven by the `mcp-ltspice` server (synthesis ->",
        "zero placement -> vendor substitution -> optimization -> MC yield).",
        "",
        "## Final BOM",
        "",
        "| Refdes | Ideal value | Vendor value | Vendor part | SRF |",
        "|---|---|---|---|---|",
    ]
    for ref in sorted(comps.keys()):
        info = parts.get(ref, {})
        ideal = info.get("ideal_value", "--")
        vendor_val = comps[ref]
        vendor = info.get("vendor", "--")
        srf = info.get("srf_hz", None)
        ideal_str = f"{ideal:.3e}" if isinstance(ideal, (int, float)) else str(ideal)
        vendor_str = f"{vendor_val:.3e}"
        srf_str = f"{srf/1e9:.2f} GHz" if srf else "--"
        lines.append(f"| {ref} | {ideal_str} | {vendor_str} | {vendor} | {srf_str} |")
    lines.append("")

    lines.append("## Spec Compliance -- final design")
    lines.append("")
    lines.append("| Criterion | Target | Measured | Margin | Status |")
    lines.append("|---|---|---|---|---|")
    for c in check_final.criteria:
        m = f"{c.measured_db:+.2f} dB" if math.isfinite(c.measured_db) else "n/a"
        margin = f"{c.margin_db:+.2f} dB" if math.isfinite(c.margin_db) else "n/a"
        status = "PASS" if c.status == "pass" else "FAIL"
        lines.append(f"| {c.label} | {c.target_db:.1f} dB | {m} | {margin} | {status} |")
    lines.append(f"\n**Overall: {check_final.overall.upper()}**")
    lines.append("")

    lines.append("## Monte Carlo Yield Analysis")
    lines.append("")
    lines.append(f"- Component tolerance: {mc_tol_pct}% (3 sigma)")
    lines.append(f"- Trials: {mc.n_runs}")
    lines.append(f"- **Yield: {mc.yield_pct:.1f}%** ({mc.n_passing} / {mc.n_runs})")
    if mc.failing_criteria_counts:
        lines.append("")
        lines.append("Failing criteria breakdown:")
        for label, count in sorted(mc.failing_criteria_counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"- {label}: {count} ({100*count/mc.n_runs:.1f}%)")
    lines.append("")

    lines.append("## Detected transmission zeros (final design)")
    lines.append("")
    lines.append("| Frequency | Depth | Q |")
    lines.append("|---|---|---|")
    for z in zeros:
        lines.append(
            f"| {z['freq_hz']/1e9:.3f} GHz | {z['depth_db']:.1f} dB | {z['q_factor']:.0f} |"
        )

    lines.append("")
    lines.append("## Per-metric statistics across all MC trials")
    lines.append("")
    lines.append("| Metric | Mean | Std | p05 | p50 | p95 |")
    lines.append("|---|---|---|---|---|---|")
    for name, stats in mc.per_metric_stats.items():
        lines.append(
            f"| {name} | {stats['mean']:.2f} | {stats['std']:.2f} | "
            f"{stats['p05']:.2f} | {stats['p50']:.2f} | {stats['p95']:.2f} |"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
