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
└── examples/                   # Worked end-to-end designs (basic_lpf,
                                #   buck_smps, emc_compliance,
                                #   filter_compare, opamp_filter)
```

## The three servers and their responsibilities

```
                        ┌────────────────────────────┐
                        │      LLM agent / tool      │
                        │       (Claude, etc.)       │
                        └─────┬──────┬──────┬────────┘
                              │      │      │
                 MCP stdio    │      │      │
                ┌─────────────┘      │      └──────────────┐
                │                    │                     │
                ▼                    ▼                     ▼
       ┌────────────────┐  ┌──────────────────┐  ┌──────────────────┐
       │  mcp-ltspice   │  │ mcp-rf-analysis  │  │   mcp-qucs-s     │
       │ ───────────────│  │ ─────────────────│  │ ─────────────────│
       │ synthesize_*   │  │ cascade_networks │  │ run_sp_analysis  │
       │ place_zero     │  │ deembed          │  │ harmonic_balance │
       │ substitute_real│  │ list_lte_bands   │  │ synthesize_line  │
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
                            │ CircuitDocument│
                            │ Backend/results│
                            │ Optimization   │
                            │ Touchstone I/O │
                            │ ESeries snap   │
                            │ Jobs/artifacts │
                            │ Sandboxed runs │
                            │ JSON logger    │
                            └────────────────┘
```

## Interop contract

`mcp-ltspice` has a one-way package dependency on `mcp-rf-analysis` for
the closed-loop coexistence workflow; there is no reverse dependency or
cycle. Otherwise the servers exchange simulation data through:

- **Touchstone artifacts** (`.s2p` / `.snp`). All servers read and write via
  `rf_mcp_common.touchstone`, which wraps `skrf.Network` with Hz-strict
  frequency handling. Durable workflows expose opaque workspace/artifact IDs
  and bounded `artifact://` reads; synchronous compatibility tools may also
  return a local path.
- **The `Envelope` response model** (`rf_mcp_common.envelope`):

  ```python
  class Envelope[T]:
      status: Literal["ok", "error"]
      data: T | None
      warnings: list[str]
      metadata: dict[str, Any]   # tool_version, runtime_sec, ...
      error: str | None
  ```

  Library-level functions and successful MCP calls use this shape. At the MCP
  boundary, `EnvelopeErrorMiddleware` converts an error envelope into a
  structured `ToolError` with a stable coarse code, so clients do not mistake
  a failed simulation for a successful tool call.

- **Hz-only frequency conventions on the wire.** Display units (MHz,
  GHz) appear in human-readable messages and tool descriptions but
  never in tool arguments or stored data.

- **`CircuitDocument` 1.0 and backend-neutral results.** Supported LTspice
  ASC, SPICE, Qucs schematic, and Qucsator netlist subsets become an explicit
  pin-to-net graph. Backend adapters compile that graph and normalize output
  without extrapolation; unsupported syntax remains a blocking diagnostic.

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
       ├──► substitute_real_components ──► snaps ideal L/C to catalog values
       │                                   and returns parasitic metadata
       ├──► simulate_realized_filter ──► model netlists + two sweeps + .s2p
       │
       ├──► run_simulation ──► invokes LTspice (Wine) or ngspice
       │           │
       │           ▼
       │    extract_sparameters ──► two matched-port sweeps → full .s2p
       │
       ├──► evaluate_filter_spec ──► pass/fail per exact criterion grid
       │
       ├──► optimize_filter ──► scipy.optimize.minimize over analytical
       │                       S-params; loss = sum of negative margins
       │
       ├──► monte_carlo_analysis ──► bounded parallel Gaussian tolerance,
       │                             summary metrics + optional JSONL artifact
       │
       ├──► simulation_submit / analysis_submit
       │        └──► durable job state, progress, cancel/retry, artifacts
       │
       └──► render_response ──► S21/S11 Bode PNG with marker lines
```

## Why analytical S-params alongside a real simulator

Two reasons:

1. **Speed** — the optimizer and Monte Carlo run thousands of S-param
   evaluations. Analytical ABCD-chain math (in `extract.py`) handles
   this in milliseconds; spawning a SPICE process per evaluation would
   be too slow by 4-6 orders of magnitude.
2. **CI portability** — the analytical path has no external simulator
   dependency. Required Linux CI installs ngspice. A weekly capability-labelled
   self-hosted matrix runs LTspice, qucsator-RF, and Xyce integrations; local
   tests skip only when their backend is absent.

A real simulator verifies generated supported circuits after analytical
screening. `simulate_realized_filter` instantiates registered two-pin `.lib`
models or explicit approximate passive subcircuits and returns hashes and
pin mappings. General multi-pin/arbitrary schematic realization remains
unsupported and is rejected rather than approximated silently.

The generic IR optimizer is the slower simulator-backed path for arbitrary
supported graphs. It uses seeded bounded/discrete screening, hard constraints,
named corners, exact model-hash attestation, yield sampling, and optional
independent-backend validation. Fixed subcircuit/model/Touchstone values cannot
be tuned as though they were parameterized; generic lumped models and explicit
instance parameters can be.

## Execution and trust boundary

Simulator inputs are imported into immutable, checksum-addressed workspaces.
SPICE include trees are resolved and copied before execution; traversal,
symlink escapes, missing dependencies, and mutation are rejected. External
processes run in their own process group with timeout/cancellation tree cleanup.
ngspice uses a no-network bubblewrap profile by default when available;
unsandboxed execution requires an explicit trusted-input opt-in. Remote
transport is not enabled by the console entry points.

## Resource bundling

Each server bundles its data files inside the package source tree
(`packages/<name>/src/<pkg>/resources/`) and reads them via
`importlib.resources` so editable installs work the same as wheels.
Hatchling auto-includes anything under `src/` so no `force-include`
incantation is needed.

## Versioning

Each package has its own `pyproject.toml` and version, bumping
independently per [Semver](https://semver.org/). The current minor
release is `0.6.0`. Runtime package and FastMCP server versions are derived
from installed distribution metadata rather than duplicated in source.
`rf-mcp-common` is the contract layer; breaking
changes there cascade to every server and warrant a major bump for
all four.

`mcp-qucs-s` implements Qucsator S-parameter and noise analysis plus Xyce
harmonic balance. Simulator-dependent tools report a clear error when their
backend is unavailable.
