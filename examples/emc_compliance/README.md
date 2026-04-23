# EMC pre-compliance check

Predicts CISPR 22 conducted-emissions compliance from an SMPS line-
current spectrum and FCC Part 15.109 radiated-emissions compliance from
a clock-loop radiator. Demonstrates the EMC tools.

## Run

```bash
uv run python examples/emc_compliance/design.py
```

## What it shows

1. `predict_conducted_emissions` — converts a (freq, current) spectrum
   to LISN voltage and compares against CISPR 22 Class B
2. `predict_radiated_emissions_loop` — small-loop antenna estimate of
   radiated E-field from a current-carrying PCB loop
3. `fcc_part15_radiated_limit_at` and `cispr_limit_at` — direct limit
   lookups (handy for filling out spec-sheet tables)

## Outputs

- `report.md` — pass/fail per frequency for both standards
