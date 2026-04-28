# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Each package under `packages/` versions independently; entries below are
grouped by package.

## [Unreleased]

### Filter synthesis — HPF / BPF / BSF (`mcp-ltspice`)

Closes the biggest user-visible gap in filter coverage. Three new top-level synthesis functions backed by classical Pozar §8.5 frequency transformations from the LPF prototype:

- **`synthesize_lc_hpf`** (`filter.synthesize_lc_hpf`) — high-pass via series-L → series-C and shunt-C → shunt-L. Component count equals the LPF prototype's. -3 dB at the specified cutoff (Butterworth) or equiripple edge (Chebyshev).
- **`synthesize_lc_bpf`** (`filter.synthesize_lc_bpf`) — band-pass via series-L → series-LC tank, shunt-C → shunt-LC tank. Component count doubles. f₀ = √(f_low · f_high), Δ = (f_high − f_low) / f₀; each LC pair resonates at f₀ exactly.
- **`synthesize_lc_bsf`** (`filter.synthesize_lc_bsf`) — band-stop via series-L → series parallel-LC (anti-resonant), shunt-C → shunt series-LC (resonant). Used to notch a specific band (LO leakage, image rejection).

Currently supports Butterworth and Chebyshev I across all three. Elliptic HPF/BPF/BSF needs a separate transformation for finite transmission zeros and is on the roadmap.

**Analytical S-parameter analysis is wired up for all four kinds** (LPF, HPF, BPF, BSF). `components_dict_to_elements` extended with a `kind` parameter (`"lowpass"`, `"highpass"`, `"bandpass"`, `"bandstop"`). Three new ABCD element types added to support BPF/BSF resonator topologies:

- `series_lc_series` — series-LC in main path (BPF series section). `Z = sL + 1/(sC)` — dips at f₀.
- `shunt_lc_parallel` — parallel-LC to ground (BPF shunt section). `Y = sC + 1/(sL)` — dips at f₀.
- `series_lc_parallel` — parallel-LC in main path (BSF series section). `Z = sL/(s²LC+1)` — peaks at f₀.

The existing `shunt_lc_trap` kind (series-LC to ground; elliptic LPF trap) doubles as the BSF shunt section. Verified BPF response: -3 dB at band edges, deep stopband > 50 dB one decade out. Verified BSF response: > 60 dB notch at f₀, lossless passband one decade out.

### Substrate preset library + microstrip loss (`mcp-qucs-s`)

- New `mcp_qucs_s.substrates` module with **16 curated presets** covering FR-4 (4 thicknesses), Rogers RO4350B (3), Rogers RO4003C (2), RT/Duroid 5880 (2) + 6002, PTFE, Isola FR408HR (2), Taconic TLY5. Each carries documented εr / h_mm / t_um / tan_d from the manufacturer datasheet.
- New `list_substrate_presets_tool` MCP tool returns the full catalogue with descriptions.
- `synthesize_microstrip_line` and `analyze_microstrip_tool` now accept a **preset name string** (e.g. `"Rogers4350B_0508"`) as the `substrate` argument in addition to the parameter dict. Saves engineers from re-typing `{er, h_mm, t_um, tan_d}` every call.
- `analyze_microstrip` now uses the substrate's `tan_d` (previously accepted but ignored) to compute **conductor + dielectric attenuation in dB/mm**, surfaced as `alpha_d_db_per_mm`, `alpha_c_db_per_mm`, `alpha_total_db_per_mm`. Pozar §3.8.1 dielectric formula plus skin-effect conductor loss (default σ = 5.8 × 10⁷ S/m for copper; override for gold / aluminium / measured plating). Verified against published values for FR-4 / Rogers / Duroid at 5 GHz.

### Monte Carlo trace mode (`mcp-ltspice`)

`monte_carlo_analysis` gains a `trace=True` flag that emits a JSONL file (one record per trial: seed, sampled components, metrics, pass/fail status, failures list). Lets engineers do offline sensitivity / root-cause analysis of yield loss without re-running MC. Default path is `mc_trace_<base_seed>.jsonl` in cwd; override via `trace_path`.

