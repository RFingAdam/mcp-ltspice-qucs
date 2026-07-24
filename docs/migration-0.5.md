# Migration guide: 0.5 remediation surface

## Canonical tool names

Use underscore-separated primary names. Dotted category names such as
`filter.synthesize_lc`, `power.design_buck`, and `sim.run` remain registered
for one compatibility window, but their MCP metadata marks them deprecated
with `remove_in = "1.0.0"` and identifies the canonical replacement.

The notable non-mechanical changes are:

| Deprecated/old name | Canonical name |
|---|---|
| `filter.evaluate_spec` / Python wrapper `evaluate_filter_spec_tool` | `evaluate_filter_spec` |
| broad `.asc` rendering behavior | `render_generated_lc_ladder_asc` |
| dotted aliases in general | Read `meta.canonical_name` from the tool contract |

Clients should discover tools rather than hard-code the complete list. The
checked-in `tests/tool_contract_snapshot.json` is the release contract for
names, schemas, annotations, and deprecation metadata.

## Error handling

An `Envelope(status="error")` is still available to direct Python callers.
Over MCP it is now a failed tool invocation with a JSON `ToolError` payload:

```json
{"code":"INVALID_INPUT","message":"..."}
```

Clients must handle MCP execution errors instead of assuming every call result
contains a successful envelope.

## Simulation inputs and outputs

- Simulator files and include trees are copied into an immutable workspace.
  Paths outside the imported dependency snapshot are rejected.
- Unsandboxed trusted execution is explicit. Do not expose the stdio process
  through a remote transport without a separate authentication/isolation
  review.
- Durable workflows return opaque `workspace_id`, `job_id`, and `artifact_id`
  values. Use `job_get`, `job_list_artifacts`, and `artifact_read`; do not
  derive host paths from IDs.
- Large sweeps and traces may return a JSONL artifact manifest instead of an
  inline array.

## Numerical semantics

- Filter kind and topology are explicit throughout tuning and statistical
  analysis.
- TDR requires transform-compatible data and reports whether it is a
  DC-anchored low-pass or band-pass transform.
- Delay analysis requires a caller-selected band.
- Passband/spec evaluation rejects out-of-range bands and inserts exact edge
  frequencies.
- Equivalent-circuit values are accompanied by fit residual, solver status,
  bounds, validity range, and warnings.

## Client setup

Codex uses the repository's `.codex/config.toml`; Claude Code uses
`.mcp.json`. Both start the locked workspace entry points. Trust the project,
run `codex mcp list` or `claude mcp list`, and verify the three servers before
requesting a simulation.
