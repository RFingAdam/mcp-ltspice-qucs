# Buck SMPS — 5V → 3.3V at 2A

A complete first-pass buck converter design: power-stage sizing,
high-side MOSFET selection, control-loop compensator. Demonstrates the
power tools and the MOSFET catalog.

## Run

```bash
uv run python examples/buck_smps/design.py
```

## What it shows

1. `design_buck` — sizes L from inductor-ripple target, Cout from
   output-ripple target, computes peak/RMS inductor current and ESR
   limit
2. `find_mosfet_for_application` — filters the catalog by Vds, Id,
   Vgs threshold, sorts by Rds_on (lowest loss first)
3. `type2_compensator` — designs the control-loop compensator with
   60° phase boost, returns RC values

## Outputs

- `report.md` — sized inductor, capacitor, MOSFET pick + estimated
  losses, compensator RC values
