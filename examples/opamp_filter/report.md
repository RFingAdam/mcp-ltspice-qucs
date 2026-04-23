# Anti-aliasing LPF for 96 kSPS audio ADC

4th-order Butterworth low-pass filter, fc = 22 kHz, cascaded Sallen-Key.

- **Order:** 4
- **Topology:** sallen_key
- **Stages:** 2 (2 op-amps required)
- **Min op-amp GBW:** 2.87 MHz
- **Stopband rejection at Nyquist (48 kHz):** -27.1 dB

## Per-stage components

| Stage | fc | Q | R1 | R2 | C1 | C2 |
|---|---|---|---|---|---|---|
| 1 | 22.00 kHz | 0.541 | 13367 Ω | 3915 Ω | 1.0 nF | 1.0 nF |
| 2 | 22.00 kHz | 1.307 | 5537 Ω | 9452 Ω | 1.0 nF | 1.0 nF |

## Op-amp choice

**THS3491** (TI) — high-speed current-feedback ADC driver

- Family: BIPOLAR
- GBW: 900.0 MHz (need > 2.9)
- Slew rate: 8000.0 V/µs
- Input noise: 2.0 nV/√Hz
- Input offset: 1000 µV
- Supply: 9.0-33.0 V
- RRIO: in=False, out=False
