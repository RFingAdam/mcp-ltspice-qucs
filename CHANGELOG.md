# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Each package under `packages/` versions independently; entries below are
grouped by package.

## [Unreleased]

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
