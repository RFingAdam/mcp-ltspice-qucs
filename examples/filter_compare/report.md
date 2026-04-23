# Filter order comparison

Generic 5/7/9-order elliptic LPF comparison driven by the
`compare_filter_orders` tool. Spec at `spec.json`.

**Winner: order 5** — all criteria pass | yield 100% (excellent) | SRF: 5 components flagged (critical) | 7 components

## Score table

| Order | Components | Spec | MC yield (2% tol) | SRF | Score |
|---|---|---|---|---|---|
| **5 ← shippable** | 7 | pass | 100.0% | critical | 186 |
| **7** | 10 | fail | 0.0% | critical | 5 |
| **9** | 13 | fail | 0.2% | critical | -1 |

## Order 5

- **Components:** 7
- **Traps used:** 2
- **Spec:** pass
- **MC yield (2% tol):** 100.0%
- **SRF severity:** critical (5 flagged)
- **Most-influential component:** L2
- **Touchstone:** `order5_final.s2p`

**BOM:**

| Refdes | Value |
|---|---|
| C2 | 3.9 pF |
| C4 | 3.9 pF |
| L1 | 10 nH |
| L2 | 2.2 nH |
| L3 | 18 nH |
| L4 | 2.2 nH |
| L5 | 8.2 nH |

**Spec compliance:**

| Criterion | Target | Measured | Margin | Status |
|---|---|---|---|---|
| Passband IL | 1.0 dB | +0.22 dB | +0.78 dB | ✅ |
| Passband RL | 10.0 dB | +12.99 dB | +2.99 dB | ✅ |
| stopband 1 | 40.0 dB | +63.77 dB | +23.77 dB | ✅ |
| stopband 2 | 45.0 dB | +57.92 dB | +12.92 dB | ✅ |
| stopband 3 | 35.0 dB | +48.58 dB | +13.58 dB | ✅ |
| deep stopband | 30.0 dB | +45.93 dB | +15.93 dB | ✅ |

---

## Order 7

- **Components:** 10
- **Traps used:** 3
- **Spec:** fail
- **MC yield (2% tol):** 0.0%
- **SRF severity:** critical (9 flagged)
- **Most-influential component:** L6
- **Touchstone:** `order7_final.s2p`

**BOM:**

| Refdes | Value |
|---|---|
| C2 | 2.2 pF |
| C4 | 2.2 pF |
| C6 | 2.7 pF |
| L1 | 5.6 nH |
| L2 | 12 nH |
| L3 | 12 nH |
| L4 | 5.6 nH |
| L5 | 10 nH |
| L6 | 3.3 nH |
| L7 | 15 nH |

**Spec compliance:**

| Criterion | Target | Measured | Margin | Status |
|---|---|---|---|---|
| Passband IL | 1.0 dB | +0.50 dB | +0.50 dB | ✅ |
| Passband RL | 10.0 dB | +9.65 dB | -0.35 dB | ❌ |
| stopband 1 | 40.0 dB | +62.30 dB | +22.30 dB | ✅ |
| stopband 2 | 45.0 dB | +50.31 dB | +5.31 dB | ✅ |
| stopband 3 | 35.0 dB | +45.16 dB | +10.16 dB | ✅ |
| deep stopband | 30.0 dB | +44.20 dB | +14.20 dB | ✅ |

---

## Order 9

- **Components:** 13
- **Traps used:** 4
- **Spec:** fail
- **MC yield (2% tol):** 0.2%
- **SRF severity:** critical (11 flagged)
- **Most-influential component:** L6
- **Touchstone:** `order9_final.s2p`

**BOM:**

| Refdes | Value |
|---|---|
| C2 | 3.9 pF |
| C4 | 3.9 pF |
| C6 | 8.2 pF |
| C8 | 4.7 pF |
| L1 | 4.7 nH |
| L2 | 4.7 nH |
| L3 | 12 nH |
| L4 | 8.2 nH |
| L5 | 10 nH |
| L6 | 1.5 nH |
| L7 | 12 nH |
| L8 | 5.6 nH |
| L9 | 3.3 nH |

**Spec compliance:**

| Criterion | Target | Measured | Margin | Status |
|---|---|---|---|---|
| Passband IL | 1.0 dB | +0.88 dB | +0.12 dB | ✅ |
| Passband RL | 10.0 dB | +7.39 dB | -2.61 dB | ❌ |
| stopband 1 | 40.0 dB | +67.30 dB | +27.30 dB | ✅ |
| stopband 2 | 45.0 dB | +57.05 dB | +12.05 dB | ✅ |
| stopband 3 | 35.0 dB | +54.03 dB | +19.03 dB | ✅ |
| deep stopband | 30.0 dB | +53.47 dB | +23.47 dB | ✅ |

---

