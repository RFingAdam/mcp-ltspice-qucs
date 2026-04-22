# Architecture

The repo ships **three MCP servers** plus a tiny shared package, all
under one uv workspace.

## Workspace layout

```
mcp-ltspice-qucs/
├── packages/
│   ├── rf-mcp-common/          # Shared contracts (no MCP transport)
│   ├── mcp-ltspice/            # FastMCP server: filter synthesis, sim, eval
│   ├── mcp-rf-analysis/        # FastMCP server: skrf + bands + coex
│   └── mcp-qucs-s/             # FastMCP server: distributed-element sim
└── examples/halow_lpf/         # End-to-end demo
```

## The three servers and their responsibilities

```
                        ┌────────────────────────────┐
                        │      LLM agent / tool      │
                        │       (Claude, etc.)       │
                        └─────┬──────┬──────┬────────┘
                              │      │      │
              MCP stdio/HTTP  │      │      │
                ┌─────────────┘      │      └──────────────┐
                │                    │                     │
                ▼                    ▼                     ▼
       ┌────────────────┐  ┌──────────────────┐  ┌──────────────────┐
       │  mcp-ltspice   │  │ mcp-rf-analysis  │  │   mcp-qucs-s     │
       │ ───────────────│  │ ─────────────────│  │ ─────────────────│
       │ synthesize_*   │  │ cascade_networks │  │ run_sp_analysis  │
       │ place_zero     │  │ deembed          │  │ harmonic_balance │
       │ substitute_real│  │ list_lte_bands   │  │ microstrip_synth │
       │ optimize       │  │ check_coex_matrix│  │ richards_kuroda  │
       │ monte_carlo    │  │ compute_desense  │  │ noise_params     │
       │ evaluate_spec  │  │ evaluate_template│  │ export_touchstone│
       │ render_png     │  │ ...              │  │ ...              │
       └───────┬────────┘  └─────────┬────────┘  └─────────┬────────┘
               │                     │                     │
               │       Touchstone (.s2p / .snp) on disk    │
               └─────────────────────┴─────────────────────┘
                                     │
                                     ▼
                            ┌────────────────┐
                            │ rf-mcp-common  │
                            │ ───────────────│
                            │ Envelope       │
                            │ Touchstone I/O │
                            │ ESeries snap   │
                            │ JSON logger    │
                            └────────────────┘
```

## Interop contract

The three servers don't import each other. They communicate exclusively
through:

- **Touchstone files** on disk (`.s2p` / `.snp`). All servers read and
  write via `rf_mcp_common.touchstone`, which wraps `skrf.Network` with
  Hz-strict frequency handling.
- **The `Envelope` response model** (`rf_mcp_common.envelope`):

  ```python
  class Envelope[T]:
      status: Literal["ok", "error"]
      data: T | None
      warnings: list[str]
      metadata: dict[str, Any]   # tool_version, runtime_sec, ...
      error: str | None
  ```

  Every tool in every server returns this shape. Tools never raise to
  the MCP transport — they catch their own errors and convert them to
  `error()` envelopes.

- **Hz-only frequency conventions on the wire.** Display units (MHz,
  GHz) appear in human-readable messages and tool descriptions but
  never in tool arguments or stored data.

This contract means a fourth MCP — say, one wrapping CST Studio or a VNA
— can drop in without modifying the existing servers, as long as it
honors the same Touchstone + Envelope conventions.

## mcp-ltspice tool flow

```
┌────────────────────┐
│ synthesize_lc_filter│
└──────┬─────────────┘
       │ produces .asc + (optional) analytical .s2p preview
       │
       ├──► place_transmission_zero ──► (updates .asc, recompute .s2p)
       │
       ├──► substitute_real_components ──► swaps ideal L/C → vendor parts
       │                                   with parasitic R/L/C tables
       │
       ├──► run_simulation ──► invokes LTspice (Wine) or ngspice
       │           │
       │           ▼
       │    extract_sparameters ──► .raw → .s2p
       │
       ├──► evaluate_filter_spec ──► pass/fail per criterion
       │
       ├──► optimize_filter ──► scipy.optimize.minimize over analytical
       │                       S-params; loss = sum of negative margins
       │
       ├──► monte_carlo_analysis ──► joblib parallel, Gaussian tolerance,
       │                             yield% + per-metric histograms
       │
       └──► render_response ──► S21/S11 Bode PNG with marker lines
```

## Why analytical S-params alongside a real simulator

Two reasons:

1. **Speed** — the optimizer and Monte Carlo run thousands of S-param
   evaluations. Analytical ABCD-chain math (in `extract.py`) handles
   this in milliseconds; spawning a SPICE process per evaluation would
   be too slow by 4-6 orders of magnitude.
2. **CI portability** — the analytical path has no external
   dependencies, so the test suite (and the CI matrix) runs without
   needing LTspice or ngspice installed. Real-simulator integration
   tests are gated by pytest markers (`@pytest.mark.ltspice` /
   `@pytest.mark.ngspice`) and skip cleanly when the simulator is
   absent.

The real simulator is used for the final design verification once the
optimizer has converged, where parasitic effects, modulation transients,
and vendor SPICE subcircuits matter.

## Resource bundling

Each server bundles its data files inside the package source tree
(`packages/<name>/src/<pkg>/resources/`) and reads them via
`importlib.resources` so editable installs work the same as wheels.
Hatchling auto-includes anything under `src/` so no `force-include`
incantation is needed.

## Versioning

Each package has its own `pyproject.toml` and version. All start at
`0.1.0` and bump independently per [Semver](https://semver.org/).
`rf-mcp-common` is the contract; breaking changes there cascade to
every server and warrant a major bump for all four.
