<p align="center">
  <img src="assets/logo.svg" alt="mcp-ltspice-qucs" width="480">
</p>

<p align="center">
  <a href="https://opensource.org/licenses/Apache-2.0"><img src="https://img.shields.io/badge/License-Apache_2.0-blue.svg" alt="License: Apache 2.0"></a>
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11%2B-blue.svg" alt="Python 3.11+"></a>
  <a href="https://github.com/astral-sh/ruff"><img src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json" alt="Code style: ruff"></a>
  <a href="https://github.com/RFingAdam/mcp-ltspice-qucs/actions/workflows/ci.yml"><img src="https://github.com/RFingAdam/mcp-ltspice-qucs/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI"></a>
</p>

# mcp-ltspice-qucs

A three-server **Model Context Protocol (MCP)** suite that turns RF filter
design and multi-radio coexistence engineering into a fluent agent
workflow. Use **LTspice** and **Qucs-S** through domain-aware abstractions
("place a transmission zero at 1853 MHz", "evaluate against this coex
spec") instead of SPICE primitives.

## Why this exists

Designing a single coexistence-aware filter today looks like: hours in
LTspice nudging component values, swapping vendor SPICE models by hand,
re-running the sim, eyeballing the S21 trace, repeat. This suite codifies
the workflow so an LLM agent can iterate at the **design intent** layer,
collapsing each iteration from minutes to seconds while keeping a
human-engineer in the loop for judgment calls.

## The three servers

| Server | Purpose |
|---|---|
| **`mcp-ltspice`** | LTspice (and ngspice fallback) for lumped-element filter synthesis, S-parameter extraction, vendor model substitution, optimization, Monte Carlo |
| **`mcp-qucs-s`** | Qucs-S for native S-parameter sims, harmonic balance, microstrip / distributed-element synthesis, Richards/Kuroda lumped→distributed conversion |
| **`mcp-rf-analysis`** | Simulator-agnostic skrf wrappers, LTE/5G NR/GNSS/ISM/HaLow band databases, FCC/ETSI/3GPP spec evaluation, multi-radio coex matrix |

All three speak **Touchstone** (`.s2p`/`.snp`) as the cross-tool exchange
format.

## Headline demo

The [basic LPF example](examples/basic_lpf/) synthesizes a 5th-order
Butterworth low-pass filter at fc = 1 GHz, substitutes Coilcraft 0402HP
and Murata GJM C0G real parts, evaluates against a generic spec, and
runs a 1000-trial Monte Carlo at 5% component tolerance — all through
MCP tool calls. **All 5 spec criteria pass with 99% yield.**

![Basic LPF response](examples/basic_lpf/response.png)

| Criterion | Target | Measured | Margin |
|---|---|---|---|
| Passband IL | ≤ 0.5 dB | 0.02 dB | +0.48 dB |
| Passband RL | ≥ 14 dB | 24.57 dB | +10.57 dB |
| 2 × fc | ≥ 30 dB | 30.85 dB | +0.85 dB |
| 3 × fc | ≥ 45 dB | 48.16 dB | +3.16 dB |
| 5 × fc | ≥ 60 dB | 70.16 dB | +10.16 dB |

Run your own designs under `examples/private/` or `examples/_local/`
(both gitignored).

## Quickstart

```bash
git clone https://github.com/RFingAdam/mcp-ltspice-qucs
cd mcp-ltspice-qucs
uv sync --all-packages
uv run python examples/basic_lpf/design.py
```

See [`docs/installation.md`](docs/installation.md) for ngspice / LTspice /
Qucs-S setup, and [`docs/architecture.md`](docs/architecture.md) for the
interop contract between servers.

## Scope and related MCP servers

This MCP suite is **circuit-level + filter-synthesis** focused. It deliberately
stops at the antenna port and at the schematic-to-layout boundary. For domains
this suite does *not* cover:

- **Antenna design** (radiation patterns, NEC2, FDTD) → use **`nec2-antenna`** or **`openems`**
- **PCB-level EMC / SI / PI** (impedance from layout, decoupling, return paths,
  crosstalk from geometry) → use **`pcb-emcopilot`**
- **Regulatory standards lookup** (CISPR / FCC / ETSI / 3GPP / IEC / ISO,
  certification matrix, market requirements) → use **`emc-regulations`**
- **Physical-layer testing** (BLE / WiFi / HaLow on real hardware) → use **`rf-test`**

See [`docs/related-mcp-servers.md`](docs/related-mcp-servers.md) for the full
boundary statement, decision flow, and cross-MCP workflow examples.

## License

Apache-2.0. See [LICENSE](LICENSE).
