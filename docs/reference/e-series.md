# E-series snap

IEC 60063 preferred numbers — the realizable component value series for
resistors, capacitors, and inductors. We support **E6 / E12 / E24 /
E48 / E96 / E192** for snapping continuous-value optimizer output to
something you can actually buy.

## Choice guide

| Series | Tolerance grade | When to use |
|---|---|---|
| E6  | ±20% | Pre-selection only; almost never realizable |
| E12 | ±10% | Hobby / low-precision power supplies |
| E24 | ±5%  | General passive components |
| E48 | ±2%  | RF inductors (Coilcraft 0402HP standard) |
| E96 | ±1%  | Precision resistors, RF capacitors |
| E192| ±0.5% | Reference / metrology grade |

## API

::: rf_mcp_common.ecomp
    options:
      heading_level: 3
      show_source: true
      members_order: source
      show_root_heading: false
      members:
        - ESeries
        - SnapResult
        - snap_to_eseries
