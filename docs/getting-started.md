# Getting Started

Five minutes from clone to first synthesized filter.

## 1. Install

```bash
git clone https://github.com/RFingAdam/mcp-ltspice-qucs
cd mcp-ltspice-qucs
uv sync --all-packages
```

That's enough to run every tool that doesn't need a real SPICE
simulator (synthesis, evaluation, optimization, Monte Carlo, all of
`mcp-rf-analysis`, all closed-form `mcp-qucs-s` tools). If you also
want to run actual LTspice / ngspice / Qucs-S simulations, follow
[Installation](installation.md).

## 2. Verify

```bash
uv run pytest -q
```

You should see ~180 tests pass. Two simulator integration tests skip
when LTspice / ngspice are absent; that's expected.

## 3. Run the headline example

```bash
uv run python examples/halow_lpf/design.py
```

Takes ~30 seconds. Generates `examples/halow_lpf/{final.s2p,
final.asc, response.png, report.md}` and prints a pass/fail spec table
plus Monte Carlo yield.

## 4. Use a server from your MCP client

Add this to your client's MCP config (Claude Desktop, IDE plugin, etc.):

```json
{
  "mcpServers": {
    "ltspice": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp-ltspice-qucs",
               "mcp-ltspice"]
    },
    "rf-analysis": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp-ltspice-qucs",
               "mcp-rf-analysis"]
    },
    "qucs-s": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/mcp-ltspice-qucs",
               "mcp-qucs-s"]
    }
  }
}
```

Restart your client and the tools appear under the configured server
names.

## 5. Sanity-check a server interactively

```bash
uv run mcp-ltspice    # listens on stdio for MCP requests
```

In another shell, use the `mcp` CLI (or your IDE's MCP inspector) to
list tools and call `synthesize_lc_filter`.

## What next

- Read the [Architecture](architecture.md) page to understand the
  three-server layout and the Touchstone interop contract.
- Browse the [Tool Catalog](tool-catalog.md) for the full list of
  available tools.
- Try the [basic LPF example](examples/basic-lpf.md) end-to-end to see
  the workflow in action.
