<div align="center">

<img src="assets/logo-banner.svg" alt="mcp-ltspice-qucs — RF filter and SMPS-EMC design via LTspice, Qucs-S, and scikit-rf" width="100%"/>

<br/>

[![CI](https://github.com/RFingAdam/mcp-ltspice-qucs/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/RFingAdam/mcp-ltspice-qucs/actions/workflows/ci.yml)
[![License](https://img.shields.io/badge/License-AGPL--3.0-1E40AF.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB.svg)](https://www.python.org/downloads/)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![MCP](https://img.shields.io/badge/MCP-server-A78BFA.svg)](https://modelcontextprotocol.io)
[![eng-mcp-suite](https://img.shields.io/badge/eng--mcp--suite-member-22D3EE.svg)](https://github.com/RFingAdam/eng-mcp-suite)

**Design RF filters and switch-mode-power EMC from spec — three FastMCP servers driving LTspice, Qucs-S, and scikit-rf.**
**Iterate at the design-intent layer ("place a zero at 1.85 GHz", "predict conducted emissions against CISPR 32 Class B") from your terminal or AI agent.**

[Quick start](#quick-start) ·
[Tools](#tools) ·
[Workflows](#workflows) ·
[Documentation](#documentation)

</div>

---

## What is mcp-ltspice-qucs?

mcp-ltspice-qucs is a three-server MCP suite plus a shared contracts
library, all speaking **Touchstone** as the cross-tool exchange format.
It collapses a typical filter-design loop — hours of LTspice nudging
component values, swapping SPICE models, re-running, eyeballing S21 —
into an agent-driven iteration at the **design intent** layer.

Drive it from any MCP client. `mcp-ltspice` exposes 59 flat tools plus
matching namespaced aliases (`filter.*`, `power.*`, `analog.*`).
`mcp-qucs-s` adds native S-parameter simulation, Xyce harmonic balance,
and closed-form microstrip / distributed-filter synthesis with 16
substrate presets. `mcp-rf-analysis` adds simulator-agnostic skrf
wrappers, band databases, FCC / ETSI / 3GPP spec evaluation, and
multi-radio coexistence analysis. Every synthesis path is validated
against real simulator output — ngspice, LTspice, qucsator-RF, and
Xyce — not just its own math (876 passing tests).

**What mcp-ltspice-qucs does well:**

- **AI-native via MCP.** Three first-class [Model Context Protocol](https://modelcontextprotocol.io)
  servers. Any MCP agent can iterate filter topologies, sweep vendor
  parts, and run Monte Carlo yield analyses.
- **Real simulators, not toy math.** Drives **LTspice** (native or
  under Wine), **ngspice**, **Qucs-S / qucsator-RF** for native
  S-parameter simulation, and **Xyce** for harmonic balance. Analytical
  models are cross-checked against simulator output at millidB level.
- **Closed-form synthesis and optimization.** LC ladders (Butterworth /
  Chebyshev / elliptic, all of LPF / HPF / BPF / BSF including elliptic
  band-transforms), Sallen-Key, MFB, Richards-Kuroda, and a complete
  distributed set: stepped-impedance, edge-coupled, hairpin,
  interdigital, and combline microstrip filters on an exact
  coupled-line TEM model.
- **Coexistence-driven design.** Victim-weighted transmission-zero
  placement against LTE / 5G NR / GNSS / FCC-restricted bands, a
  GNSS ΔC/N₀ desense model, and a closed loop that iterates filter
  order until the coex matrix meets a desense target.
- **Vendor parasitics built in.** Coilcraft 0402HP, Murata GJM,
  Johanson, TDK models with SRF checks and E24/E96/E192 snap, plus
  user-supplied measured-model directories.
- **CISPR-aware.** Conducted and radiated emission prediction against
  CISPR 22 / 32 and FCC Part 15 limits, anchored to closed-form
  references, before you build.

---

## Quick start

### Install

```bash
git clone https://github.com/RFingAdam/mcp-ltspice-qucs.git
cd mcp-ltspice-qucs
uv sync --all-packages
uv run pytest -q                  # 876 pass; simulator-gated tests skip when tools are absent
uv run python examples/basic_lpf/design.py
```

See [`docs/installation.md`](docs/installation.md) for ngspice / LTspice /
Qucs-S external-tool setup.

### Wire it into your MCP client

**Claude Code:**
```bash
claude mcp add ltspice -- uv run --directory /path/to/mcp-ltspice-qucs/packages/mcp-ltspice mcp-ltspice
claude mcp add qucs-s  -- uv run --directory /path/to/mcp-ltspice-qucs/packages/mcp-qucs-s  mcp-qucs-s
claude mcp add rf-analysis -- uv run --directory /path/to/mcp-ltspice-qucs/packages/mcp-rf-analysis mcp-rf-analysis
```

Then ask your assistant:

> *"Synthesize a 5th-order Butterworth LPF at fc = 1 GHz, swap in Coilcraft 0402HP and Murata GJM C0G parts at 5% tolerance, and report yield."*

The agent calls `synthesize_lc_filter`, `substitute_real_components`,
and `monte_carlo_analysis` in sequence. Demo result on the bundled LPF
example: **all 5 spec criteria pass at 99% yield.**

---

## Tools

`mcp-ltspice-qucs` ships three MCP servers, 110 tools total:

| Server                | Tools | Purpose                                                                 |
| --------------------- | ----: | ----------------------------------------------------------------------- |
| **`mcp-ltspice`**     | 59    | LTspice + ngspice. LC ladder synthesis (incl. elliptic BPF/BSF), coex-driven closed-loop design, vendor parts, Monte Carlo, SMPS sizing + EMC, active filters, device catalogs |
| **`mcp-qucs-s`**      | 17    | Qucs-S native S-param sim + Xyce harmonic balance. Microstrip + 16 substrate presets, couplers, Richards-Kuroda, stepped-impedance / edge-coupled / hairpin / interdigital / combline filters |
| **`mcp-rf-analysis`** | 34    | Touchstone I/O, skrf wrappers, LTE / 5G NR / ISM / HaLow / GNSS bands, FCC / ETSI / 3GPP eval, coex matrix with GNSS ΔC/N₀ model, victim-weighted zero placement, EMC predictors |

Tools register under both flat names (back-compat) and categorised
aliases (`filter.*`, `power.*`, `analog.*`, `digital.*`, `vendor.*`,
`sim.*`). Full reference in
[`docs/tool-catalog.md`](docs/tool-catalog.md) +
[`docs/tools/`](docs/tools/) (one page per server).

---

## What it solves

| Workflow                 | Headline tools                                                   | Reference                          |
| ------------------------ | ---------------------------------------------------------------- | ---------------------------------- |
| LC ladder filter design  | `synthesize_lc_filter` → `place_transmission_zero` → `substitute_real_components` | Butterworth / Chebyshev / Elliptic |
| Active filter design     | `synthesize_sallen_key_lpf` / `_hpf` / `_bpf`, `synthesize_mfb_lpf` / `_bpf` | Sallen-Key, MFB                    |
| SMPS EMC pre-compliance  | `design_pi_filter`, `predict_conducted_emissions`, `design_snubber`, `design_cm_choke` | CISPR 22 / CISPR 32                |
| Microstrip + coupler     | `microstrip_synth`, `branchline_coupler`, `rat_race`, `lange_coupler` | Hammerstad-Jensen                  |
| Monte Carlo yield        | `monte_carlo_analysis` (joblib parallel)                         | Gaussian component tolerance       |
| Distributed filters      | `synthesize_stepped_impedance_lpf`, `synthesize_coupled_line_bpf`, `synthesize_hairpin_bpf`, `synthesize_interdigital_bpf`, `synthesize_combline_bpf` | Pozar §8.6-8.7, exact TEM N-line model |
| Multi-radio coexistence  | `place_zeros_for_coex` → `synthesize_for_coex_target` → `check_coex_matrix` (GNSS ΔC/N₀) | 3GPP TS 36.101, FCC restricted bands |

Five worked examples ship under [`examples/`](examples/):
`basic_lpf`, `buck_smps`, `emc_compliance`, `filter_compare`,
`opamp_filter`.

---

## Workflows

mcp-ltspice-qucs fits in the following [eng-mcp-suite](https://github.com/RFingAdam/eng-mcp-suite)
workflow bundles:

- **`rf-design`** — closed-form trans-line synthesis (lineforge) +
  wire-antenna MoM (mcp-nec2-antenna) + circuit/filter sim (this server).
- **`coexistence-review`** — multi-radio band picking + filter design
  against CISPR limits, fed into PCB layout review (mcp-pcb-emcopilot).

See the [suite manifest](https://github.com/RFingAdam/eng-mcp-suite/blob/main/manifest.yaml)
for the full list of sibling MCPs and bundle definitions.

---

## Scope and related MCP servers

This suite is **circuit-level + filter-synthesis** focused. Deliberately
stops at the antenna port and at the schematic-to-layout boundary. For:

- **Antenna design** → [`mcp-nec2-antenna`](https://github.com/RFingAdam/mcp-nec2-antenna)
  (wire / MoM) or [`mcp-openems`](https://github.com/RFingAdam/mcp-openems) (FDTD).
- **PCB-level EMC / SI / PI** → [`mcp-pcb-emcopilot`](https://github.com/RFingAdam/mcp-pcb-emcopilot).
- **Regulatory standards lookup** → [`mcp-emc-regulations`](https://github.com/RFingAdam/mcp-emc-regulations).
- **Physical-layer testing on real hardware** → a hardware-DUT MCP.

See [`docs/related-mcp-servers.md`](docs/related-mcp-servers.md) for the
full boundary statement, decision flow, and cross-MCP workflow examples.

---

## Documentation

- **[Getting started](docs/getting-started.md)** — install through first call.
- **[Tool catalog](docs/tool-catalog.md)** — all 110 tools, per-server pages under [`docs/tools/`](docs/tools/).
- **[Usage example](docs/usage.md)** — practical end-to-end walkthrough.
- **[Architecture](docs/architecture.md)** — interop contract between servers.
- **[Suite architecture](docs/suite-architecture.md)** — how this MCP fits in eng-mcp-suite.
- **[Changelog](CHANGELOG.md)**

---

## Part of eng-mcp-suite

<sub>This MCP server is part of</sub>

[![eng-mcp-suite](https://img.shields.io/badge/eng--mcp--suite-engineering%20MCP%20catalog-22D3EE?style=for-the-badge)](https://github.com/RFingAdam/eng-mcp-suite)

<sub>An open umbrella for engineering MCP servers across RF, EMC, PCB,
signal integrity, EM simulation, and lab test. Same brand, same docs
structure, designed to compose. See the
[full catalog](https://github.com/RFingAdam/eng-mcp-suite#whats-included)
or jump to a sibling:</sub>

| Domain                      | Sibling MCPs                                                                 |
| --------------------------- | ---------------------------------------------------------------------------- |
| **RF / Transmission lines** | [lineforge](https://github.com/RFingAdam/lineforge)                          |
| **Antennas**                | [mcp-nec2-antenna](https://github.com/RFingAdam/mcp-nec2-antenna)            |
| **PCB / SI**                | [mcp-pcb-emcopilot](https://github.com/RFingAdam/mcp-pcb-emcopilot)          |
| **EMC regulatory**          | [mcp-emc-regulations](https://github.com/RFingAdam/mcp-emc-regulations)      |
| **EM simulation (3D)**      | [mcp-openems](https://github.com/RFingAdam/mcp-openems)                      |
| **Diagrams**                | [drawio-engineering-mcp](https://github.com/RFingAdam/drawio-engineering-mcp) |
| **Lab gear**                | [copper-mountain-vna-mcp](https://github.com/RFingAdam/copper-mountain-vna-mcp) |

---

## Contributing

Contributions are welcome.

1. **Pick a [GitHub issue](https://github.com/RFingAdam/mcp-ltspice-qucs/issues)**.
2. **Fork + branch** (`feature/your-thing` or `fix/your-bug`).
3. **Run the local check suite**:
   ```bash
   uv sync --all-packages
   uv run pytest -q
   uv run ruff check . && uv run ruff format --check .
   ```
4. **Open a PR** — link the issue, request review.

Full contributor guide in [`CONTRIBUTING.md`](CONTRIBUTING.md).

---

## License & changelog

[AGPL-3.0-or-later](LICENSE); per-release changes in [`CHANGELOG.md`](CHANGELOG.md)
([Keep a Changelog](https://keepachangelog.com/) format). Relicensed
from Apache-2.0 in v0.4.0 to align with the eng-mcp-suite toolkit-wide
AGPL move. Underlying Qucs-S (GPL), LTspice (proprietary), and
scikit-rf (BSD) are runtime-invoked dependencies, not redistributed
by these wrappers.

## Acknowledgments

- **[LTspice](https://www.analog.com/en/resources/design-tools-and-calculators/ltspice-simulator.html)** — Analog Devices' SPICE simulator.
- **[Qucs-S](https://ra3xdh.github.io/)** — Quite Universal Circuit Simulator with SPICE-compatible kernels.
- **[scikit-rf](https://scikit-rf.readthedocs.io/)** — Touchstone and S-parameter library underneath the analysis layer.
- **The MCP working group** — for the [Model Context Protocol](https://modelcontextprotocol.io) specification.

<div align="center">

<sub>Part of <a href="https://github.com/RFingAdam/eng-mcp-suite">eng-mcp-suite</a> — built for RF engineers, EMC labs, and AI agents.</sub>

</div>
