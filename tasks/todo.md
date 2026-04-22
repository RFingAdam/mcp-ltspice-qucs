# Build Plan: RF Engineering MCP Suite

Live task list mirroring the approved plan. Check items as completed.

## Phase 0 — Bootstrap

- [x] Create monorepo directory structure
- [x] git init, Apache-2.0 LICENSE, .gitignore, README skeleton
- [x] Workspace pyproject.toml with uv workspace + ruff/mypy/pytest config
- [ ] Per-package pyproject.toml × 4
- [ ] Install ngspice + Wine + Qt6 dev libs via apt (single sudo)
- [ ] Install LTspice via Wine
- [ ] Build Qucs-S from source
- [ ] `uv sync` to populate dev environment
- [ ] Set up pre-commit hooks
- [ ] Smoke-test ngspice / LTspice / Qucs-S
- [ ] Initial commit: `chore: scaffold workspace and toolchain`

## Phase 1 — rf-mcp-common + mcp-ltspice core

- [ ] `rf-mcp-common`: Envelope, Touchstone, ECompSnap, structured logger
- [ ] `mcp-ltspice` tool 1: `run_simulation` (LTspice -b + ngspice fallback)
- [ ] `mcp-ltspice` tool 2: `extract_sparameters`
- [ ] `mcp-ltspice` tool 3: `synthesize_lc_filter` (ellip/butter/cheby1, T/Pi)
- [ ] `mcp-ltspice` tool 4: `place_transmission_zero`
- [ ] `mcp-ltspice` tool 7: `evaluate_filter_spec`
- [ ] `mcp-ltspice` tool 10: `render_response`
- [ ] Round-trip + zero-placement + spec-eval + renderer tests passing

## Phase 2 — mcp-rf-analysis

- [ ] Network ops tools (cascade, deembed, renorm, stability, smith)
- [ ] Spec evaluation tools (rejection, passband, template eval)
- [ ] Band databases: lte.json, 5gnr.json, gnss.json, ism.json, halow.json
- [ ] Coex matrix tool
- [ ] Link budget + antenna isolation tools
- [ ] Touchstone utility tools (compare, delay, fit)
- [ ] Spec template library

## Phase 3 — Remaining mcp-ltspice tools

- [ ] Tool 5: `find_transmission_zeros`
- [ ] Tool 6: `substitute_real_components` (Coilcraft, Murata, Johanson, TDK)
- [ ] Tool 8: `optimize_filter`
- [ ] Tool 9: `monte_carlo_analysis`
- [ ] Tool 11: `stability_check`

## Phase 4 — mcp-qucs-s

- [ ] Headless Qucs-S runner
- [ ] Tool 1: `run_sp_analysis`
- [ ] Tool 2: `run_harmonic_balance` (optional Xyce backend)
- [ ] Tool 3-5: Microstrip line/filter/coupler synthesis
- [ ] Tool 6: `extract_noise_parameters`
- [ ] Tool 7: `lumped_to_distributed`
- [ ] Tool 8: `export_touchstone`

## Phase 5 — HaLow LPF design

- [ ] examples/halow_lpf/spec.json (the user's coex spec)
- [ ] examples/halow_lpf/design.py (end-to-end script)
- [ ] Final synthesized filter passes all spec targets
- [ ] Monte Carlo yield ≥85%
- [ ] examples/halow_lpf/report.md with spec table + BOM

## Phase 6 — Polish for 10/10 GitHub

- [ ] Per-package READMEs
- [ ] ARCHITECTURE.md
- [ ] CONTRIBUTING.md, CHANGELOG.md, SECURITY.md
- [ ] mkdocs site
- [ ] .github/workflows/ci.yml (matrix + ruff + mypy + pytest)
- [ ] Release workflow scaffold
- [ ] gh repo create --private + push + verify CI green
- [ ] Set repo topics

## Review (filled at end)

_To be populated when implementation completes._
