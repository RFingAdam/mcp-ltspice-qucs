# rf-mcp-common

Shared contracts for the [mcp-ltspice-qucs](../../README.md) suite. Tiny
package that the three MCP servers depend on so the cross-tool interop
surface is defined exactly once.

## What's here

- **`Envelope`** — pydantic model for the structured response every MCP
  tool returns: `{status, data, warnings, metadata}`.
- **`touchstone`** — Hz-strict Touchstone reader/writer wrapping
  `skrf.Network`. Internal frequency unit is always Hz; display-friendly
  units belong in tool responses, not on the wire.
- **`ecomp`** — E24/E96/E192 series snap helpers for component value
  realization.
- **`logging`** — JSON structured logger with a per-tool-call timing
  context manager.

This package is **not** an MCP server. It only ships data models and
helpers.
