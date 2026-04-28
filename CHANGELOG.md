# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Each package under `packages/` versions independently; entries below are
grouped by package.

## [Unreleased]

### `mcp-ltspice` — turn-key elliptic LPF design platform

Six new MCP tools enabling fully-automated coexistence-aware filter
design from spec to BOM, with real-vendor S-parameters fetched on
demand:

- **`fetch_coilcraft_s2p`** (issue #9) — pull and cache Coilcraft
  S-parameter Touchstone files from public part pages. Stdlib-only HTTP
  with retries and exponential backoff; cached at
  `~/.cache/mcp-ltspice/coilcraft/`.
- **`fetch_murata_spice`** (issue #10) — pull and cache Murata SPICE
  `.lib` (or `.s2p`) for any GRM/GJM part with a known URL.
- **`register_user_vendor_dir`** (issue #11) — index a user-supplied
  directory of `.s2p` / `.lib` / `.inc` files under a namespace; surfaced
  through `list_user_vendor_parts` and usable by
  `substitute_real_components`.
- **`vendor_cache_manifest`** — walk the vendor-fetch cache tree and
  return file metadata (size, sha256) for inspection / cleanup.
- **`place_zeros_for_coex`** (issue #12) — restricted-band-aware
  transmission-zero solver. Given fundamental passband, harmonic orders,
  and victim bands (LTE / 5G NR / GNSS / ISM / FCC-restricted), returns
  the severity-weighted optimal TZ frequencies plus a markdown rationale
  and a list of unprotected victims.
- **`validate_against_spice`** (issue #16) — run actual ngspice / LTspice
  on a substituted-real-vendor schematic and reconcile against the
  analytical S2P preview. Returns per-frequency Δ|S21| / Δphase, max
  deltas, flagged regions, and a verdict (`agree` / `minor_disagreement`
  / `disagree` / `spice_unavailable`). Closes the analytical-vs-real
  gap that previously had to be checked by hand.

`substitute_real_components` extended (issue #13) with three new keyword
arguments:

- `srf_margin: float = 0.0` — when > 0, rejects parts whose
  `srf_hz < srf_margin × max_spec_freq_hz` and substitutes the
  next-best neighbour from the same vendor series.
- `max_spec_freq_hz` / `spec` — provide either to derive the SRF
  threshold (auto-derived from `passband.f_stop` and
  `stopband_targets[*].freq` when `spec` is given).
- `max_value_drift_pct: float | None = 25.0` — bounds how far from the
  ideal value the SRF-aware substitution may drift; raises a clear
  `SrfRejectionError` if no qualifying part is within bound.

The legacy call signature is fully back-compatible; default
`srf_margin=0.0` preserves the previous behaviour exactly.

### `examples/halow_lpf/` — replaced

The previous worldwide HaLow LPF example was incoherent: three
competing design flows producing contradictory `.s2p` files, a
vendor-bounded variant with 0% Monte Carlo yield, and documentation
recommending a different order than the comparison report's actual
winner. **None** of the `.s2p` files were SPICE-validated.

The old files are archived at `examples/_archive/halow_lpf_v0/` (local
only, gitignored). The new pipeline is a single canonical
`design.py` that exercises every relevant MCP tool — including all six
new ones above — and produces a complete deliverable bundle:

- 9th-order elliptic LPF passband 863–928 MHz (worldwide HaLow SKU)
- Murata Type 2HK (LBAA0Z02HK, +23 dBm) target
- Spec compliance: PASS, all stopband + passband criteria
- Monte Carlo yield: ~74 % at 2 % component tolerance
- Bundled PDF report with schematic, S2P plot, BOM, MC histograms

### `mcp-qucs-s`
- Implemented all 4 closed-form synthesis tools (no Qucs-S install required):
  - `synthesize_microstrip_line` — Hammerstad-Jensen W/L from Z₀
  - `analyze_microstrip_tool` — Z₀ / ε_eff / wavelength from W
  - `synthesize_coupler` — branch-line / rat-race / coupled-line / Lange
  - `lumped_to_distributed` — Richards transformation + Kuroda identities
- Implemented all 4 simulator-driven tools with graceful degradation:
  - `run_sp_analysis`, `extract_noise_parameters`, `export_touchstone`,
    `run_harmonic_balance` return clean error envelopes when Qucs-S /
    Xyce are missing instead of crashing
- Added `status` tool for capability discovery
- Qucs-S `.dat` parser + Touchstone exporter

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
