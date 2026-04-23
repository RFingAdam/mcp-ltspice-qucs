# Buck SMPS: 5.0V → 3.3V at 2.0A

Switching frequency: **1.0 MHz**

## Power-stage components

- Duty cycle: **66.0%**
- Inductor: **1.87 µH** (peak 2.30 A, RMS 2.01 A)
- Output capacitance: **3.7 µF**
- Output cap ESR limit: **33.3 mΩ**

## High-side switch

**CSD17552Q5A** (TI)

- Vds max: 30.0 V
- Id continuous: 60.0 A
- Rds_on: 7.5 mΩ
- Qg total: 14.0 nC
- Vgs threshold: 1.6 V
- Package: VSON-8

## Loop compensator (Type-II)

- Crossover: **100.0 kHz** (≈ fsw/10)
- Plant pole: 25722 Hz
- ESR zero: 2546.5 kHz

Compensator R/C:

| Component | Value |
|---|---|
| R_fb | 10.00 kΩ |
| C_z | 0.59 nF |
| C_p | 0.05 nF |
