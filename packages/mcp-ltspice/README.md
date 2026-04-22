# mcp-ltspice

MCP server exposing **LTspice** (and **ngspice** as a fallback) for RF
filter synthesis and analysis.

Part of the [mcp-ltspice-qucs](../../README.md) suite.

## Tools

| Tool | Purpose |
|---|---|
| `run_simulation` | Headless LTspice/-b execution; returns raw file path |
| `extract_sparameters` | AC sim → 2-port S-params → Touchstone .s2p |
| `synthesize_lc_filter` | Elliptic / Chebyshev / Butterworth LPF/HPF/BPF synthesis with T or Pi topology |
| `place_transmission_zero` | Move a shunt-trap zero to a target frequency, snap to E24/E96 |
| `find_transmission_zeros` | Peak-detect notches in S21 |
| `substitute_real_components` | Swap ideal L/C for vendor SPICE subcircuits (Coilcraft, Murata, Johanson, TDK) |
| `evaluate_filter_spec` | Pass/fail per criterion with margin in dB |
| `optimize_filter` | Iterative tuning against a spec, E24-snapped final values |
| `monte_carlo_analysis` | N-run yield + per-metric histograms with component tolerances |
| `render_response` | S21/S11 Bode plot PNG with frequency markers |
| `stability_check` | K-factor, Δ, μ-factor across frequency for amplifier circuits |

## Backends

- **LTspice** via Wine — full `.asc` round-trip, native subcircuit
  handling, `.meas` directives. Required for tool fidelity, optional
  for CI.
- **ngspice** — drop-in fallback, used in CI. The runner transparently
  rewrites `.asc` to a netlist when LTspice is unavailable.

See [`../../docs/installation.md`](../../docs/installation.md) for setup.
