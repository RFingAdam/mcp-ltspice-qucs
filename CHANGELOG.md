# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Each package under `packages/` versions independently; entries below are
grouped by package.

## [Unreleased]

### Added — Qucs-S noise parameters (#25, mcp-qucs-s)

`extract_noise_parameters` was the last scaffolded tool in the suite. It now
runs a real noise analysis and returns the four classical noise parameters
per frequency: NF50, Fmin, Γopt and Rn. It also evaluates the noise figure
at any source reflection coefficient via

    F(Γs) = Fmin + (4·Rn/Z0)·|Γs − Γopt|² / ((1−|Γs|²)·|1+Γopt|²)

which is the number that actually matters once a real LNA input match
replaces the ideal Γopt.

Anchored on an identity with no fitting in it: **a passive network's noise
figure equals its insertion loss**, exactly, at T₀ = 290 K. Matched pads of
3, 6, 10 and 20 dB all report NF equal to their loss to within 1e-4 dB, with
Fmin = NF and Γopt = 0.

Two Qucs conventions were established empirically rather than assumed, since
either would produce plausible-but-wrong numbers:

- **`Rn` is in absolute ohms, not normalised to Z₀.** Recomputing F at
  Γs = 0 from Fmin/Rn/Γopt reproduces the reported NF50 to 4e-16 in ohms,
  and is off by 0.01 in noise factor if treated as normalised.
- **Noise temperature is per-component, not an analysis setting.**
  `.SP … Temp="16.85"` does not change device noise. Qucs defaults
  components to 26.85 °C, where a 10 dB pad correctly reads 10.13 dB
  — `1 + (L−1)·T/T₀`. `build_noise_netlist` therefore applies the IEEE
  reference 16.85 °C to resistor lines by default, leaving any explicit
  `Temp` alone and never rewriting non-resistor device cards.

### Added — harmonic balance via Xyce (#24, mcp-qucs-s)

`run_harmonic_balance` was scaffolded: it detected Xyce and then returned
`error("not yet implemented")`. It now runs a real analysis.

- **`mcp_qucs_s.harmonic_balance`** builds the `.HB` netlist, runs Xyce,
  and parses the frequency-domain output into a single-sided spectrum.
  One tone gives harmonic distortion; two give the intermodulation
  products, IM3, OIP3 and IIP3.
- **`sweep_compression_point`** is a new tool: sweep drive level and locate
  P1dB by interpolating where gain falls 1 dB below its small-signal value.
- Missing Xyce still returns an `error()` envelope, now pointing at the
  source-build instructions rather than at binaries that do not exist for
  Debian/Ubuntu.

Validated against a closed form rather than a plausible-looking number: for
a behavioural cubic `V(out) = a1·V(in) + a3·V(in)³` the third-order
intercept is exactly `A = sqrt(4·a1/(3·a3))`, and the computed IIP3 matches
it to **0.001 dB**. IM3 tracks the textbook 3:1 slope (30.00 dB per 10 dB
of drive) and the intercept does not drift with drive level. A diode pair
is the more realistic-looking test circuit but a poor validator — its I-V
is exponential, not cubic, so its products never follow a 3:1 slope.

Three Xyce behaviours found the hard way and now documented in
`docs/installation.md`:

- `.HB` needs one `NUMFREQ` entry **per tone**; a single value with two
  tones aborts the run.
- `^` in a behavioural `B`-source expression makes the HB startup transient
  diverge; explicit multiplication works.
- `.PRINT HB_FD` emits a **two-sided** spectrum, so a positive-frequency
  bin folds to twice its printed magnitude (DC does not).

### Fixed — documentation (docs/installation.md)

The Xyce section pointed at "pre-built debs" from Sandia. Those do not
exist: Sandia's Linux binaries are RHEL 8 RPMs that explicitly do not run
on Debian/Ubuntu, and the team no longer ships open-source binaries at all.
Replaced with the verified source build (Trilinos 14.4 + Xyce 7.10, ~17 min
on 8 cores), including the GCC-15 incompatibility that otherwise fails the
Trilinos build at ~30%.

