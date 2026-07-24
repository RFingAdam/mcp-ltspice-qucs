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

The simulator-independent suite runs everywhere. Tests marked for
LTspice, ngspice, Qucs-S, or Xyce skip when that backend is absent.

## 3. Run the headline example

```bash
uv run python examples/basic_lpf/design.py
```

Generates `examples/basic_lpf/{basic_lpf.s2p,basic_lpf.asc,response.png}`
and prints a pass/fail spec table plus analytical Monte Carlo yield.

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

## 5. Sanity-check the registration

Restart your MCP client, inspect the configured server's tool list, and
call `synthesize_lc_filter`. Running `uv run mcp-ltspice` directly starts
the stdio server and waits for an MCP client; it is not an interactive
shell.

## What next

- Read the [Architecture](architecture.md) page to understand the
  three-server layout and the Touchstone interop contract.
- Browse the [Tool Catalog](tool-catalog.md) for the full list of
  available tools.
- Use the [General circuit workbench](circuit-workbench.md) to import a
  supported schematic/netlist, attach exact models, and run bounded
  simulator-backed optimization.
- Try the [basic LPF example](examples/basic-lpf.md) end-to-end to see
  the workflow in action.