`monte_carlo_analysis(transmission_zeros=...)` default changed from `True` to `None` — now auto-infers topology from the components dict (matches the `components_dict_to_elements` behaviour added earlier in this Unreleased stream). Old code passing `True`/`False` continues to work unchanged.

### Tests

30 new tests across `test_hpf_bpf_bsf_synthesis.py`, `test_substrate_presets.py`, `test_mc_trace.py`. Total pass count 421 (+30 vs. prior 391 baseline), 0 regressions.

### `mcp-ltspice` — power-supply EMC pre-compliance toolkit

Five new tools fill the gap between SMPS sizing (existing buck / boost / LDO) and a real product passing conducted-emissions:

- **`design_pi_output_filter`** (`power.design_pi_output_filter`) — Pi-section LC output filter (C-L-C, 3rd-order LPF) sized for an attenuation target at a given frequency. Returns L, C_in, C_out, resonant frequency, achieved attenuation at f_target / f_sw, plus a damping-resistor recipe and BOM notes (inductor saturation, MLCC DC-bias derating).
- **`design_dm_input_filter`** (`power.design_dm_input_filter`) — 2nd-order differential-mode LC input filter sized for a conducted-emissions target. Includes the Middlebrook stability check (|Z_out_filter| < |Z_in_converter| / safety_factor) so the filter doesn't destabilise the converter's loop, and surfaces a damping-branch recipe (R_d, C_d) per Erickson & Maksimović §10.4.
- **`predict_conducted_emissions`** (`power.predict_conducted_emissions`) — harmonic decomposition of a trapezoidal switching waveform with two-sinc envelope (duty-cycle and edge-rate cutoffs), LISN-loaded prediction, CISPR 22 / 32 Class A or B limit overlay (QP or AVG detector). Returns per-harmonic frequency / emission / limit / margin arrays plus a worst-margin pass/fail summary.
- **`design_rc_snubber`** (`power.design_rc_snubber`) — RC snubber for switch-node ringing. Inputs: parasitic loop inductance, switch C_oss, peak voltage, switching frequency. Returns R, C, ring frequency, achieved damping factor, and per-cycle dissipation. Standard recipe: C_snub = C_oss, R_snub = √(L_par/C_oss) × 2ζ.
- **`design_cm_choke`** (`power.design_cm_choke`) — common-mode choke selection from a curated catalogue (Würth WE-CMB, TDK ZJYS / ACT, Murata DLW). Filters by DC current rating, target CM impedance at design frequency, and DM-leakage cap. Returns ranked candidate list plus the highest-margin pick.

All five register under both their flat names and `power.*` namespaced aliases. Tests in `test_power_emc.py` (31 cases) cover return-shape, math correctness (resonance / attenuation / dissipation scaling), CISPR class / detector relationships, and edge cases.

### `mcp-qucs-s` — implementation status corrections

Earlier `[Unreleased]` notes claimed "Implemented all 4 simulator-driven tools with graceful degradation." This is corrected to the actual status:

- **Implemented (4 closed-form synthesis tools, no Qucs-S install required):**
  - `synthesize_microstrip_line` — Hammerstad-Jensen W/L from Z₀
  - `analyze_microstrip_tool` — Z₀ / ε_eff / wavelength from W
  - `synthesize_coupler` — branch-line / rat-race / coupled-line / Lange (single-line approximation; coupled-line gap synthesis pending)
  - `lumped_to_distributed` — Richards transformation + Kuroda identities
- **Implemented (2 simulator-driven tools, require Qucs-S):**
  - `run_sp_analysis` — Qucs-S `.dat` parser + Touchstone exporter
  - `export_touchstone` — Run + export `.s2p` in one call
- **Scaffolded — return `error("not yet implemented")` envelope (Tier-6 roadmap):**
  - `run_harmonic_balance` — detects Xyce, but Xyce-netlist generation and harmonic-content parsing (IM3 / IIP3 / 1 dB compression) are pending
  - `extract_noise_parameters` — detects Qucs-S, but noise-analysis dataset parser (Fmin / Γopt / Rn / NF50) is pending

