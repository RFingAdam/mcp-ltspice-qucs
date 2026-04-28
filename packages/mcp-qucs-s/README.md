# mcp-qucs-s

MCP server exposing **Qucs-S** for RF-specific simulation that LTspice
handles poorly: native S-parameter analysis, harmonic balance,
distributed-element synthesis.

Part of the [mcp-ltspice-qucs](../../README.md) suite.

## Tools

| Tool | Status | Purpose |
|---|---|---|
| `status` | ✅ implemented | Capability discovery — reports whether Qucs-S / Xyce are installed |
| `synthesize_microstrip_line` | ✅ implemented | Closed-form Hammerstad-Jensen W/L from Z₀, εr, h, t, tan δ |
| `analyze_microstrip_tool` | ✅ implemented | Z₀ / εeff / wavelength from existing W/h |
| `synthesize_coupler` | ✅ implemented | Branch-line, rat-race, Lange, coupled-line (single-line approx — gap synthesis pending) |
| `lumped_to_distributed` | ✅ implemented | Richards transformation + Kuroda identities for lumped→stub conversion |
| `run_sp_analysis` | ✅ implemented | Native Qucs-S S-parameter sim, `.dat` parser, Touchstone output |
| `export_touchstone` | ✅ implemented | Run + export `.s2p` for cross-tool consumption |
| `run_harmonic_balance` | ⚠️ scaffolded | Detects Xyce; netlist generation + harmonic-content parsing pending (Tier-6 roadmap) |
| `extract_noise_parameters` | ⚠️ scaffolded | Detects Qucs-S; noise dataset parser (Fmin, Γopt, Rn, NF50) pending (Tier-6 roadmap) |

> **Note:** Tools marked ⚠️ scaffolded return an `error()` envelope with a clear "not yet implemented" message — they do **not** return placeholder data masquerading as success.

## Backends

- **Qucs-S + Ngspice** — primary, sufficient for the seven implemented tools above.
- **Qucs-S + Xyce** — required for `run_harmonic_balance` once that tool is fully implemented. Today, even with Xyce installed, the tool returns a "not yet implemented" error.
