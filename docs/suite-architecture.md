# Suite architecture

How mcp-ltspice-qucs fits inside [eng-mcp-suite](https://github.com/RFingAdam/eng-mcp-suite).

The internal architecture of the three servers (interop contract,
Envelope shape, Touchstone exchange format) is documented in
[`ARCHITECTURE.md`](architecture.md). This page is about the **external**
boundary — what this MCP feeds, what it consumes, and which workflow
bundles include it.

---

## Position in eng-mcp-suite

mcp-ltspice-qucs sits in the **circuit-level synthesis + simulation**
layer of the engineering MCP stack. It deliberately stops at the
antenna port and at the schematic-to-layout boundary.

```
        ┌───────────────────────────────────────────────┐
        │   AI agent (Claude Code / Desktop)            │
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

- **mcp-pcb-emcopilot** — finalized filter schematic + Touchstone
  `.s2p` for layout-aware insertion-loss budgeting.
- **mcp-emc-regulations** — predicted conducted-emission spectrum from
  SMPS designs for margin-check against CISPR 22 / CISPR 32.
- **mcp-rf-analysis** (internal) — every `mcp-ltspice` filter result is
  consumable by the cascade / de-embed tools without re-touching disk.

### Consumes (this MCP accepts input from)…

- **lineforge** — characteristic impedance + εr_eff for matching
  network design on a known PCB cross-section.
- **mcp-nec2-antenna** — antenna feedpoint impedance for matching-
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
  response model and Touchstone I/O. This lets an agent compose
  cross-server calls without serialization gymnastics.
- **Touchstone as the wire format.** Every solver consumes and
  produces Touchstone (Hz-strict). This is the same format the rest
  of eng-mcp-suite expects, so cross-MCP composition is free.
- **External simulators, not embedded.** LTspice and Qucs-S are
  invoked as subprocesses. Keeps licensing clean and lets users
  point at their existing install.
