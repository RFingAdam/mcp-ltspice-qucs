# mcp-qucs-s

MCP server exposing **Qucs-S** for RF-specific simulation that LTspice
handles poorly: native S-parameter analysis, harmonic balance,
distributed-element synthesis.

Part of the [mcp-ltspice-qucs](../../README.md) suite.

## Tools

| Tool | Purpose |
|---|---|
| `run_sp_analysis` | Native S-parameter sim, Touchstone output direct |
| `run_harmonic_balance` | Spectral content via Xyce backend (PA linearity, intermod, harmonic emission) |
| `synthesize_microstrip_line` | Closed-form Hammerstad-Jensen W/L from Z₀, εr, h, t, tan δ |
| `synthesize_microstrip_filter` | Stepped-impedance / stub-loaded / coupled-line / hairpin / interdigital realizations |
| `synthesize_coupler` | Branch-line, rat-race, Lange, coupled-line directional couplers |
| `extract_noise_parameters` | Fmin, Γ_opt, Rn across frequency |
| `lumped_to_distributed` | Richards transformation + Kuroda identities for lumped→microstrip conversion |
| `export_touchstone` | Run + export `.s2p` for cross-tool consumption |

## Backends

- **Qucs-S + Ngspice** — primary, sufficient for tools 1, 3-8.
- **Qucs-S + Xyce** — required for `run_harmonic_balance`. If Xyce is
  not on `$PATH`, that tool returns an error with an install hint.
