# Filter order comparison

Demonstrates the `compare_filter_orders` tool: side-by-side 5th vs 7th
vs 9th-order elliptic LPF designs, scored on pass/fail + Monte Carlo
yield + SRF severity − component count.

Generic spec — adapt `spec.json` for your application and the rest of
the pipeline runs unchanged.

## Run

```bash
uv run python examples/filter_compare/run.py
```

Takes ~3-5 minutes (most of it Monte Carlo at 500 trials per order).

## What it shows

For each order in `[5, 7, 9]`:
1. Synthesize the elliptic prototype at fc = 1 GHz
2. Place transmission zeros at the priority targets (5th uses 2, 7th
   uses 3, 9th uses 4)
3. Substitute Coilcraft 0402HP + Murata GJM C0G real parts
4. Vendor-bound differential-evolution optimize with
   `passband_weight=30`
5. Evaluate against the spec
6. SRF audit + sensitivity analysis + Monte Carlo at 2% tolerance
7. Score and pick the most shippable

## Outputs

- `order5_final.s2p`, `order7_final.s2p`, `order9_final.s2p` — analytical
  Touchstone files
- `report.md` — full BOM + spec table + score breakdown for every order
