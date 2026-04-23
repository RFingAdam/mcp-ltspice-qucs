# EMC pre-compliance check

First-order conducted + radiated emissions estimate using the
`mcp-rf-analysis` EMC tools.

## Conducted emissions vs CISPR 22 Class B (with 6 dB margin)

**Overall: FAIL** (29 of 29 points fail)

| Freq (MHz) | Measured (dBµV) | Limit (dBµV) | Margin (dB) | Status |
|---|---|---|---|---|
| 1.00 | 133.9 | 56.0 | -83.9 | ❌ FAIL |
| 2.00 | 127.9 | 56.0 | -77.9 | ❌ FAIL |
| 3.00 | 124.4 | 56.0 | -74.4 | ❌ FAIL |
| 4.00 | 121.9 | 56.0 | -71.9 | ❌ FAIL |
| 5.00 | 120.0 | 56.0 | -70.0 | ❌ FAIL |
| 6.00 | 118.4 | 56.4 | -68.0 | ❌ FAIL |
| 7.00 | 117.1 | 56.8 | -66.3 | ❌ FAIL |
| 8.00 | 115.9 | 57.0 | -64.9 | ❌ FAIL |
| 9.00 | 114.9 | 57.3 | -63.6 | ❌ FAIL |
| 10.00 | 114.0 | 57.5 | -62.4 | ❌ FAIL |
| 11.00 | 113.2 | 57.8 | -61.4 | ❌ FAIL |
| 12.00 | 112.4 | 58.0 | -60.4 | ❌ FAIL |
| 13.00 | 111.7 | 58.1 | -59.6 | ❌ FAIL |
| 14.00 | 111.1 | 58.3 | -58.8 | ❌ FAIL |
| 15.00 | 110.5 | 58.5 | -58.0 | ❌ FAIL |
| 16.00 | 109.9 | 58.6 | -57.3 | ❌ FAIL |
| 17.00 | 109.4 | 58.7 | -56.6 | ❌ FAIL |
| 18.00 | 108.9 | 58.9 | -56.0 | ❌ FAIL |
| 19.00 | 108.4 | 59.0 | -55.4 | ❌ FAIL |
| 20.00 | 108.0 | 59.1 | -54.9 | ❌ FAIL |
| 21.00 | 107.5 | 59.2 | -54.3 | ❌ FAIL |
| 22.00 | 107.1 | 59.3 | -53.8 | ❌ FAIL |
| 23.00 | 106.7 | 59.4 | -53.3 | ❌ FAIL |
| 24.00 | 106.4 | 59.5 | -52.9 | ❌ FAIL |
| 25.00 | 106.0 | 59.6 | -52.4 | ❌ FAIL |
| 26.00 | 105.7 | 59.7 | -52.0 | ❌ FAIL |
| 27.00 | 105.4 | 59.8 | -51.6 | ❌ FAIL |
| 28.00 | 105.0 | 59.8 | -51.2 | ❌ FAIL |
| 29.00 | 104.7 | 59.9 | -50.8 | ❌ FAIL |

> Conducted emissions assume a 100 mA SMPS fundamental at 1 MHz
> with 1/n harmonic rolloff. Replace `spectrum` in design.py
> with your real switching-current spectrum from LTspice.