Both scaffolded tools previously returned `ok()` envelopes with a `"note: scaffolded"` field — calling agents could mistake this for success. They now return `error()` envelopes with clear "not yet implemented" messages.

- The README's tool table previously listed `synthesize_microstrip_filter` (stepped-impedance / hairpin / interdigital filter realisations) — the tool was never implemented. The row has been removed; filter synthesis in the distributed domain is on the Tier-6 roadmap.

### Architecture hygiene (`mcp-ltspice`)

- Vendor catalogue: `JOHANSON_L` and `TDK_MLG` were `COILCRAFT_0402HP.copy()` — alias-only, not real data. Replaced with measured tables for the Johanson L-07W series (0402) and TDK MLK1005S series.
- `compare_filter_orders` now accepts `srf_margin` and `max_value_drift_pct` and passes them through to the inner `substitute_real_components` call. Previous behaviour ran the comparison with legacy semantics, so winners could differ from a final design that enabled SRF gating.
- Tool namespacing: every flat tool name now has a namespaced alias (`filter.*` / `analog.*` / `power.*` / `digital.*` / `vendor.*` / `sim.*` / `coex.*`). Old names continue to work but emit a one-shot `DeprecationWarning` per process pointing to the namespaced equivalent. Removal in a later major release.

### Correctness fixes (`mcp-ltspice`)

- `components_dict_to_elements(components, transmission_zeros=...)` — the `transmission_zeros` flag now defaults to `None` (auto-infer from the components dict) instead of `False`. Prior default silently produced wrong S-parameters when an elliptic ladder was passed without the flag set explicitly. Explicit `False` on elliptic-shape components now emits a `RuntimeWarning`.
- `place_transmission_zero` and `trap_lc_for_freq` — the `preserve_ratio: bool` parameter is replaced by `mode: Literal["preserve_ratio", "hold_l", "hold_c"]`. The old API with `preserve_ratio=False` and both `l_existing` and `c_existing` provided fell through none of the conditional branches and silently substituted `L=1 nH` — that bug is fixed. Legacy `preserve_ratio` calls continue to work via a deprecation shim.
- Elliptic synthesis (`synthesize_lc_lpf` filter_type="elliptic") — the reported `transmission_zeros_hz` field was inconsistent with the actual `1/(2π√(L_k C_k))` of the synthesised trap pairs at certain orders. Root cause: `_fit_lc_to_prototype` was an unconstrained least-squares fit over (L, C) pairs that drifted the L·C product away from the target trap resonance. Fix: each trap now has only `L_trap` as an optimisation variable; `C_trap` is computed as `1/(ω_zk² · L_trap)` so the resonance is pinned exactly to the prototype's transmission zero. New regression test (`test_elliptic_synth_consistency.py`) covers orders 3 / 5 / 7 / 9 across multiple `(fc, ripple, stopband)` combinations and asserts the reported and achieved TZ frequencies agree to within 1 %.

### Vendor catalogue (`mcp-ltspice`) — Johanson L-07W and TDK MLK1005S replacements

The previous `JOHANSON_L = COILCRAFT_0402HP.copy()` and `TDK_MLG = COILCRAFT_0402HP.copy()` aliases were misleading — they suggested broader vendor coverage than the package actually had. Replaced with nominal datasheet-derived tables for:

- **Johanson L-07W series (0402)** — 19 values from 1.0 nH to 39 nH, with SRFs ~10–15 % higher than Coilcraft 0402HP at equal inductance (wirewound construction). Extends the available value range upward.
- **TDK MLK1005S series (0402)** — 19 values from 0.6 nH to 22 nH, extending the catalogue to sub-nH values that Coilcraft 0402HP doesn't carry.

These are first-order parasitic estimates suitable for synthesis-time sims; for design-final precision, fetch a real S-parameter file from the vendor (planned via the `vendor.fetch_*` tools on the Tier-3 roadmap).

### Docs / branding
- Logo SVG (full and mark variants) under `assets/`
- mkdocs-material site at `mkdocs.yml` with auto-generated tool API docs
  via mkdocstrings
