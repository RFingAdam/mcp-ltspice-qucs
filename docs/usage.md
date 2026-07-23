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

### Registering your own vendor models

The curated catalogues cover a handful of representative RF series. For
third-party or measured parts — Würth, AVX, TDK, distributor exports, or your
own lab `.s2p` files — point the MCP at a directory:

```text
my_models/
├── wurth_L_3n3.s2p     # inductor, 3.3 nH  (name shorthand + measured)
├── wurth_L_4n7.s2p
└── avx_C_2p2.s2p       # capacitor, 2.2 pF
```

> *"Register `~/my_models` as a vendor namespace called `user_wurth`."*

The agent calls `register_user_vendor_dir`. Each file's kind (L/C), value and
self-resonant frequency are recovered from the measured reactance
(series-through fixture, `Z = 2·Z₀·(1−S21)/S21`) and cross-checked against the
filename. Afterwards `substitute_real_components(inductor_vendor="user_wurth",
...)` treats them like any curated series. Registering multiple labelled
directories (`user_wurth`, `user_lab_2024`) keeps them from colliding, and
re-registering refreshes the index.

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

## Step 5 — Confirm it against real SPICE

Everything up to here runs on the fast closed-form ladder — no simulator
touched. That is the right default for a 1000-trial Monte Carlo loop, but
before quoting a yield you should confirm the analytical preview actually
matches what SPICE says the circuit does.

> *"Run ngspice on the schematic and reconcile it against the analytical
> response."*

The agent calls `validate_against_spice` with the `.asc` and the component
dict. It runs a real ngspice AC sweep, extracts the S-parameters, computes
the analytical response on the same grid, and returns a **verdict**:

- `agree` — SPICE and analytical match within threshold (0.5 dB passband,
  3 dB stopband by default). Trust the yield number.
- `minor_disagreement` — a marginal or stopband-only miss.
- `disagree` — they diverge in the passband; the analytical margin is not
  reliable for this design, and the response carries a warning saying so.
- `spice_unavailable` — no simulator installed; you still get the
  analytical S2P back rather than an error.

This is the gate to run before reporting a yield or margin that came only
from the preview — especially once real-vendor `.include` models are wired
in, where the SPICE run is the only path that sees their true effect.

---

## What just happened

In four tool calls (~30 s of agent time), you went from "I need a 1 GHz
LPF" to a vendor-specific, spec-passing, yield-quoted design. No
schematic editor, no SPICE-deck hand-editing, no waiting for
GUI-driven AC sweeps.

- For more tools: [Tool catalog](tool-catalog.md)
- For how this fits in the suite: [Suite architecture](suite-architecture.md)
- For sibling MCPs that compose with this one: [eng-mcp-suite catalog](https://github.com/RFingAdam/eng-mcp-suite#whats-included)