### Fixed — three silent-correctness bugs (#31, #32, #35)

- **`.asc` I/O assumed UTF-8/LF (#31).** LTspice XVII writes UTF-16LE with a
  BOM, so `read_components` decoded mojibake, matched no `SYMBOL` line, and
  returned `{}` as though the schematic held no parts. Worse,
  `update_component` then rewrote the file as UTF-8/LF — silently
  re-encoding a user-authored schematic it had never successfully read.
  Encoding and line endings are now detected and preserved byte-for-byte
  outside the edited line; an undecodable file raises `AscDecodeError`
  instead of looking empty; and editing a refdes that is not present raises
  rather than rewriting the file unchanged.
- **The ngspice netlister mis-netlisted every non-lowpass ladder (#32).** It
  read each element's position from its refdes letter, so a highpass
  (series C, shunt L) was emitted as its dual and ngspice returned
  S-parameters for a circuit nobody designed. `z0` was hardcoded to 50 Ω
  besides. New `mcp_ltspice.asc_netlist` netlists from schematic *geometry*
  using the same coordinate rules LTspice applies, which is correct for any
  topology — including schematics this package did not generate — and reads
  the port impedance off the terminating resistors. On a generated lowpass
  it reproduces LTspice's own netlist node for node. Unknown symbol kinds
  raise `NotImplementedError` naming the symbol instead of being skipped.
- **`cascade_networks` extrapolated silently (#35).** It found the common
  grid with `numpy.intersect1d`, which needs exact float equality; two
  instruments never produce bit-identical grids, so real inputs hit the
  fallback that resampled every other network onto network 1's grid —
  extrapolating past the end of their measured data with no warning.
  `deembed_network` raised in the same situation. Both now share one
  documented policy in `common_frequency_grid`: restrict to the overlap of
  the measured ranges so resampling is always interpolation, raise when the
  ranges are disjoint, and report any trimming through the envelope's
  `warnings`.

### Fixed — Qucs-S could not be driven at all (mcp-qucs-s)

Running qucsator-RF 1.0.7 for the first time showed the Qucs-S backend was
unreachable end to end, for three independent reasons.

- **Nothing could produce simulator input.** `run_sp_analysis`,
  `export_touchstone` and `extract_noise_parameters` all take a path to a
  file no code in the package could generate, so using them meant
  hand-authoring a schematic in the Qucs GUI first. New
  `mcp_qucs_s.netlist` generates qucsator netlists, and a new
  `simulate_lc_ladder` MCP tool goes design → netlist → simulation →
  Touchstone in one call.
- **`find_qucs_s()` returned the GUI.** Discovery tried `qucs-s` *first*,
  which is the Qt application, not the engine. Passing it
  `-i netlist -o dat` opens a window and blocks forever on a headless box.
  Discovery now looks for `qucsator_rf` / `qucsator` and never falls back
  to the GUI; when only the GUI is present the error says so and points at
  the missing `--recurse-submodules` submodule build.
- **`parse_qucs_dat` could not read Qucs output.** It called `float()` on
  every line, but Qucs writes complex values as `+1.23e-10+j6.18e-08`, so
  every real `.dat` raised `ValueError`. It also expected each
  S-parameter split into `.r`/`.i` sections, a format qucsator-RF does not
  emit. Both layouts now parse.

Elements are specified by explicit position (`series_l` vs `shunt_l`)
rather than inferred from the refdes letter, so highpass and bandstop
ladders netlist correctly — the failure mode behind #32 in the ngspice
netlister, deliberately not repeated here.

Verified against real qucsator output: lowpass **and** highpass ladders
track the closed-form Butterworth response to within 0.001 dB, and a shunt
LC trap notches at its resonance. Regression cover includes a real
qucsator-RF `.dat` committed as a fixture, so the parser cannot drift from
the format the simulator actually writes.

`sch_path` is renamed to `netlist_path` throughout: qucsator consumes a
netlist, not the GUI's `.sch`, and the old name sent users looking for the
wrong file.

### Fixed — generated schematics were electrically disconnected (mcp-ltspice)

Found by running the pipeline against real LTspice 26.0.2 (Wine) and
ngspice 44.2 for the first time. Both simulator paths were broken
end-to-end: `run_simulation` reported success and `extract_sparameters`
then failed, so no user could get S-parameters out of either.

- **`generate_lpf_asc` never emitted a single `WIRE`.** The `_wire`
  helper was dead code, and LTspice determines connectivity purely from
  coordinates, so it netlisted every component onto its own `NC_*` nodes
  — no filter at all. The AC analysis returned `No. Points: 0` and a
  692-byte `.raw`, which the artifact-presence success policy accepted.
  Extraction then died with "No plots found in the RAW file". Symbols
  are now placed by solving for the anchor that puts their pins on the
  intended nodes, using pin offsets from the stock symbol library and
  the R90 convention `(dx, dy) → (-dy, dx)` verified against LTspice.
- **S-parameter extraction required an `I(Rs1)` trace that ngspice never
  writes**, so every ngspice run raised `IndexError` from `spicelib`.
  The port-1 current is now derived from `V(p1)` by Ohm's law, which is
  exact under the port convention the function already assumed. This
  also removes a sign-convention trap: SPICE orients a device's current
  by netlist node order, and LTspice emits `Rs1 p1 N001` for our
  schematic, so its `I(Rs1)` runs *out* of port 1 — pinning S11 at 0 dB
  for a perfectly matched filter. When the trace is present it is now
  used only as a consistency check, warning if magnitudes disagree.
- **`get_trace` raises instead of returning `None`**, so the "Missing
  required traces" `ValueError` was unreachable. It now reports the
  missing *and* available trace names.

Verified against both simulators: |S21| tracks the closed-form
doubly-terminated Butterworth response within 0.15 dB from 0.1–4 GHz,
with passband |S11| below −94 dB.

Regression cover for the class of bug, not just the instance:
`test_asc_connectivity.py` reimplements LTspice's coordinate-based
connectivity rule in pure Python, so **CI catches a floating pin with no
simulator installed**; the end-to-end tests now compare against theory
instead of asserting `raw_path.is_file()`, which a zero-point stub
satisfied.

### Fixed — LTspice first run blocks batch mode under Wine (mcp-ltspice)

Recent LTspice releases open a modal "Anonymously Share LTspice Usage
Data" dialog the first time they run in a Wine prefix. It appears even
under `-b`, so batch runs hang until the caller's timeout with an empty
log and no `.raw` — the symptom points nowhere near a consent prompt.
`run_simulation` now warns up front when `LTspice.ini` is absent from
the prefix, and converts the resulting `TimeoutExpired` into a
`RuntimeError` naming the dialog and giving both remedies.

### Fixed — documentation (docs/installation.md)

- The Qucs-S build recipe cloned without `--recurse-submodules`, so
  qucsator-RF — the actual simulation engine — was never built; the
  result surfaces much later as "Qucs-S not installed". Also adds the
  missing `flex`, `bison`, `gperf` and `dos2unix` build deps (`gperf`
  fails at cmake time, `dos2unix` only at ~78% of the build) and
  corrects `libqt6charts6-dev` → `qt6-charts-dev`.
- Documents the LTspice first-run dialog and the `wineboot -u` /
  `msiexec /qn` silent-install path.

### Fixed — simulator portability (mcp-ltspice)

Reported by [@cr4i50n](https://github.com/cr4i50n) in
[#28](https://github.com/RFingAdam/mcp-ltspice-qucs/pull/28) and
[#29](https://github.com/RFingAdam/mcp-ltspice-qucs/pull/29) after
trying to run the LTspice server on their own machine.

- **LTspice under Wine exits 1 on a successful run**, so the runner's
  strict return-code check rejected every good simulation. Success is
  now decided by whether the `.raw` artifact was produced; the return
  code is retained in `RunResult` and logged as a warning. Any stale
  `.raw` is deleted first, so the artifact cannot be a leftover. Same
  policy now applies to ngspice, matching `mcp-qucs-s`.
- **Wine does not translate POSIX paths in argv**, so `-Run /home/…`
  reached LTspice as a malformed drive spec. The `.asc` path is now
  converted with `winepath -w`.
- **A `.exe` no longer forces Wine on native Windows**, and
  `find_ltspice()` learned the Windows install locations and honours
  `WINEPREFIX`. Windows users previously always got "LTspice.exe found
  but Wine is not installed".
- **A set-but-invalid `LTSPICE_PATH`** silently fell through to ngspice;
  it now warns.
- **New `MCP_LTSPICE_SIMULATOR`** (`ltspice` | `ngspice`) pins simulator
  selection for ngspice-only deployments. Read by `detect_simulator()`
  and `run_simulation()` — deliberately *not* by `find_ltspice()`, which
  must keep reporting what is actually installed.

### Fixed — correctness (all packages)

Surfaced by enabling mypy, which had never actually run (see below).

- **`ladder_sparams_from_components` violated reciprocity.** S12 was
  computed as `2·(a·d − b·c)/denom`. That determinant is identically 1
  for a cascade of series-Z / shunt-Y two-ports, but evaluating it
  numerically overflows on long ladders: a 9th-order bandstop produced
  `inf` at one bin, and the finite-value guard rewrote S12 to 0 while
  S21 stayed finite.
- **`series_lc_parallel` leaked divide-by-zero / invalid-value
  RuntimeWarnings** and its clamp could not fire at the anti-resonant
  bin: it tested the quotient (NaN there) instead of flooring the
  denominator before dividing, as the neighbouring trap branch does.
- **`RF_MCP_LOG_LEVEL=debug` crashed every server at import.** The raw
  env string went straight to `Logger.setLevel`, which only accepts an
  int or an exact uppercase name. Case is now normalized, numeric levels
  are accepted, and an unusable value warns and falls back to INFO
  instead of taking down the server.
- **`mcp-rf-analysis` error envelopes were unattributable.** `_wrap`
  reported `func.__name__`, but most tools pass a lambda, so failures
  read `"<lambda> failed: …"`. They now name the tool that was called.
- **Unrecognized band filters returned an empty list**, so a misspelled
  region read as "there are no such bands". `list_lte_bands`,
  `list_gnss_bands`, and `list_ism_bands` now raise with the available
  values, matching `list_5gnr_bands` / `list_halow_channels`.
- **`list_spec_templates` used `.suffix` on a `Traversable`**, which
  only exists for filesystem-backed packages — `AttributeError` when
  zip-installed.
- **`richards.py` / `runner.py` called `.group()` on a possibly-`None`
  regex match**, crashing on any refdes without a digit.
- **`render_response` accepted an unvalidated list** as `freq_range_hz`,
  building a wrong-arity tuple from a 1- or 3-element list.
- **`network_from_dat` skipped the validation its sibling performed**,
  turning a partial Qucs-S `.dat` into a bare `KeyError`. Both loaders
  now share a checked path that also rejects ragged component arrays.

### Fixed — CI and tooling

- **CI had been red since 2026-05-13.** `tests/test_workspace_smoke.py`
  failed both `ruff format --check` and `ruff check`, and never ran: the
  top-level `tests/` directory was missing from `testpaths`.
- **mypy had never checked a single line.** It aborted with "Duplicate
  module named conftest" before reaching any source file, and CI hid
  that with `|| true`. Both fixed; the suppression is gone and the
  workspace is clean under the enforced rule set.
- **The docs site never deployed** — GitHub Pages was not enabled, so
  the `deploy` job failed on every push while `build` passed.
- **Fork PRs never got CI.** Approval was required for all first-time
  contributors, so neither #28 nor #29 was ever validated.
- **Simulator test gating matched too broadly.** `"ngspice" in
  item.keywords` also matches test names and parametrize ids, so a case
  parametrized over the string `"ngspice"` was skipped as though it
  needed the binary. Now uses `get_closest_marker`.
- `uv.lock` was left stale by the v0.4.0 version bump.

### Added — tests (458 → 533)

- **`mcp-rf-analysis` had no server-layer tests at all**; all 33 tools
  are now swept for the shared envelope contract, with the tool list
  discovered by introspection so new tools are covered automatically.
- **`mcp-qucs-s` had no `conftest.py`**, so its registered `qucs` marker
  gated nothing. Added, plus `xyce`. `sparams.py` — the only module that
  runs once Qucs-S is installed, and one that needs no binary to test —
  went from zero tests to full parse / round-trip / malformed-input
  coverage.
- **`check_coex_matrix`'s only test asserted nothing.** It was named for
  a 2H collision, conceded in its own comment that the chosen pair does
  not collide, and checked only that two keys existed. Repointed at LTE
  B3 DL, which HaLow 2H = 1830 MHz genuinely falls into, plus a
  clean-pair negative case.
- Portability coverage for the runner: `LTSPICE_PATH` precedence, Wine
  path translation and its fallback, native-Windows invocation, the
  simulator pin, and both halves of the artifact-vs-returncode policy.

## [0.4.0] — 2026-05-13

### Changed
- **License: Apache-2.0 → AGPL-3.0-or-later** (all four workspace
  packages: mcp-ltspice, mcp-qucs-s, mcp-rf-analysis, rf-mcp-common).
  Aligns with the eng-mcp-suite toolkit-wide AGPL move (the AGPL
  closes the "wrap as a paid SaaS without contributing back" gap by
  extending copyleft to network use). Underlying tools — Qucs-S (GPL),
  LTspice (proprietary), scikit-rf (BSD) — are runtime-invoked, not
  redistributed by these wrappers, so the wrapper's AGPL license is
  independent of theirs. See the
  [LICENSE_SUMMARY](https://github.com/RFingAdam/eng-mcp-suite/blob/main/LICENSE_SUMMARY.md)
  for the toolkit-wide rationale.

(no changes yet — next stream picks up from the v0.3.0 roadmap)

## [0.3.0] — 2026-05-06

### Correctness — silent failures resolved (all packages)

A five-agent code review identified six silent-failure paths the v0.2.0
correctness-honesty pass missed. All are fixed; the user-visible impact:

- **`mcp-ltspice.extract_sparams_from_raw`** previously filled only the
  diagonal of the S-matrix, leaving `S21` and `S12` as zero. Every
  Touchstone file produced via the LTspice → ngspice → `extract_sparameters`
  flow claimed −∞ dB through the network, which silently failed any
  `evaluate_filter_spec` check. The fix recovers the full S column from a
  single AC sweep (`S11` from `(V₁ − Z₀·I_Rs1)/(2√Z₀·a₁)`, `S21` from
  `V₂/(√Z₀·a₁)`) and fills column 2 by reciprocity + symmetry for the
  passive ladder filters this package synthesises. New flag
  `assume_reciprocal_symmetric` (default `True`) for the rare case where
  a user wants to merge two separate sweeps.
- **`mcp-rf-analysis.cispr_limit_at`** log-interpolated across the 5 MHz
  step in the CISPR 22/32 Class B QP table (56 dBµV → 60 dBµV), reporting
  ~58 dBµV at 12 MHz instead of the actual 60 dBµV. The same flaw was
  present in the FCC 15B mirror table. Both tables now encode the step
  with a paired `(5e6, 56.0), (5.001e6, 60.0)` entry, mirroring the
  pattern already used in `radiated.py`. Dead `_FCC_15_109_B_AT_3M` table
  removed.
- **`mcp-ltspice.runner`** never asserted `proc.returncode` on either the
  LTspice or ngspice path, so a non-zero exit (convergence failure,
  missing model lib) that left a stale `.raw` from a previous run was
  reported as success. The `RunResult.log_path` field was constructed
  but the file was never written for the LTspice path. Both fixed:
  returncode is now checked explicitly with stdout/stderr tail in the
  exception, and the log file is persisted on every run.
- **`mcp-ltspice.srf_check`** silently `continue`d past components whose
  vendor lookup raised `ValueError`/`KeyError`, then returned
  `severity='ok'` even though half the BoM was unaudited. Components
  that can't be audited are now surfaced via a new `unaudited` field
  and a warning per refdes; `severity` rises to at least `caution`.
- **`mcp-ltspice.synthesis.lc_filter`** unpacked `scipy.optimize.least_squares`
  results without checking `res.success`, so an unconverged elliptic fit
  silently returned wrong L/C values. Now raises `RuntimeError` with the
  optimiser status when convergence fails.

### Test pinning (31 new regression tests)

- New `tests/test_hardening_v030.py` (`mcp-ltspice`): contract tests for
  every fix above, including a `monkeypatch`-based round-trip of
  `extract_sparams_from_raw` that doesn't need a simulator install.
- New `tests/test_microstrip_loss_pin.py` (`mcp-qucs-s`): scaling-law
  tests for dielectric and conductor loss (linear in `tan_d`, linear in
  `f` for α_d, √f for α_c, 1/√σ for α_c) plus order-of-magnitude
  guardrails across FR-4 / Rogers / Duroid at 1–28 GHz. Catches missing-
  factor and wrong-conversion bugs without depending on textbook example
  values that vary across editions.
- HPF / BPF / BSF response now verified at orders 3, 5, 7, 9 (was only 3
  and 5 before). BPF additionally checked at narrow (Δ ≈ 0.01) and wide
  (Δ ≈ 0.67) fractional bandwidth — the regimes where classical LPF→BPF
  formulas break down.
- Vendor parasitic substitution: `lookup_part("coilcraft_0402hp", 4.7e-9)`
  now pinned to return an SRF in the documented 4–8 GHz neighbourhood,
  not just "non-zero".
- CISPR 22/32 Class B step at 5 MHz pinned with explicit `at 5.0 MHz` /
  `at 5.1 MHz` / `at 15 MHz` regression tests.

### Security

- `SECURITY.md` now spells out the untrusted-`.asc` attack surface
  explicitly. `runner.py` invokes external simulators on the supplied
  path with no sandboxing; a malicious `.asc` can include `.SUBCKT`,
  `.lib`, or `.include` directives the simulator will load. Document
  guidance: run inside a container/chroot if accepting external
  schematics.

### Cleanup

- Empty `packages/mcp-ltspice/src/mcp_ltspice/resources/` scaffolding
  tree (specs/ templates/ models/{coilcraft,johanson,murata,tdk}/, all
  empty) removed.
- `tasks/` (private workflow scratch) untracked and gitignored.
- All four packages bumped from 0.1.0 (stale since the suite tagged
  v0.2.0 without bumping pyprojects) to 0.3.0 to align with the suite tag.

### Test count

458 passed, 4 simulator-gated skips, 0 failures (was 427 / 4 / 0 in 0.2.0).

## [0.2.0] — 2026-04-28

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

[Unreleased]: https://github.com/RFingAdam/mcp-ltspice-qucs/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/RFingAdam/mcp-ltspice-qucs/releases/tag/v0.2.0
[0.1.0]: https://github.com/RFingAdam/mcp-ltspice-qucs/releases/tag/v0.1.0
