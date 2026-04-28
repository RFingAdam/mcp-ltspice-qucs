# Related MCP servers — scope and boundaries

`mcp-ltspice-qucs` is part of a larger ecosystem of engineering-domain
MCP servers. This document tells an LLM agent (or a human reader)
**which server to reach for** when a question spans more than one
domain — and where this suite explicitly does **not** duplicate work.

## What this MCP suite covers

- **Filter synthesis** — LC ladders (Butterworth / Chebyshev / elliptic),
  active filters (Sallen-Key / MFB), transmission-zero placement, real-
  vendor part substitution, optimisation, Monte Carlo yield.
- **Distributed-element synthesis** (closed-form) — microstrip lines,
  branch-line / rat-race / Lange / coupled-line couplers,
  Richards-Kuroda lumped-to-distributed conversion.
- **SPICE simulation** — LTspice (Wine) or ngspice; `.raw` parser;
  S-parameter extraction.
- **SMPS sizing** — buck / boost / LDO; type-II compensator; Bode +
  phase-margin analysis.
- **SMPS EMC pre-compliance** (circuit-level) — Pi LC output filter,
  DM input filter with Middlebrook check, conducted-emissions
  prediction with CISPR 22/32 limit overlay, RC snubber, common-mode
  choke selection.
- **Coexistence** — multi-radio harmonic-victim lookup, desense matrix.
- **Component catalogues** — op-amp / MOSFET / BJT / diode / voltage
  reference datasheet metadata.
- **Digital / mixed-signal helpers** — setup/hold timing, propagation
  delay, digital-to-analog crosstalk, supply-noise injection.

## What this MCP suite explicitly does not cover

| Domain | Use this MCP instead | Why we don't duplicate |
|---|---|---|
| **Antenna design / radiation patterns** | [`nec2-antenna`] (closed-form + NEC2 method-of-moments — dipole, vertical, loop, Yagi, inverted-V) and [`openems`] (full-wave FDTD — patch, monopole, helix, horn, microstrip antenna) | Both external MCPs cover the antenna design space far more completely than any closed-form add-on we could build. We deliberately stop at the antenna port. |
| **PCB-level EMC / SI / PI** (impedance from layout, decoupling, return paths, crosstalk from geometry, stackup, copper pours, vias) | [`pcb-emcopilot`] (90+ tools — `pcb_analyze_*`, `pcb_calc_*`, `pcb_predict_*`) | We're circuit-level (schematic in, S-params / yield out). pcb-emcopilot is board-level (layout in, EMC margin out). They sit on either side of "I have a circuit, now lay it out". |
| **Regulatory standards lookup** (CISPR / FCC Part 15 / Part 18 / Part 95 / ETSI / 3GPP / IEC 61000 / ISO 11452 / CISPR 25) | [`emc-regulations`] | Authoritative regulatory tables and cross-reference. Our band-list / CISPR-limit tools are filter-design helpers (subsets used internally by `lookup_harmonic_victims` etc.) — not the canonical reference. |
| **RF testing of physical hardware** (BLE / WiFi / HaLow on a real DUT) | [`rf-test`] | We're pre-silicon / pre-board simulation. rf-test drives an actual device. |
| **Physical-layer SDR captures** | [`sdr-node`] | Same — we're at design-time, sdr-node is at runtime. |
| **3D CAD / mechanical** | [`blender`] | Out of scope. |
| **Drawio engineering diagrams** | [`drawio-engineering`] | Out of scope. |

## Specific tool overlaps and recommendations

These tools in `mcp-ltspice-qucs` overlap functionally with other MCPs.
We keep them because they integrate tightly with our filter / SMPS
workflows, but if you only need the standalone capability prefer the
canonical source.

### Microstrip impedance (analysis direction)

| This MCP | Canonical alternative | When to use which |
|---|---|---|
| `mcp-qucs-s.synthesize_microstrip_line` (Z₀ → W, L) | — (synthesis is unique) | This MCP — synthesis is unique |
| `mcp-qucs-s.analyze_microstrip_tool` (W → Z₀) | `pcb-emcopilot.pcb_calc_microstrip_impedance` | **Prefer pcb-emcopilot** for analysis — it integrates with the wider PCB workflow (CPW, stripline, differential, eye-diagram). Use ours only if you're already mid-Richards-Kuroda flow. |

### Frequency-band data (regulatory tables)

| This MCP (filter-design helper subset) | Canonical regulatory source |
|---|---|
| `list_lte_bands_tool` | `emc-regulations.lte_bands_list` |
| `list_5gnr_bands_tool` | `emc-regulations.nr_bands_list` |
| `list_ism_bands_tool` | `emc-regulations.ism_bands_list` |
| `list_gnss_bands_tool` | (broader regulatory in emc-regulations) |
| `list_fcc_restricted_bands_tool` | `emc-regulations.fcc_restricted_bands_list` |

