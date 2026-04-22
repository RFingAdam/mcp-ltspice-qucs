# Tool Catalog

Three servers, **40 tools** total. Frequencies are always Hz on the
wire; every tool returns the [Envelope](reference/envelope.md) shape.

## At a glance

=== "mcp-ltspice (11 tools)"

    | Tool | Purpose |
    |---|---|
    | `run_simulation` | Headless LTspice / ngspice batch run |
    | `extract_sparameters` | `.raw` → 2-port S-params → Touchstone |
    | `synthesize_lc_filter` | Butterworth / Chebyshev I / elliptic LPF |
    | `place_transmission_zero` | Move shunt-LC notch to a target frequency, snap to E24/E96 |
    | `find_transmission_zeros` | Peak-detect notches in S21 |
    | `substitute_real_components` | Vendor parasitic models (Coilcraft, Murata, Johanson, TDK) |
    | `evaluate_filter_spec` | Pass/fail per criterion with margin in dB |
    | `optimize_filter` | Nelder-Mead against spec, E24/E96 snap |
    | `monte_carlo_analysis` | joblib parallel yield analysis with Gaussian tolerance |
    | `stability_check` | Rollett K, Δ, Edwards-Sinsky μ across frequency |
    | `render_response` | matplotlib Bode PNG with frequency markers |

=== "mcp-rf-analysis (19 tools)"

    | Tool | Purpose |
    |---|---|
    | `cascade_networks` | Cascade two or more 2-port networks |
    | `deembed_network` | Strip left/right fixtures from a measurement |
    | `renormalize_impedance` | Change reference Z₀ |
    | `compute_stability` | K-factor / Δ / μ-factor across frequency |
    | `smith_chart_data` | Sᵢᵢ + normalised impedance for plotting |
    | `check_rejection_at` | Single-frequency rejection vs target |
    | `check_passband_compliance` | IL + RL across [f_start, f_stop] |
    | `evaluate_against_spec_template` | Bundled FCC / ETSI / 3GPP / coex templates |
    | `list_spec_templates_tool` | Discover bundled spec templates |
    | `list_lte_bands_tool` | LTE bands 1-71 (3GPP TS 36.101) |
    | `list_5gnr_bands_tool` | 5G NR FR1 + FR2 (3GPP TS 38.101) |
    | `list_gnss_bands_tool` | GPS / GLONASS / Galileo / BeiDou |
    | `list_ism_bands_tool` | ISM allocations by ITU region |
    | `list_halow_channels_tool` | 802.11ah HaLow channels per region |
    | `lookup_band_by_freq_tool` | All bands containing a frequency |
    | `list_fcc_restricted_bands_tool` | FCC §15.205 restricted bands |
    | `is_in_restricted_band_tool` | Check if a frequency is restricted |
    | `lookup_harmonic_victims` | Find victim bands for 2H/3H/4H/5H of a TX |
    | `check_coex_matrix` | Aggressor × victim matrix with predicted desense |
    | `compute_path_loss` | Friis / log-distance |
    | `compute_antenna_isolation_estimate` | Free-space + ground-plane penalty |
    | `compute_desense` | RX desense from filtered aggressor |
    | `compare_sparameters` | Element-wise diff between two .s2p files |
    | `extract_delay` | Group delay / unwrapped phase |
    | `fit_equivalent_circuit` | Fit a lumped equivalent to a measured 2-port |

=== "mcp-qucs-s (10 tools)"

    | Tool | Purpose |
    |---|---|
    | `status` | Report whether Qucs-S / Xyce are installed |
    | `synthesize_microstrip_line` | Hammerstad-Jensen W/L from Z₀, εr, h |
    | `analyze_microstrip_tool` | Z₀ / ε_eff / wavelength from W |
    | `synthesize_coupler` | Branch-line / rat-race / coupled-line / Lange |
    | `lumped_to_distributed` | Richards transform + Kuroda identities |
    | `run_sp_analysis` | Native Qucs-S S-parameter simulation |
    | `extract_noise_parameters` | F_min, Γ_opt, R_n |
    | `run_harmonic_balance` | Spectral content via Xyce backend |
    | `export_touchstone` | Sim + export `.s2p` in one call |

## Response envelope

Every tool returns:

```json
{
  "status": "ok | error",
  "data": { ... } | null,
  "warnings": [ "..." ],
  "metadata": { "tool_version": "0.1.0", "runtime_sec": 0.123 },
  "error": null | "human readable message"
}
```

Tools never raise to the MCP transport — they catch their own errors
and return `error()` envelopes so the agent can reason about the
failure. See [Envelope contract](reference/envelope.md) for the full
spec.

## See also

- [Touchstone interop](reference/touchstone.md) — the cross-server
  exchange format
- [E-series snap](reference/e-series.md) — preferred-number rounding for
  realizable component values
