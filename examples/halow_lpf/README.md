# Worldwide HaLow Elliptic LPF — turn-key design

A **canonical, end-to-end** filter design that demonstrates every relevant
tool in this MCP suite. Targets a **Murata Type 2HK** (LBAA0Z02HK,
+23 dBm) HaLow module so the same SKU works in every regulatory region:

| Region | Band | Max EIRP |
|--------|------|----------|
| EU (ETSI EN 300 220) | 863–870 MHz | +14 dBm |
| US (FCC Part 15.247) | 902–928 MHz | +36 dBm |
| Japan (ARIB STD-T108) | 916.5–927.5 MHz | +20 dBm |
| Korea | 917.5–923.5 MHz | +14 dBm |
| China (UHF white space) | 755–787 MHz¹ | +20 dBm |
| Singapore / India | 866–869 MHz | +14 dBm |
| Australia / New Zealand | 915–928 MHz | +30 dBm |

¹ China sub-band is below this filter's passband; firmware-controlled
modes select the appropriate filter chain.

The design covers all regions in a **single SKU** with passband
**863–928 MHz** so the same hardware ships everywhere except CN.

## What gets suppressed and why

| Harmonic | Frequency range | Primary victim | Filter target |
|----------|-----------------|----------------|---------------|
| 2H | 1726–1856 MHz | LTE B3 DL (1805–1880), B9 DL (1844–1880) | ≥ 50 dB at 1830 MHz |
| 3H | 2589–2784 MHz | LTE B7 DL (2620–2690), B41 (2496–2690), B38 (2570–2620) | ≥ 55 dB at 2640 MHz |
| GNSS skirt | 1175–1610 MHz | GPS L1, GLONASS L1, Galileo E1, BeiDou B1 | ≥ 30 dB |

Note: **3H does not directly land on 2.4 GHz Wi-Fi/BLE** — ISM ends at
2483.5 MHz, 3H starts at 2589 MHz. 2.4 GHz coexistence concerns are
sideband regrowth / intermod, not direct harmonic landings.

## How to run

```bash
uv run python examples/halow_lpf/design.py
```

The script emits the full deliverable bundle next to itself
(everything below is gitignored except for this README and `spec.json`):

```
halow_lpf.asc                       LTspice schematic
halow_lpf.s2p                       analytical S-parameters
halow_lpf.spice.s2p                 SPICE-extracted S-parameters
halow_lpf.analytical.s2p            analytical at SPICE freq grid
response.png                        S21/S11 Bode plot
halow_lpf.schematic.svg/.png        clean schemdraw rendering
compare_orders_report.md            order comparison (5/7/9)
report.md                           single-design final report
coex_report.md                      co-located radio desense matrix
spice_validation.md                 analytical-vs-SPICE Δ
halow_lpf_report.pdf                bundled deliverable
```

## Pipeline (each step uses an MCP tool)

1. **`lookup_harmonic_victims`** (`mcp-rf-analysis`) — confirms which
   bands the 2H/3H landings hit.
2. **`place_zeros_for_coex`** ⭐ NEW — computes optimal TZ frequencies
   weighted by victim severity. For HaLow worldwide it returns
   ≈ 1830 MHz (2H, LTE B3 centroid) and ≈ 2640 MHz (3H, LTE B7/B41
   centroid).
3. **`compare_filter_orders`** — runs the full synthesize → place zeros
   → vendor-snap → optimize → MC workflow for orders 5/7/9 and picks the
   most shippable.
4. **`synthesize_lc_lpf`** + **`place_transmission_zero`** — final
   prototype with TZs at the computed frequencies.
5. **`substitute_real_components(srf_margin=1.2, ...)`** ⭐ EXTENDED —
   real Coilcraft 0402HP + Murata GJM 0402 C0G with **automatic
   rejection** of any candidate whose SRF lands inside `1.2 ×
   max_spec_target_freq`.
6. **`optimize_filter`** — Nelder-Mead vendor-bounded optimization,
   E96-snapped.
