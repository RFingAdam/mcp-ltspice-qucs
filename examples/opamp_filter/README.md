# Anti-aliasing op-amp filter

A 4th-order Butterworth LPF for the front-end of a 96 kSPS audio ADC.
Demonstrates the analog active-filter tools.

## Run

```bash
uv run python examples/opamp_filter/design.py
```

## What it shows

1. `cascaded_lpf_design` — split a 4th-order LPF into two cascaded
   Sallen-Key 2nd-order stages with the correct per-stage Q from the
   Butterworth polynomial roots
2. `find_opamp_for_application` — filter the bundled op-amp catalog by
   GBW, noise, and offset to pick a suitable part
3. `transfer_function_db` — analytical |H(f)| evaluation to verify the
   design before committing components

## Outputs

- `response.png` — magnitude plot
- `report.md` — per-stage R/C values + chosen op-amp + Nyquist
  rejection