For day-to-day filter design, the in-suite versions are convenient
because they feed directly into `lookup_harmonic_victims` and
`check_coex_matrix`. For authoritative regulatory queries (medical,
automotive, certification matrix, market requirements), use
`emc-regulations`.

The reverse-lookup tool `lookup_band_by_freq_tool` (returns *all* band
categories matching a single frequency) is unique to this MCP.

### HaLow channel grids

`list_halow_channels_tool` is **unique to this MCP** — no external
server carries the IEEE 802.11ah regional channel sets (EU 863–870,
US/AU/NZ 902–928, JP 916.5–927.5, KR 917.5–923.5, CN 755–787, etc.).

### Conducted-emissions prediction

| This MCP | Canonical alternative | When to use which |
|---|---|---|
| `mcp-rf-analysis.predict_conducted_emissions` | `pcb-emcopilot.pcb_analyze_conducted_emissions` | **This MCP**: post-circuit (you have an SMPS schematic; predict emissions from line-current spectrum + filter S-params). **pcb-emcopilot**: pre-circuit / topology-level (you have an SMPS topology spec; predict emissions from switching parameters). |
| `mcp-ltspice.predict_conducted_emissions` (in `power.emc`) | `pcb-emcopilot.pcb_analyze_conducted_emissions` | **This MCP**: trapezoidal-waveform harmonic decomposition with CISPR 22/32 limit overlay; couples directly into `design_pi_output_filter` / `design_dm_input_filter` results. **pcb-emcopilot**: layout-aware analysis once the PCB exists. |

The in-suite tools couple cleanly to filter design (you predict
emissions, then size the filter, then re-predict). pcb-emcopilot
takes over once the layout exists.

### CISPR / FCC limit lookup (no overlap with bigger picture)

`mcp-rf-analysis.cispr_limit_at` and `fcc_part15_radiated_limit_at`
return narrow Class A/B values used inline by the filter / EMC
prediction tools. For the broader regulatory matrix
(certification timelines, market-by-market test requirements,
medical / automotive / IEC 61000 immunity levels), use
`emc-regulations`.

## Decision flow for an LLM agent

```
Are you designing an antenna?
  → use nec2-antenna (closed-form + NEC2) or openems (FDTD)

Are you analysing an existing PCB layout?
  → use pcb-emcopilot

Are you looking up a regulatory limit / certification requirement?
  → use emc-regulations

Are you designing a filter, SMPS, EMI filter, or coupler from spec?
Are you predicting conducted emissions from a switching converter?
Are you computing harmonic landings into LTE / GNSS bands?
Are you running an LTspice / ngspice / Qucs-S simulation?
  → use this MCP suite (mcp-ltspice / mcp-rf-analysis / mcp-qucs-s)

Are you testing a physical DUT?
  → use rf-test, sdr-node
```

## Cross-MCP workflow examples

### Designing a DC/DC power rail to pass FCC Part 15 conducted emissions

1. `mcp-ltspice.design_buck` → first-cut L, Cout
2. `mcp-ltspice.design_pi_output_filter` → ripple suppression
3. `mcp-ltspice.design_dm_input_filter` → input EMI filter (Middlebrook stable)
4. `mcp-ltspice.design_cm_choke` → CM choke selection
5. `mcp-ltspice.predict_conducted_emissions` → CISPR margin per harmonic
6. `pcb-emcopilot.pcb_analyze_conducted_emissions` → board-level cross-check after layout
7. `emc-regulations.fcc_part15_limit` → confirm exact regulatory limit
8. `emc-regulations.test_plan_generator` → certification test plan

### Designing a 2.4 GHz front-end with antenna + matching network + filter

1. `nec2-antenna.nec2_create_dipole` (or `openems.openems_create_patch`) → antenna design + radiation pattern
2. `mcp-qucs-s.synthesize_coupler` → branch-line splitter if needed
3. `mcp-ltspice.synthesize_lc_filter` (Chebyshev BPF) → harmonic / image filter
4. `mcp-rf-analysis.cascade_networks` → composite S-parameters
5. `mcp-rf-analysis.check_passband_compliance` → passband / RL margins
6. `mcp-rf-analysis.lookup_harmonic_victims` → coex check vs LTE / GNSS
7. `pcb-emcopilot.pcb_calc_microstrip_impedance` → trace impedance for the matching network
8. `pcb-emcopilot.pcb_analyze_return_paths` → return-current paths, ground stitching

[`nec2-antenna`]: https://github.com/RFingAdam/nec2-antenna
[`openems`]: https://github.com/RFingAdam/openems-mcp
[`pcb-emcopilot`]: https://github.com/RFingAdam/pcb-emcopilot
[`emc-regulations`]: https://github.com/RFingAdam/emc-regulations
[`rf-test`]: https://github.com/RFingAdam/rf-test
[`sdr-node`]: https://github.com/RFingAdam/sdr-node
[`drawio-engineering`]: https://github.com/RFingAdam/drawio-engineering
[`blender`]: https://github.com/RFingAdam/blender-mcp
