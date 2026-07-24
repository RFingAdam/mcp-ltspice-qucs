# mcp-qucs-s

MCP server exposing **Qucs-S** for RF-specific simulation that LTspice
handles poorly: native S-parameter analysis, harmonic balance,
distributed-element synthesis.

Part of the [mcp-ltspice-qucs](../../README.md) suite.

## Tools

The server exposes 29 tools:

| Area | Tools |
|---|---|
| Capability and substrate data | `status`, `probe_backend`, `list_substrate_presets_tool` |
| Planar synthesis | `synthesize_microstrip_line`, `analyze_microstrip_tool`, `synthesize_coupler`, `lumped_to_distributed` |
| Distributed filters | `synthesize_stepped_impedance_lpf`, `synthesize_coupled_line_bpf`, `synthesize_hairpin_bpf`, `synthesize_interdigital_bpf`, `synthesize_combline_bpf` |
| Qucsator simulation | `run_sp_analysis`, `simulate_lc_ladder`, `export_touchstone`, `extract_noise_parameters` |
| Xyce nonlinear analysis | `run_harmonic_balance`, `sweep_compression_point` |
| Circuit workbench | `workspace_create`, `artifact_import`, `circuit_parse`, `circuit_validate`, `circuit_export` (plus the `artifact://` resource) |
| Durable simulation jobs | `simulation_submit`, `job_get`, `job_cancel`, `job_retry`, `job_list_artifacts`, `artifact_read` |

The synthesis tools are closed-form and require no external simulator.
Simulator tools return a clear error envelope when their backend is absent.

## Backends

- **qucsator-RF** — native S-parameter and noise analysis, parsed to
  Touchstone or structured noise parameters.
- **Xyce** — harmonic balance, intermodulation metrics, and P1dB sweeps.

Both drivers reject non-zero exits and stale, empty, or malformed output.
Each invocation runs in an isolated workspace and emits a manifest.
Required hosted CI does not install these backends. Their integration tests
run in the scheduled capability-labelled self-hosted matrix and skip locally
when the executable is absent.
