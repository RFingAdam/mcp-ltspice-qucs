#!/usr/bin/env python3
"""Generic 5th-order Butterworth LPF design — public example.

Synthesizes a textbook 5th-order Butterworth low-pass filter at 1 GHz,
substitutes Coilcraft 0402HP + Murata GJM C0G real parts, evaluates
against a generic spec, runs a 1000-trial Monte Carlo with 5%
tolerance, and renders the response.

Outputs (gitignored) land alongside this file:
  - basic_lpf.asc, basic_lpf.s2p, response.png, report.md
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
from mcp_ltspice.montecarlo import monte_carlo_analysis
from mcp_ltspice.render import render_response
from mcp_ltspice.synthesis import Topology, synthesize_lc_lpf
from mcp_ltspice.vendor_models import substitute_real_components
from rf_mcp_common.touchstone import network_to_touchstone

HERE = Path(__file__).parent
SPEC_PATH = HERE / "spec.json"


def _write_s2p(components: dict[str, float], path: Path) -> Path:
    f = np.geomspace(1e6, 5e9, 801)
    elements = components_dict_to_elements(components, topology="series_first")
    s = ladder_sparams_from_components(elements, f, z0=50.0)
    return network_to_touchstone(f, s, path, z0=50.0)


def _print_check(label: str, result) -> None:
    print(f"\n=== {label} ({result.overall.upper()}) ===")
    print(f"{'Criterion':<24} {'Target':>10} {'Measured':>12} {'Margin':>10} {'Status':>8}")
    for c in result.criteria:
        target = f"{c.target_db:.1f} dB"
        measured = f"{c.measured_db:.2f} dB" if math.isfinite(c.measured_db) else "n/a"
        margin = f"{c.margin_db:+.2f} dB" if math.isfinite(c.margin_db) else "n/a"
        print(f"{c.label:<24} {target:>10} {measured:>12} {margin:>10} {c.status:>8}")


def main() -> None:
    spec = FilterSpec.model_validate(json.loads(SPEC_PATH.read_text()))
    print("Spec loaded:", SPEC_PATH.name)

    # 1. Synthesize prototype
    print("\n[1] Synthesizing 5th-order Butterworth LPF (fc=1 GHz)")
    design = synthesize_lc_lpf(
        "butterworth",
        order=5,
        cutoff_hz=1.0e9,
        z0=50.0,
        topology=Topology.SERIES_FIRST,
    )
    for ref, val in sorted(design.components.items()):
        print(f"   {ref}: {val:.3e}")

    # 2. Substitute vendor parts
    print("\n[2] Substituting Coilcraft 0402HP + Murata GJM C0G")
    parts = substitute_real_components(design.components)
    real_comps = {ref: info["snapped_value"] for ref, info in parts.items()}
    for ref, info in sorted(parts.items()):
        delta = (info["snapped_value"] - info["ideal_value"]) / info["ideal_value"] * 100
        print(
            f"   {ref}: ideal {info['ideal_value']:.3e} -> {info['snapped_value']:.3e} ({delta:+.1f}%)"
        )

    final_s2p = _write_s2p(real_comps, HERE / "basic_lpf.s2p")
    asc = generate_lpf_asc(real_comps, HERE / "basic_lpf.asc")
    print(f"   --> {final_s2p.name}, {asc.name}")

    # 3. Evaluate
    check = evaluate_filter_spec(final_s2p, spec)
    _print_check("Spec compliance", check)

    # 4. Render
    png = render_response(
        final_s2p,
        HERE / "response.png",
        markers=[
            (1e9, "fc"),
            (2e9, "2x fc"),
            (3e9, "3x fc"),
            (5e9, "5x fc"),
        ],
        title="Generic 5th-order Butterworth LPF -- mcp-ltspice demo",
    )
    print(f"\n[3] Response plot: {png.name}")

    # 5. Monte Carlo
    print("\n[4] Monte Carlo yield (5% tolerance, 1000 runs)")
    mc = monte_carlo_analysis(
        real_comps,
        spec,
        tolerance_pct=5.0,
        n_runs=1000,
        transmission_zeros=False,
        n_jobs=-1,
    )
    print(f"   yield: {mc.yield_pct:.1f}% ({mc.n_passing}/{mc.n_runs})")

    print(f"\n{'=' * 50}")
    print(f"VERDICT: {check.overall.upper()}  |  MC yield: {mc.yield_pct:.1f}%")
    print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
