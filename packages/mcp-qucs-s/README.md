# mcp-qucs-s

MCP server exposing **Qucs-S** for RF-specific simulation that LTspice
handles poorly: native S-parameter analysis, harmonic balance,
distributed-element synthesis.

Part of the [mcp-ltspice-qucs](../../README.md) suite.

## Tools

| Tool | Status | Purpose |
|---|---|---|
| `status` | implemented | Capability discovery — reports whether Qucs-S / Xyce are installed |
| `synthesize_microstrip_line` | implemented | Closed-form Hammerstad-Jensen W/L from Z₀, εr, h, t, tan δ. Accepts a substrate preset name or a parameter dict |
| `analyze_microstrip_tool` | implemented | Z₀ / εeff / wavelength + conductor & dielectric loss (`alpha_d_db_per_mm`, `alpha_c_db_per_mm`, `alpha_total_db_per_mm`) from existing W/h |
| `list_substrate_presets_tool` | implemented | Returns the **16 curated substrate presets** (FR-4 / Rogers RO4350B / RO4003C / RT-Duroid 5880 + 6002 / PTFE / Isola FR408HR / Taconic TLY5) with εr / h / t / tan δ + datasheet citation |
| `synthesize_coupler` | implemented | Branch-line, rat-race, Lange, coupled-line (single-line approx — gap synthesis pending) |
| `lumped_to_distributed` | implemented | Richards transformation + Kuroda identities for lumped→stub conversion |
| `run_sp_analysis` | implemented | Native Qucs-S S-parameter sim, `.dat` parser, Touchstone output |
| `export_touchstone` | implemented | Run + export `.s2p` for cross-tool consumption |
| `run_harmonic_balance` | scaffolded | Detects Xyce; netlist generation + harmonic-content parsing pending. Returns `error("not yet implemented")` |
| `extract_noise_parameters` | scaffolded | Detects Qucs-S; noise dataset parser (Fmin, Γopt, Rn, NF50) pending. Returns `error("not yet implemented")` |

Tools marked *scaffolded* return an `error()` envelope with a clear
"not yet implemented" message. They do **not** return placeholder
data masquerading as success — that pattern is worse than no tool at
all.

## Backends

- **Qucs-S + Ngspice** — primary, sufficient for the seven implemented tools above.
- **Qucs-S + Xyce** — required for `run_harmonic_balance` once that tool is fully implemented. Today, even with Xyce installed, the tool returns a "not yet implemented" error.
