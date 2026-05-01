# mcp-ltspice

MCP server exposing **LTspice** (and **ngspice** as a fallback) for
RF filter synthesis, SMPS-EMC pre-compliance, and analog active-filter
design. Part of the [mcp-ltspice-qucs](../../README.md) suite.

This is the largest of the three servers — 56 tools today, each
registered under both a flat name and a categorised alias
(`filter.*`, `power.*`, `analog.*`, `digital.*`, `vendor.*`,
`sim.*`). The table below is a curated tour, not the full list;
the canonical surface is `@mcp.tool` registrations in
`src/mcp_ltspice/server.py`.

## Tour

### Filter synthesis (`filter.*`)

| Tool | Purpose |
|---|---|
| `synthesize_lc_filter` | LC ladder LPF — Butterworth / Chebyshev / Elliptic, T or Pi topology |
| `synthesize_lc_hpf` | High-pass via the Pozar §8.5 LPF→HPF transformation |
| `synthesize_lc_bpf` | Band-pass — series-LC and shunt-LC tanks at f₀ = √(f_low·f_high) |
| `synthesize_lc_bsf` | Band-stop — anti-resonant series-LC, resonant shunt-LC |
| `place_transmission_zero` | Move a shunt-trap zero to a target frequency, snap to E24/E96 |
| `find_transmission_zeros` | Peak-detect notches in an S21 trace |
| `substitute_real_components` | Swap ideal L/C for vendor parts (Coilcraft, Johanson, TDK, Murata) with SRF / Q / ESR honoured |
| `evaluate_filter_spec` | Pass/fail per criterion with margin in dB |
| `optimize_filter` | Iterative tuning against a spec, E24-snapped final values |
| `monte_carlo_analysis` | Yield + per-metric histograms with component tolerances; `trace=True` writes per-trial JSONL |
| `compare_filter_orders` | Bake-off across orders against the same spec |
| `srf_audit` | Flag components whose SRF intrudes on the design band |
| `render_response` | S21 / S11 Bode PNG with frequency markers |
| `render_lc_schematic` | Auto-rendered ladder schematic (PNG / SVG) |
| `build_design_report_pdf` | Combine response, schematic, MC, BOM into a single PDF |

### Power-supply EMC (`power.*`) — v0.2.0

| Tool | Purpose |
|---|---|
| `design_pi_output_filter` | Pi-section LC output filter (C-L-C) sized for a target attenuation |
| `design_dm_input_filter` | 2nd-order LC input filter with Middlebrook stability check |
| `predict_conducted_emissions` | Trapezoidal switch-node spectrum vs CISPR 22 / 32 (Class A/B, QP/AVG) |
| `design_rc_snubber` | RC snubber for switch-node ringing |
| `design_cm_choke` | Common-mode choke selection from a curated catalogue (Würth / TDK / Murata) |
| `design_buck`, `design_boost`, `analyze_ldo` | Topology sizing |
| `type2_compensator`, `compute_phase_margin` | Type-II loop compensation + Bode |

### Analog (`analog.*`)

| Tool | Purpose |
|---|---|
| `sallen_key_lpf` / `sallen_key_hpf` / `sallen_key_bpf` | Single-stage active filter synthesis |
| `mfb_lpf` / `mfb_bpf` | Multiple-feedback active filters |
| `cascaded_lpf_design` | N-th order via cascaded biquads (Mancini stage tables) |

### Simulator + utilities (`sim.*`, `vendor.*`)

| Tool | Purpose |
|---|---|
| `run_simulation` | Headless LTspice (`-b`) or ngspice; returns raw file path |
| `extract_sparameters` | AC sim → 2-port S-params → Touchstone `.s2p` |
| `stability_check` | K-factor, Δ, μ-factor for amplifier circuits |
| `find_opamp_for_application`, `find_mosfet_for_*`, `find_bjt_*`, `find_diode_*`, `list_vendor_parts` | Component catalogue queries |

## Backends

- **LTspice** via Wine — full `.asc` round-trip, native subcircuit
  handling, `.meas` directives. Required for tool fidelity, optional
  for CI.
- **ngspice** — drop-in fallback, used in CI. The runner transparently
  rewrites `.asc` to a netlist when LTspice is unavailable.

See [`../../docs/installation.md`](../../docs/installation.md) for setup.
