# Backend and client validation matrix

The suite reports runtime readiness separately from CI coverage. A backend can
be `installed`, `launchable`, `validated`, or `unavailable`; only `validated`
means its tiny known-answer simulation completed in the current environment.
Read `capabilities://ltspice`, `capabilities://qucs-s`, or
`capabilities://rf-analysis`, or call the corresponding `probe_backend` tool.

<!-- BEGIN GENERATED -->
| Backend | Server | Declared analyses | Readiness validation | CI tier |
|---|---|---|---|---|
| LTspice | `mcp-ltspice` | `ac`, `transient`, `dc`, `noise` | Known-answer file run | Scheduled native Windows + Wine self-hosted |
| ngspice | `mcp-ltspice` | `ac`, `transient`, `dc`, `noise` | Known-answer netlist run | Required Linux per-commit |
| Qucsator-RF | `mcp-qucs-s` | `sparameters`, `noise` | Known-answer S-parameter run | Scheduled capability-labelled self-hosted |
| Xyce | `mcp-qucs-s` | `harmonic_balance` | Known-answer harmonic-balance run | Scheduled capability-labelled self-hosted |
| Python numerical stack | `mcp-rf-analysis` | `touchstone`, `network_operations`, `tdr`, `eye_diagram`, `equivalent_circuit_fit`, `coexistence`, `emc_estimation` | Known-answer matched-through cascade | Required Linux/Windows per-commit |
<!-- END GENERATED -->

## CI tiers

- [CI](https://github.com/RFingAdam/mcp-ltspice-qucs/actions/workflows/ci.yml)
  is required per commit. It runs static checks, locked and minimum dependency
  contracts, real stdio initialize/list/call/error tests, isolated wheel
  installation, Windows discovery, and a real ngspice known answer.
- [Extended simulator and MCP client matrix](https://github.com/RFingAdam/mcp-ltspice-qucs/actions/workflows/extended-matrix.yml)
  runs weekly and on demand on capability-labelled self-hosted runners. It is
  the truthful execution tier for Qucsator-RF, Xyce, LTspice native/Wine,
  current Codex, and current Claude Code.
- [Release validation](https://github.com/RFingAdam/mcp-ltspice-qucs/actions/workflows/release-validation.yml)
  rebuilds documentation and contract snapshots, audits dependencies, builds
  all distributions, emits an SPDX SBOM, and attests artifact provenance.

An absent proprietary or specialist runner is not converted into a passing
result. The extended workflow remains queued until a runner with the declared
capability label accepts it.

## Output confidence

Tool results identify their method and backend. Use these terms consistently:

- **Simulator-validated**: a named external simulator produced the artifact,
  and the output passed freshness and structural checks.
- **Analytical**: a closed-form or network-algebra result; no circuit simulator
  was invoked.
- **Approximate**: a bounded engineering estimate with assumptions stated in
  the result.
- **Planned/unsupported**: rejected explicitly; the suite does not silently
  substitute another method or topology.