7. **`validate_against_spice`** ⭐ NEW — runs ngspice on the substituted
   schematic, compares the SPICE-extracted S2P against analytical, and
   flags any frequency region where they disagree beyond the configured
   thresholds.
8. **`monte_carlo_analysis`** — 2000-trial MC at 2 % tolerance.
9. **`check_coex_matrix`** (`mcp-rf-analysis`) — desense matrix vs
   GNSS/LTE with 25 dB antenna isolation. The aggressor's filtered
   harmonic dBc values come from interpolating the actual filter S2P at
   each harmonic frequency.
10. **`evaluate_filter_spec`** — pass/fail with margins.
11. **`render_response`** + **`render_asc_as_schematic`** — Bode plot
    and clean schematic.
12. **`build_design_report_pdf`** — single shareable PDF with everything.

## Optional: real-vendor S-parameter files (issues #9, #10, #11)

Three new MCP tools let you drive the same pipeline with **measured
vendor S-parameters** instead of curated parasitic models:

```python
from mcp_ltspice.vendor_fetch import (
    fetch_coilcraft_s2p,
    fetch_murata_spice,
    register_user_vendor_dir,
)

# Fetch a Coilcraft S2P (cached at ~/.cache/mcp-ltspice/coilcraft/)
fetch_coilcraft_s2p(
    "0402DF-152XJL",
    source_url="https://www.coilcraft.com/getmedia/.../0402DF-152XJL.s2p",
)

# Fetch a Murata SPICE library (cached at ~/.cache/mcp-ltspice/murata/)
fetch_murata_spice(
    "GJM1555C1H1R0BB01",
    source_url="https://ds.murata.co.jp/.../GJM1555C1H1R0BB01.lib",
)

# Or index a directory of measured / third-party files
register_user_vendor_dir("~/lab/vendor_models/", namespace="user")
```

After registration, point `substitute_real_components` at the new
namespace and it uses the measured data instead of curated parasitic
estimates.

## What changed vs. the old example

The previous `examples/halow_lpf/` was incoherent — three competing
design flows (`design.py`, `design_v2.py`, `design_compare.py`) producing
contradictory "final" S2Ps, a vendor-bounded variant with **0% Monte
Carlo yield**, and documentation that recommended a different order than
the comparison report's actual winner. **None** of the `.s2p` files were
SPICE-validated.

The old files were moved to `examples/_archive/halow_lpf_v0/` (local
only — gitignored) for reference. The new pipeline:

- single canonical `design.py` (this directory)
- single coherent `spec.json`
- automatic transmission-zero placement via the new `place_zeros_for_coex` tool
- automatic SRF-aware vendor part rejection (no silent low-SRF snaps)
- **actual SPICE simulation** of the final substituted-vendor schematic,
  reconciled against the fast analytical preview
- canonical PDF deliverable suitable for an external review

## Spec

See `spec.json` for the machine-readable spec. Highlights:

```
Passband      : 863 – 928 MHz
IL            : ≤ 1.0 dB
RL            : ≥ 14 dB
Stopband 2H   : ≥ 50 dB at 1830 MHz (LTE B3 DL center)
Stopband 3H   : ≥ 55 dB at 2640 MHz (LTE B7 / B41 DL center)
GNSS protect  : ≥ 30 dB at 1575/1602 MHz (GPS L1, GLONASS L1OF)
Topology      : 5–9th order elliptic, T-network (series-first)
Components    : 0402 SMD; Coilcraft 0402HP + Murata GJM 0402 C0G
Z₀            : 50 Ω
```

## References

- IEEE 802.11ah-2016 / 802.11-2020 Annex E — HaLow channel allocations
- FCC Part 15.247 (US 902–928 MHz unlicensed)
- ETSI EN 300 220 (EU 863–870 MHz SRD)
- ARIB STD-T108 (JP 920 MHz)
- Pozar, *Microwave Engineering*, 4th ed., §8.6 — elliptic LPF synthesis
- Murata Type 2HK datasheet (LBAA0Z02HK)
