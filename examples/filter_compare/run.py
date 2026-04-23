#!/usr/bin/env python3
"""Compare 5th, 7th, and 9th-order elliptic LPF designs side-by-side.

Demonstrates the ``compare_filter_orders`` tool: synthesizes each
order, places transmission zeros at the priority targets, vendor-
substitutes Coilcraft + Murata real parts, vendor-bound-optimizes,
runs Monte Carlo at 2% tolerance, scores each, and prints the most
shippable choice.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp_ltspice.compare import compare_filter_orders
from mcp_ltspice.eval import FilterSpec

HERE = Path(__file__).parent


def main() -> None:
    spec = FilterSpec.model_validate(json.loads((HERE / "spec.json").read_text()))
    print("Comparing 5/7/9-order elliptic LPFs against the spec at spec.json")

    result = compare_filter_orders(
        orders=[5, 7, 9],
        cutoff_hz=1.0e9,
        spec=spec,
        # Zero-target priority list. Each order takes the first n_traps
        # from this list (5th uses 2, 7th uses 3, 9th uses 4).
        zero_targets_hz=[1.6e9, 2.0e9, 2.5e9, 3.5e9],
        ripple_db=0.1,
        stopband_atten_db=50,
        mc_n_runs=500,  # smaller for speed in the demo
        optimize_max_iter=800,
        s2p_dir=str(HERE),  # save .s2p files alongside this script
    )

    print(f"\n{'Order':<7}{'Components':<12}{'Spec':<8}{'Yield':<10}{'SRF':<11}{'Score':<8}")
    print("-" * 70)
    for r in sorted(result.results, key=lambda x: -x.score):
        marker = " <- WINNER" if r.order == result.winner_order else ""
        print(
            f"{r.order:<7}{r.n_components:<12}{r.spec_overall:<8}"
            f"{r.mc_yield_pct:>6.1f}%   {r.srf_severity:<11}{r.score}{marker}"
        )

    print(f"\nMost shippable: order {result.winner_order}")
    print(f"Rationale: {result.winner_rationale}")

    # Write a markdown summary
    _write_report(HERE / "report.md", result)
    print(f"\nFull report -> {(HERE / 'report.md').name}")


def _write_report(path: Path, res) -> None:
    lines = [
        "# Filter order comparison",
        "",
        "Generic 5/7/9-order elliptic LPF comparison driven by the",
        "`compare_filter_orders` tool. Spec at `spec.json`.",
        "",
        f"**Winner: order {res.winner_order}** — {res.winner_rationale}",
        "",
        "## Score table",
        "",
        "| Order | Components | Spec | MC yield (2% tol) | SRF | Score |",
        "|---|---|---|---|---|---|",
    ]
    for r in sorted(res.results, key=lambda x: -x.score):
        marker = " ← shippable" if r.order == res.winner_order else ""
        lines.append(
            f"| **{r.order}{marker}** | {r.n_components} | "
            f"{r.spec_overall} | {r.mc_yield_pct:.1f}% | "
            f"{r.srf_severity} | {r.score} |"
        )
    lines.append("")

    for r in res.results:
        lines.append(f"## Order {r.order}")
        lines.append("")
        lines.append(f"- **Components:** {r.n_components}")
        lines.append(f"- **Traps used:** {r.n_traps_used}")
        lines.append(f"- **Spec:** {r.spec_overall}")
        lines.append(f"- **MC yield (2% tol):** {r.mc_yield_pct:.1f}%")
        lines.append(f"- **SRF severity:** {r.srf_severity} ({r.n_srf_flagged} flagged)")
        if r.most_sensitive_component:
            lines.append(f"- **Most-influential component:** {r.most_sensitive_component}")
        if r.s2p_path:
            lines.append(f"- **Touchstone:** `{Path(r.s2p_path).name}`")
        lines.append("")
        lines.append("**BOM:**")
        lines.append("")
        lines.append("| Refdes | Value |")
        lines.append("|---|---|")
        for ref in sorted(r.components.keys()):
            v = r.components[ref]
            unit = "nH" if ref.startswith("L") else "pF"
            scaled = v * (1e9 if ref.startswith("L") else 1e12)
            lines.append(f"| {ref} | {scaled:.3g} {unit} |")
        lines.append("")
        lines.append("**Spec compliance:**")
        lines.append("")
        lines.append("| Criterion | Target | Measured | Margin | Status |")
        lines.append("|---|---|---|---|---|")
        for c in r.criteria:
            m = f"{c['measured_db']:+.2f} dB"
            margin = f"{c['margin_db']:+.2f} dB"
            status = "✅" if c["status"] == "pass" else "❌"
            lines.append(f"| {c['label']} | {c['target_db']:.1f} dB | {m} | {margin} | {status} |")
        lines.append("")
        lines.append("---")
        lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
