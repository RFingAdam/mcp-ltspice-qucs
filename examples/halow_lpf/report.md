# HaLow LPF Final Design Report

End-to-end design driven by the `mcp-ltspice` server (synthesis ->
zero placement -> vendor substitution -> optimization -> MC yield).

## Final BOM

| Refdes | Ideal value | Vendor value | Vendor part | SRF |
|---|---|---|---|---|
| C2 | 1.787e-12 | 1.300e-12 | murata_gjm_c0g | 5.31 GHz |
| C4 | 1.252e-12 | 1.690e-12 | murata_gjm_c0g | 5.81 GHz |
| C6 | 6.053e-13 | 1.330e-12 | murata_gjm_c0g | 10.07 GHz |
| C8 | 4.106e-11 | 4.220e-12 | murata_gjm_c0g | 1.28 GHz |
| L1 | 8.750e-09 | 6.650e-09 | coilcraft_0402hp | 3.10 GHz |
| L2 | 5.713e-09 | 6.490e-09 | coilcraft_0402hp | 4.00 GHz |
| L3 | 1.066e-08 | 6.340e-10 | coilcraft_0402hp | 2.60 GHz |
| L4 | 6.762e-09 | 8.060e-09 | coilcraft_0402hp | 3.50 GHz |
| L5 | 6.542e-09 | 8.450e-09 | coilcraft_0402hp | 3.50 GHz |
| L6 | 1.197e-08 | 1.740e-08 | coilcraft_0402hp | 2.30 GHz |
| L7 | 1.144e-08 | 1.050e-08 | coilcraft_0402hp | 2.30 GHz |
| L8 | 7.981e-11 | 1.070e-09 | coilcraft_0402hp | 11.80 GHz |
| L9 | 1.492e-08 | 9.090e-09 | coilcraft_0402hp | 2.00 GHz |

## Spec Compliance -- final design

| Criterion | Target | Measured | Margin | Status |
|---|---|---|---|---|
| Passband IL | 0.5 dB | +0.13 dB | +0.37 dB | PASS |
| Passband RL | 15.0 dB | +15.22 dB | +0.22 dB | PASS |
| GPS L1 protection | 30.0 dB | +33.05 dB | +3.05 dB | PASS |
| EU 2f0 (LTE B3 UL) | 55.0 dB | +82.22 dB | +27.22 dB | PASS |
| NA 2f0 (LTE B25 DL) | 55.0 dB | +56.08 dB | +1.08 dB | PASS |
| ISM 2.4G low (BLE/WiFi) | 30.0 dB | +77.58 dB | +47.58 dB | PASS |
| ISM 2.4G high | 30.0 dB | +66.66 dB | +36.66 dB | PASS |
| NA 3f0 | 40.0 dB | +57.11 dB | +17.11 dB | PASS |

**Overall: PASS**

## Monte Carlo Yield Analysis

- Component tolerance: 2.0% (3 sigma)
- Trials: 1000
- **Yield: 86.6%** (866 / 1000)

Failing criteria breakdown:
- Passband RL: 113 (11.3%)
- GPS L1 protection: 19 (1.9%)
- NA 2f0 (LTE B25 DL): 3 (0.3%)

## Detected transmission zeros (final design)

| Frequency | Depth | Q |
|---|---|---|
| 1.044 GHz | 39.8 dB | 80 |
| 1.364 GHz | 81.0 dB | 80 |
| 1.728 GHz | 77.0 dB | 53 |
| 2.372 GHz | 96.8 dB | 80 |

## Per-metric statistics across all MC trials

| Metric | Mean | Std | p05 | p50 | p95 |
|---|---|---|---|---|---|
| passband_il_db | 0.13 | 0.00 | 0.13 | 0.13 | 0.14 |
| passband_rl_db | 15.16 | 0.15 | 14.91 | 15.18 | 15.36 |
| rejection@GPS L1 protection | 32.86 | 1.31 | 30.63 | 32.88 | 35.02 |
| rejection@EU 2f0 (LTE B3 UL) | 69.83 | 5.57 | 64.69 | 68.17 | 80.51 |
| rejection@NA 2f0 (LTE B25 DL) | 56.21 | 0.52 | 55.38 | 56.20 | 57.12 |
| rejection@ISM 2.4G low (BLE/WiFi) | 78.71 | 1.87 | 75.23 | 78.54 | 82.01 |
| rejection@ISM 2.4G high | 66.90 | 0.90 | 65.52 | 66.84 | 68.43 |
| rejection@NA 3f0 | 57.13 | 0.27 | 56.69 | 57.11 | 57.58 |
