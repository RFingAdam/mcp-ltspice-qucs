# HaLow LPF — End-to-End Design

This example finalizes a 9th-order elliptic low-pass filter for an
802.11ah HaLow transmitter (902-928 MHz) coexisting on the same device
with LTE B2/B3/B4/B25, WiFi 2.4 GHz, BLE, and a GPS receiver. The
design hits **all 8 coex spec criteria** with **86.6% Monte Carlo yield**
at 2% component tolerance — entirely through the MCP suite.

## Run it

```bash
uv run python examples/halow_lpf/design.py
```

The script takes about 30 seconds (most of it Monte Carlo). It calls
the same tools an LLM agent would call via MCP:

1. `synthesize_lc_filter` → 9th-order elliptic, fc=1.0 GHz, 0.1 dB ripple
2. `place_transmission_zero` × 4 → notches at GPS L1, EU 2f₀, NA 2f₀, NA 3f₀
3. `substitute_real_components` → Coilcraft 0402HP + Murata GJM C0G
4. `evaluate_filter_spec` → fail (real parts shift the response by ~10 dB)
5. `optimize_filter` → Nelder-Mead 3000 iterations, E96 snap → all pass
6. `find_transmission_zeros` → confirm achieved notches
7. `render_response` → S21/S11 PNG with marker lines
8. `monte_carlo_analysis` → 1000 trials, 2% Gaussian tolerance

## Outputs

| File | What |
|---|---|
| `spec.json` | The coex spec (passband + 6 stopband targets) |
| `starting_point.s2p` | Synthesized prototype before zero placement |
| `after_zero_placement.s2p` | Notches relocated to coex frequencies |
| `with_real_parts.s2p` | After Coilcraft / Murata substitution |
| `final.s2p` | After optimization + E96 snap (the deliverable) |
| `final.asc` | LTspice schematic, ready to open |
| `response.png` | Bode plot with frequency markers |
| `report.md` | Full pass/fail table + MC yield + per-metric stats |

## Spec target (from `spec.json`)

| Criterion | Target | Why |
|---|---|---|
| Passband IL | ≤ 0.5 dB over 1-928 MHz | Don't kill HaLow link budget |
| Passband RL | ≥ 15 dB over 1-928 MHz | Match-quality / VSWR |
| GPS L1 | ≥ 30 dB rejection at 1575.42 MHz | FCC restricted band; protect on-device GPS |
| EU 2f₀ | ≥ 55 dB at 1730 MHz | 2× 865 MHz lands in LTE B3 UL — collision |
| NA 2f₀ | ≥ 55 dB at 1853 MHz | 2× 926.5 MHz lands in LTE B25 DL — collision |
| ISM 2.4G low | ≥ 30 dB at 2400 MHz | Protect on-device BLE / WiFi 2.4 |
| ISM 2.4G high | ≥ 30 dB at 2484 MHz | Protect on-device BLE / WiFi 2.4 |
| NA 3f₀ | ≥ 40 dB at 2780 MHz | 3× ~927 MHz spurious into LTE B7 / B41 |

## Result (regenerated each run; latest in `report.md`)

```
=== Final (after optimization + E96 snap) (PASS) ===
Criterion                          Target     Measured     Margin
Passband IL                        0.5 dB      0.13 dB   +0.37 dB
Passband RL                       15.0 dB     15.22 dB   +0.22 dB
GPS L1 protection                 30.0 dB     33.05 dB   +3.05 dB
EU 2f0 (LTE B3 UL)                55.0 dB     82.22 dB  +27.22 dB
NA 2f0 (LTE B25 DL)               55.0 dB     56.08 dB   +1.08 dB
ISM 2.4G low (BLE/WiFi)           30.0 dB     77.58 dB  +47.58 dB
ISM 2.4G high                     30.0 dB     66.66 dB  +36.66 dB
NA 3f0                            40.0 dB     57.11 dB  +17.11 dB

Monte Carlo yield: 86.6% (866/1000) at 2% tolerance
```

## Why 9th order, not 7th

A 7th-order elliptic with E24 snap gets within 2-3 dB of the 55 dB
targets but doesn't quite cross. The optimizer gets stuck at a local
optimum that balances both 2f₀ targets equally short. Going to 9th
order adds a 4th transmission zero (we use it for GPS L1 protection)
and gives the optimizer enough degrees of freedom to satisfy every
criterion with margin.

## Limiting yield criterion

The 86.6% yield is bottlenecked by the passband return-loss margin
(+0.22 dB) — small component variations push RL below the 15 dB
target. To improve yield you can:

1. Loosen the spec to 12 dB RL (yield → ~98%)
2. Tighten component tolerances to 1% (E192 inductors, ±1% C0G caps)
3. Add a re-optimization step that pessimistically fits to a worst-case
   tolerance corner instead of nominal values
