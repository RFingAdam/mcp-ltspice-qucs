# Suite architecture

How mcp-ltspice-qucs fits inside [eng-mcp-suite](https://github.com/RFingAdam/eng-mcp-suite).

The internal architecture of the three servers (interop contract,
Envelope shape, Touchstone exchange format) is documented in
[`ARCHITECTURE.md`](architecture.md). This page is about the **external**
boundary: what this MCP feeds, what it consumes, and which workflow
bundles include it.

---

## Position in eng-mcp-suite

mcp-ltspice-qucs sits in the **circuit-level synthesis + simulation**
layer of the engineering MCP stack. It deliberately stops at the
antenna port and at the schematic-to-layout boundary.

```
        ┌───────────────────────────────────────────────┐
        │   MCP client (Codex, Claude, IDE, etc.)       │
        └──────┬──────────────┬───────────────┬─────────┘
               │              │ via MCP       │
       ┌───────▼──────────┐  ┌▼──────────┐  ┌─▼─────────────────┐
       │ mcp-ltspice-qucs │  │ lineforge │  │ mcp-nec2-antenna  │
       │  (circuits +     │  │  (T-line) │  │  (wire / MoM)     │
       │   filters)       │  └───────────┘  └───────────────────┘
       └───────┬──────────┘
               │  Touchstone .s2p / .snp
       ┌───────▼──────────────────────────┐
       │  mcp-pcb-emcopilot               │  (layout-aware EMC / SI)
       │  mcp-emc-regulations             │  (limit / spec lookup)
       └──────────────────────────────────┘
```

### Feeds (this MCP produces output that)…

- **mcp-pcb-emcopilot**: candidate filter schematic + Touchstone
  `.s2p` for layout-aware insertion-loss budgeting and review.
- **mcp-emc-regulations**: predicted conducted-emission spectrum from
  SMPS designs for margin-check against CISPR 22 / CISPR 32.
- **mcp-rf-analysis** (internal): Touchstone output from `mcp-ltspice`
  is consumable by cascade / de-embed tools through the shared file
  contract.

### Consumes (this MCP accepts input from)…

- **lineforge**: characteristic impedance + εr_eff for matching
  network design on a known PCB cross-section.
- **mcp-nec2-antenna**: antenna feedpoint impedance for matching-
  network synthesis.

### Workflow bundles that include this MCP

| Bundle                  | Role of this MCP                                  |
| ----------------------- | ------------------------------------------------- |
| `rf-design`             | Filter + matching network synthesis               |
| `coexistence-review`    | Multi-radio band picking + co-existence filter design |
| `smps-emc`              | SMPS topology sizing + conducted-emission prediction |

See the [suite manifest](https://github.com/RFingAdam/eng-mcp-suite/blob/main/manifest.yaml)
for full bundle definitions.

---

## Design decisions

- **Three servers, one workspace.** `mcp-ltspice`, `mcp-qucs-s`, and
  `mcp-rf-analysis` share `rf-mcp-common` for the `Envelope[T]`
  response model, versioned `CircuitDocument`, backend/result contracts,
  optimization contracts, and Touchstone I/O. This lets an agent compose
  cross-server calls without reconstructing topology.
- **Circuit IR before backend syntax.** Supported LTspice ASC, SPICE, Qucs
  schematic, and Qucsator netlist inputs become an explicit pin-to-net graph.
  Unsupported constructs are blocking diagnostics, never guessed topology.
- **Model-aware optimization.** Component selection attaches immutable model
  identities to the IR. Final simulator validation and yield runs must attest
  the exact selected hashes before the result can be labelled validated.
- **Touchstone as the simulation exchange format.** Tools that exchange
  frequency-domain network data use Hz-strict Touchstone. Synthesis,
  catalog, EMC, and device-query tools return structured envelopes instead.
- **External simulators, not embedded.** LTspice/ngspice, qucsator-RF,
  and Xyce are invoked as subprocesses. This keeps licensing boundaries
  clear and lets users point at existing installations.
