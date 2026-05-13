# Usage

This page walks one realistic scenario from problem to result. For the
full tool reference, see [Tool catalog](tool-catalog.md).

---

## Scenario: 1 GHz Butterworth LPF for a coexistence cleanup

You have a sub-2 GHz front-end and you want a 5th-order Butterworth
LPF at fc = 1 GHz with ≥30 dB rejection at 2 GHz, built from real
0402-size vendor parts at a yield you can quote.

## Setup

```bash
git clone https://github.com/RFingAdam/mcp-ltspice-qucs.git
cd mcp-ltspice-qucs
uv sync --all-packages
```

Register the three servers with Claude Desktop / Code:

```json
{
  "mcpServers": {
    "ltspice": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp-ltspice-qucs/packages/mcp-ltspice", "mcp-ltspice"]
    },
    "rf-analysis": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp-ltspice-qucs/packages/mcp-rf-analysis", "mcp-rf-analysis"]
    }
  }
}
```

## Step 1 — Synthesize the prototype

Ask the assistant:

> *"Synthesize a 5th-order Butterworth LPF at fc = 1 GHz with 50 Ω source/load."*

The agent calls `synthesize_lc_filter`:

```json
{
  "topology": "lc_ladder",
  "response": "butterworth",
  "order": 5,
  "cutoff_hz": 1.0e9,
  "z0_ohm": 50.0,
  "type": "lpf"
}
```

It returns the ideal L/C values plus a Touchstone `.s2p` of the ideal
response.

## Step 2 — Substitute real parts

> *"Snap the inductors to Coilcraft 0402HP and the caps to Murata GJM C0G."*

The agent calls `substitute_real_components` with the synthesized
schematic; the tool replaces ideal `L`/`C` with vendor SPICE subcircuits
that carry self-resonance, ESR, and tolerance metadata.

## Step 3 — Evaluate against the spec

> *"Evaluate against a generic LPF spec: 0.5 dB passband IL, 14 dB return loss, ≥30/45/60 dB rejection at 2 / 3 / 5 × fc."*

The agent calls `evaluate_filter_spec`:

```json
{
  "criteria": [
    {"band": "passband", "metric": "il_max_db",   "target": 0.5},
    {"band": "passband", "metric": "rl_min_db",   "target": 14},
    {"band": "stop",     "f_hz": 2.0e9, "rej_min_db": 30},
    {"band": "stop",     "f_hz": 3.0e9, "rej_min_db": 45},
    {"band": "stop",     "f_hz": 5.0e9, "rej_min_db": 60}
  ]
}
```

A trimmed response (the bundled `examples/basic_lpf` design):

| Criterion       | Target  | Measured | Margin   |
| --------------- | ------- | -------- | -------- |
| Passband IL     | ≤ 0.5 dB | 0.02 dB  | +0.48 dB |
| Passband RL     | ≥ 14 dB  | 24.57 dB | +10.57 dB |
| 2 × fc          | ≥ 30 dB  | 30.85 dB | +0.85 dB |
| 3 × fc          | ≥ 45 dB  | 48.16 dB | +3.16 dB |
| 5 × fc          | ≥ 60 dB  | 70.16 dB | +10.16 dB |

## Step 4 — Monte Carlo yield

> *"Run 1000 trials at 5% component tolerance and tell me the yield."*

The agent calls `monte_carlo_analysis` with `n=1000, sigma=0.05`. It
returns **99% yield** — the design has enough margin to survive 5%
component variation.

---

## What just happened

In four tool calls (~30 s of agent time), you went from "I need a 1 GHz
LPF" to a vendor-specific, spec-passing, yield-quoted design. No
schematic editor, no SPICE-deck hand-editing, no waiting for
GUI-driven AC sweeps.

- For more tools: [Tool catalog](tool-catalog.md)
- For how this fits in the suite: [Suite architecture](suite-architecture.md)
- For sibling MCPs that compose with this one: [eng-mcp-suite catalog](https://github.com/RFingAdam/eng-mcp-suite#whats-included)
