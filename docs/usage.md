# Usage

This page walks one realistic scenario from problem to result. For the
full tool reference, see [Tool catalog](tool-catalog.md).

---

## Scenario: 1 GHz Butterworth LPF for a coexistence cleanup

You have a sub-2 GHz front-end and you want a 5th-order Butterworth
LPF at fc = 1 GHz with ≥30 dB rejection at 2 GHz. This walkthrough
produces an analytical, catalog-snapped candidate; a vendor-realized
design still requires model attachment and simulator verification.

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
  "filter_type": "butterworth",
  "order": 5,
  "cutoff_hz": 1.0e9,
  "output_asc": "/absolute/path/to/basic_lpf.asc",
  "output_s2p": "/absolute/path/to/basic_lpf.s2p",
  "z0": 50.0,
  "topology": "series_first"
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

The agent calls `register_user_vendor_dir`. For `.s2p` files, kind (L/C),
nominal value, and self-resonant frequency are reduced from the measured
series-through response (`Z = 2·Z₀·(1−S21)/S21`) and cross-checked against
the filename. A `.lib` must contain exactly one unambiguous two-pin
subcircuit; its name, pin map, source checksum, and source path are retained
so the workbench can include and instantiate it verbatim. Afterwards
`substitute_real_components` can use reduced lumped estimates, while
`search_component_models` plus `circuit_attach_models` carries the exact
model into `CircuitDocument`. Re-registering refreshes the index.

## Step 2 — Substitute real parts

> *"Snap the inductors to Coilcraft 0402HP and the caps to Murata GJM C0G."*

The agent calls `substitute_real_components` with the synthesized
`components` dictionary:

```json
{
  "components": {
    "L1": 4.918e-9,
    "C2": 5.150e-12,
    "L3": 1.592e-8,
    "C4": 5.150e-12,
    "L5": 4.918e-9
  },
  "inductor_vendor": "coilcraft_0402hp",
  "capacitor_vendor": "murata_gjm_c0g",
  "max_spec_freq_hz": 5.0e9,
  "srf_margin": 1.2
}
```

The result contains the closest catalog values and first-order ESR/ESL/SRF
metadata. It does not modify the `.asc`, insert a `.lib`, or cause later
analytical tools to apply those parasitics automatically.

For a model-backed workflow, import or construct a `CircuitDocument`, call
`search_component_models`, attach the chosen records with
`circuit_attach_models`, and submit `circuit_optimize_submit`. See
[General circuit workbench](circuit-workbench.md) for the supported file
subsets and validation contract.

## Step 3 — Evaluate against the spec

> *"Evaluate against a generic LPF spec: 0.5 dB passband IL, 14 dB return loss, ≥30/45/60 dB rejection at 2 / 3 / 5 × fc."*

The agent calls `evaluate_filter_spec` (also available as the deprecated
`filter.evaluate_spec`) on the analytical `.s2p` from Step 1:

```json
{
  "s2p_path": "/absolute/path/to/basic_lpf.s2p",
  "spec": {
    "passband": {
      "f_start": 1.0e6,
      "f_stop": 6.0e8,
      "il_max_db": 0.5,
      "rl_min_db": 14.0
    },
    "stopband_targets": [
      {"freq": 2.0e9, "rejection_min_db": 30.0, "label": "2 x fc"},
      {"freq": 3.0e9, "rejection_min_db": 45.0, "label": "3 x fc"},
      {"freq": 5.0e9, "rejection_min_db": 60.0, "label": "5 x fc"}
    ]
  }
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

The agent calls `monte_carlo_analysis` with the catalog-snapped component
values, `n_runs=1000`, `tolerance_pct=5.0`, and
`transmission_zeros=false`. The bundled example returns **99% analytical
yield**. This varies ideal L/C values; it does not include the catalog
parasitics or vendor model behavior returned in Step 2.

## Step 5 — Confirm it against real SPICE

Everything up to here runs on the fast closed-form ladder — no simulator
touched. That is the right default for a 1000-trial Monte Carlo loop, but
before quoting a yield you should confirm the analytical preview actually
matches what SPICE says the circuit does.

> *"Run ngspice on the schematic and reconcile it against the analytical
> response."*

The agent calls `validate_against_spice` with the generated `.asc` and the
same ideal component dictionary used to generate it. It runs a real
LTspice/ngspice AC sweep, extracts a reciprocal/symmetric two-port
approximation, computes the analytical response on the same grid, and
returns a **verdict**:

- `agree` — SPICE and analytical match within threshold (0.5 dB passband,
  3 dB stopband by default). The preview tracks this generated schematic.
- `minor_disagreement` — a marginal or stopband-only miss.
- `disagree` — they diverge in the passband; the analytical margin is not
  reliable for this design, and the response carries a warning saying so.
- `spice_unavailable` — no simulator installed; you still get the
  analytical S2P back rather than an error.

This checks the generated ladder and extraction path. It does not validate
the catalog-snapped values from Step 2 unless you regenerate the schematic
with those values, and it does not see vendor `.include` models unless you
attach them to the simulated circuit yourself.

---

## What just happened

You went from "I need a 1 GHz LPF" to a catalog-snapped candidate with an
analytical spec check and tolerance preview. The handoff is intentionally
clear: attach the actual vendor models, regenerate or edit the schematic,
and run the simulator before releasing the design.

- For more tools: [Tool catalog](tool-catalog.md)
- For how this fits in the suite: [Suite architecture](suite-architecture.md)
- For sibling MCPs that compose with this one: [eng-mcp-suite catalog](https://github.com/RFingAdam/eng-mcp-suite#whats-included)
