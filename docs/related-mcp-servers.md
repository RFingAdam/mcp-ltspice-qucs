# Related MCP servers — scope and boundaries

`mcp-ltspice-qucs` is one piece of a larger ecosystem of
engineering-domain MCP servers. This document tells an LLM agent (or a
human reader) **which tool to reach for** when a question spans more
than one domain — and where this suite explicitly does **not**
duplicate work that another MCP already covers.

Some of the sister MCPs referenced below are public (linked); others
are described by capability because the implementations are not yet
public. The *scope guidance* applies regardless: don't reach for this
suite to solve a problem that's outside its layer.

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

| Domain | Reach for | Why we don't duplicate |
|---|---|---|
| **Antenna design / radiation patterns** | A NEC2 method-of-moments antenna MCP (closed-form + dipole, vertical, loop, Yagi, inverted-V) and/or an FDTD full-wave EM MCP (patch, monopole, helix, horn, microstrip antenna) | Antenna synthesis and radiation simulation are their own domain. We deliberately stop at the antenna port. |
| **PCB-level EMC / SI / PI** (impedance from layout, decoupling, return paths, crosstalk from geometry, stackup, copper pours, vias) | A PCB-layout-aware EMC MCP | We're circuit-level (schematic in, S-params / yield out). Layout-aware EMC is board-level (layout in, EMC margin out). Two sides of "I have a circuit, now lay it out". |
| **Regulatory standards lookup** (CISPR / FCC Part 15 / Part 18 / Part 95 / ETSI / 3GPP / IEC 61000 / ISO 11452 / CISPR 25) | [`mcp-emc-regulations`] | Authoritative regulatory tables and cross-reference. Our band-list / CISPR-limit tools are filter-design helpers (subsets used internally by `lookup_harmonic_victims` etc.) — not the canonical reference. |
| **RF testing of physical hardware** (BLE / WiFi / HaLow on a real DUT) | A hardware-DUT RF test MCP | We're pre-silicon / pre-board simulation. Hardware testing drives an actual device. |
| **Physical-layer SDR captures** | An SDR capture MCP | Same — we're at design-time, SDR capture is at runtime. |
| **3D CAD / mechanical** | [`mcp-blender`] | Out of scope. |
| **Drawio engineering diagrams** | [`drawio-engineering-mcp`] | Out of scope. |

## Specific tool overlaps and recommendations

These tools in `mcp-ltspice-qucs` overlap functionally with capabilities
that exist elsewhere. We keep them because they integrate tightly with
our filter / SMPS workflows, but if you only need the standalone
capability, prefer the canonical source.

### Microstrip impedance (analysis direction)

| This MCP | Canonical alternative | When to use which |
|---|---|---|
| `mcp-qucs-s.synthesize_microstrip_line` (Z₀ → W, L) | — (synthesis is unique) | This MCP — synthesis is unique |
| `mcp-qucs-s.analyze_microstrip_tool` (W → Z₀) | a PCB-layout-aware EMC MCP | **Prefer the layout-aware tool** for analysis if one is available — it integrates with the wider PCB workflow (CPW, stripline, differential, eye-diagram). Use ours when you're already mid-Richards-Kuroda flow. |

### Frequency-band data (regulatory tables)

| This MCP (filter-design helper subset) | Canonical regulatory source |
|---|---|
| `list_lte_bands_tool` | `mcp-emc-regulations.lte_bands_list` |
| `list_5gnr_bands_tool` | `mcp-emc-regulations.nr_bands_list` |
| `list_ism_bands_tool` | `mcp-emc-regulations.ism_bands_list` |
| `list_gnss_bands_tool` | (broader regulatory in `mcp-emc-regulations`) |
| `list_fcc_restricted_bands_tool` | `mcp-emc-regulations.fcc_restricted_bands_list` |

For day-to-day filter design, the in-suite versions are convenient
because they feed directly into `lookup_harmonic_victims` and
`check_coex_matrix`. For authoritative regulatory queries (medical,
automotive, certification matrix, market requirements), use
[`mcp-emc-regulations`].

The reverse-lookup tool `lookup_band_by_freq_tool` (returns *all* band
categories matching a single frequency) is unique to this MCP.

### HaLow channel grids

