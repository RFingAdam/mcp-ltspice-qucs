# Basic LPF — public example

A generic 5th-order Butterworth low-pass filter at fc = 1 GHz, on
50 Ω. Demonstrates the full `mcp-ltspice` workflow without revealing
any application-specific design.

## Run it

```bash
uv run python examples/basic_lpf/design.py
```

Takes ~25 seconds (most of it Monte Carlo).

## What it shows

1. `synthesize_lc_filter` — closed-form Butterworth synthesis
2. `substitute_real_components` — Coilcraft 0402HP + Murata GJM C0G
3. `evaluate_filter_spec` — pass/fail per criterion with margin
4. `render_response` — Bode PNG with frequency markers
5. `monte_carlo_analysis` — 1000-trial yield analysis at 5% tolerance

## Result

```
=== Spec compliance (PASS) ===
Criterion                    Target     Measured     Margin   Status
Passband IL                  0.5 dB      0.02 dB   +0.48 dB     pass
Passband RL                 14.0 dB     24.57 dB  +10.57 dB     pass
2 x fc                      30.0 dB     30.85 dB   +0.85 dB     pass
3 x fc                      45.0 dB     48.16 dB   +3.16 dB     pass
5 x fc                      60.0 dB     70.16 dB  +10.16 dB     pass

VERDICT: PASS  |  MC yield: 99.0%
```

## Outputs (generated each run)

- `basic_lpf.s2p` — analytical Touchstone of the realized filter
- `basic_lpf.asc` — LTspice schematic
- `response.png` — S21/S11 Bode plot

These artifacts are checked into the repo as a known-good reference
for CI.

## Adapting it

Edit `spec.json` to your own targets, or duplicate the folder under a
name that matches `examples/private/` or `examples/_local/`
(gitignored) for proprietary designs.
