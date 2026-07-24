# mcp-ltspice-qucs

<p align="center">
  <img src="assets/logo.svg" alt="mcp-ltspice-qucs" width="480">
</p>

A three-server **Model Context Protocol (MCP)** suite that turns RF
filter design and multi-radio coexistence engineering into a fluent
agent workflow. Use **LTspice** and **Qucs-S** through domain-aware
abstractions ("place a transmission zero at 1853 MHz", "evaluate
against this coex spec") instead of SPICE primitives.

## Why this exists

Designing a single coexistence-aware filter today looks like: hours in
LTspice nudging component values, swapping vendor SPICE models by hand,
re-running the sim, eyeballing the S21 trace, repeat. This suite codifies
the workflow so an LLM agent can iterate at the **design intent** layer,
collapsing each iteration from minutes to seconds while keeping a
human engineer in the loop for judgment calls.

## The three servers

| Server | Purpose | Tools |
|---|---|---|
| [`mcp-ltspice`](tools/ltspice.md) | LTspice (Wine) + ngspice fallback, CircuitDocument import/export, durable jobs/artifacts, model-aware generic optimization, filter synthesis, S-parameter extraction, Monte Carlo | 76 |
| [`mcp-qucs-s`](tools/qucs-s.md) | Qucs schematic/netlist import/export, durable simulation jobs, native S-parameters/noise, Xyce harmonic balance, microstrip + distributed-element synthesis | 29 |
| [`mcp-rf-analysis`](tools/rf-analysis.md) | Simulator-agnostic skrf wrappers/readiness probing, LTE/5G NR/GNSS/ISM/HaLow band databases, FCC/ETSI/3GPP spec evaluation, multi-radio coex matrix | 35 |

All three speak **Touchstone** (`.s2p` / `.snp`) as the cross-tool
exchange format and return a uniform [response envelope](reference/envelope.md).

## Headline demo

The [basic LPF example](examples/basic-lpf.md) synthesizes a 5th-order
Butterworth low-pass filter at 1 GHz, snaps values to Coilcraft 0402HP +
Murata GJM C0G catalogs, and runs a 1000-trial analytical Monte Carlo at
5% component tolerance. **The analytical preview passes all 5 criteria
with 99% yield**; vendor parasitics are not applied in that calculation.

![Basic LPF response](assets/basic-lpf-response.png){ loading=lazy }

## Quickstart

```bash
git clone https://github.com/RFingAdam/mcp-ltspice-qucs
cd mcp-ltspice-qucs
uv sync --all-packages
uv run python examples/basic_lpf/design.py
```

See [Installation](installation.md) for ngspice / LTspice / Qucs-S
setup.

## License

AGPL-3.0-or-later. See [LICENSE](https://github.com/RFingAdam/mcp-ltspice-qucs/blob/main/LICENSE).