`list_halow_channels_tool` is **unique to this MCP** — no external
server (that we're aware of) carries the IEEE 802.11ah regional
channel sets (EU 863–870, US/AU/NZ 902–928, JP 916.5–927.5, KR
917.5–923.5, CN 755–787, etc.).

### Conducted-emissions prediction

| This MCP | Canonical alternative | When to use which |
|---|---|---|
| `mcp-rf-analysis.predict_conducted_emissions` | a PCB-layout-aware EMC MCP | **This MCP**: post-circuit (you have an SMPS schematic; predict emissions from line-current spectrum + filter S-params). **Layout-aware tool**: pre-circuit / topology-level (you have an SMPS topology spec; predict emissions from switching parameters). |
| `mcp-ltspice.predict_conducted_emissions` (in `power.emc`) | a PCB-layout-aware EMC MCP | **This MCP**: trapezoidal-waveform harmonic decomposition with CISPR 22/32 limit overlay; couples directly into `design_pi_output_filter` / `design_dm_input_filter` results. **Layout-aware tool**: layout-aware analysis once the PCB exists. |

The in-suite tools couple cleanly to filter design (you predict
emissions, then size the filter, then re-predict). A layout-aware
tool takes over once the layout exists.

### CISPR / FCC limit lookup (no overlap with bigger picture)

`mcp-rf-analysis.cispr_limit_at` and `fcc_part15_radiated_limit_at`
return narrow Class A/B values used inline by the filter / EMC
prediction tools. For the broader regulatory matrix
(certification timelines, market-by-market test requirements,
medical / automotive / IEC 61000 immunity levels), use
[`mcp-emc-regulations`].

## Decision flow for an LLM agent

```
Are you designing an antenna?
  → use a NEC2-based or FDTD antenna MCP

Are you analysing an existing PCB layout?
  → use a PCB-layout-aware EMC MCP

Are you looking up a regulatory limit / certification requirement?
  → use mcp-emc-regulations

Are you designing a filter, SMPS, EMI filter, or coupler from spec?
Are you predicting conducted emissions from a switching converter?
Are you computing harmonic landings into LTE / GNSS bands?
Are you running an LTspice / ngspice / Qucs-S simulation?
  → use this MCP suite (mcp-ltspice / mcp-rf-analysis / mcp-qucs-s)

Are you testing a physical DUT or capturing IQ samples?
  → use a hardware-DUT RF test MCP / an SDR capture MCP
```

## Cross-MCP workflow examples

### Designing a DC/DC power rail to pass FCC Part 15 conducted emissions

1. `mcp-ltspice.design_buck` → first-cut L, Cout
2. `mcp-ltspice.design_pi_output_filter` → ripple suppression
3. `mcp-ltspice.design_dm_input_filter` → input EMI filter (Middlebrook stable)
4. `mcp-ltspice.design_cm_choke` → CM choke selection
5. `mcp-ltspice.predict_conducted_emissions` → CISPR margin per harmonic
6. *(optional)* a PCB-layout-aware EMC tool → board-level cross-check after layout
7. `mcp-emc-regulations.fcc_part15_limit` → confirm exact regulatory limit
8. `mcp-emc-regulations.test_plan_generator` → certification test plan

### Designing a 2.4 GHz front-end with antenna + matching network + filter

1. *(antenna MCP — NEC2 closed-form or FDTD full-wave)* → antenna design + radiation pattern
2. `mcp-qucs-s.synthesize_coupler` → branch-line splitter if needed
3. `mcp-ltspice.synthesize_lc_filter` (Chebyshev BPF) → harmonic / image filter
4. `mcp-rf-analysis.cascade_networks` → composite S-parameters
5. `mcp-rf-analysis.check_passband_compliance` → passband / RL margins
6. `mcp-rf-analysis.lookup_harmonic_victims` → coex check vs LTE / GNSS
7. *(PCB-layout-aware tool)* → trace impedance for the matching network, return-current paths, ground stitching

[`mcp-emc-regulations`]: https://github.com/RFingAdam/mcp-emc-regulations
[`drawio-engineering-mcp`]: https://github.com/RFingAdam/drawio-engineering-mcp
[`mcp-blender`]: https://github.com/RFingAdam/mcp-blender