- Per-page docs: index, getting-started, architecture, installation,
  tool-catalog, examples, reference (envelope / Touchstone / E-series),
  contributing, changelog, security
- `.github/workflows/docs.yml` builds and deploys to GitHub Pages

### Repo conventions
- Issue templates: bug, feature, question (config disables blank issues)
- Pull request template
- CODEOWNERS
- Dependabot config (weekly, grouped dev-tools / runtime / actions)
- Code of Conduct (Contributor Covenant 2.1)



## [0.1.0] — 2026-04-22

Initial release of the four-package monorepo.

### `rf-mcp-common` 0.1.0
- Envelope response model with `ok()` / `error()` constructors and
  `Timer` for runtime metadata.
- Hz-strict Touchstone I/O wrapping `skrf.Network`: `read_touchstone`,
  `write_touchstone`, `network_to_touchstone`, `sparams_at`.
- E6/E12/E24/E48/E96/E192 component snap helper with signed percent error.
- Frequency, dB, dBm conversion helpers.
- Structured JSON logger + `tool_timer` context manager.

### `mcp-ltspice` 0.1.0
- 11 MCP tools covering the full synthesis-through-Monte-Carlo workflow:
  - `run_simulation` — LTspice (Wine) or ngspice fallback
  - `extract_sparameters` — `.raw` → `.s2p` via voltage/current method
  - `synthesize_lc_filter` — Butterworth + Chebyshev I (closed-form g) +
    Elliptic (scipy `ellipap` + weighted least-squares LC fit)
  - `place_transmission_zero` — Move shunt-LC notch to a target frequency,
    preserve L/C ratio, snap to E24/E96
  - `find_transmission_zeros` — peak-detect notches in S21
  - `substitute_real_components` — vendor parasitic tables (Coilcraft
    0402HP / 0603CS, Murata GJM C0G, Johanson L, TDK MLG)
  - `evaluate_filter_spec` — pass/fail per criterion with margin in dB
  - `optimize_filter` — Nelder-Mead, loss = sum of negative margins,
    E24/E96 snap
  - `monte_carlo_analysis` — joblib parallel Gaussian sampling, yield% +
    per-metric histograms
  - `stability_check` — Rollett K, |Δ|, Edwards-Sinsky μ
  - `render_response` — Bode PNG with frequency marker lines
- Analytical ABCD-chain S-parameter computation in `extract.py`
  (no simulator required for synthesis sanity checks)
- LTspice `.asc` generator + spicelib-based reader/modifier

### `mcp-rf-analysis` 0.1.0
- 19 MCP tools, all simulator-agnostic (Touchstone in/out)
- Bundled regulatory databases:
  - LTE bands 1-71 (3GPP TS 36.101)
  - 5G NR FR1 + FR2 (3GPP TS 38.101)
  - GNSS (GPS L1/L2/L5, GLONASS L1/L2, Galileo E1/E5/E6, BeiDou B1/B2/B3)
  - ISM allocations by ITU region
  - 802.11ah HaLow channels per region (US, EU, JP, KR, CN, SG, AU/NZ, IN)
  - FCC §15.247 + §15.205 restricted bands
  - ETSI EN 300 220 / 300 328 / 301 893 limits
  - 3GPP UE spurious + GNSS protection
- Spec template library (FCC Part 15.247, HaLow US LPF)
- Multi-radio coex matrix with predicted desense per aggressor/victim pair
- Link budget tools (Friis, log-distance, antenna isolation, desense)

### `mcp-qucs-s` 0.1.0
- Initial scaffold + status tool. Full implementation lands in
  Unreleased above.

### Examples
- `examples/halow_lpf/`: full end-to-end design of a 9th-order
  elliptic LPF for 802.11ah HaLow + multi-radio coex. Passes all 8 spec
  criteria with 86.6% Monte Carlo yield at 2% component tolerance.

[Unreleased]: https://github.com/RFingAdam/mcp-ltspice-qucs/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/RFingAdam/mcp-ltspice-qucs/releases/tag/v0.1.0
