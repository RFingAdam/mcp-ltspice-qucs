# Tool Catalog

<!-- BEGIN GENERATED -->
Three servers, **110 tools** total. Frequencies are always Hz on the
wire; every tool returns the [Envelope](reference/envelope.md) shape.
`mcp-ltspice` additionally registers namespaced aliases (`filter.*`, `power.*`, `analog.*`, `digital.*`, `vendor.*`, `sim.*`) for every primary tool; only primaries are listed here.

## At a glance

*(This section is generated — run `uv run python scripts/gen_tool_catalog.py`
after adding or changing tools.)*

=== "mcp-ltspice (59 tools)"

    | Tool | Purpose |
    |---|---|
    | `analyze_ldo` | Analyze an LDO at one operating point: efficiency, dropout, dissipation, output ripple. |
    | `build_design_report_pdf` | Bundle a design directory's artifacts (schematics, response plots, and report.md) into a single shareable PDF. |
    | `cascaded_lpf_design` | Cascaded nth-order Butterworth or Bessel LPF as 2nd-order stages (Sallen-Key). |
    | `check_setup_hold` | Setup/hold timing check on a synchronous digital path. |
    | `compare_filter_orders` | Run the synthesize -> place zeros -> vendor-snap -> optimize -> MC yield workflow for several filter orders side-by-side and return the most shippable. |
    | `compute_phase_margin` | Compute crossover freq + phase margin from open-loop Bode arrays. |
    | `corner_analysis` | Evaluate a filter spec at named corners (e.g. |
    | `design_boost` | Size a Boost (step-up) SMPS: L, Cout, ESR limit, peak/RMS currents. |
    | `design_buck` | Size a Buck (step-down) SMPS: L, Cout, ESR limit, peak/RMS currents. |
    | `design_cm_choke` | Pick a common-mode choke from a curated catalogue (Würth WE-CMB, TDK ZJYS / ACT, Murata DLW). |
    | `design_dm_input_filter` | Size a 2nd-order LC differential-mode input EMI filter for conducted-emissions compliance, with the Middlebrook stability check (|Z_out_filter| < |Z_in_conve… |
    | `design_pi_output_filter` | Size a Pi-section LC output filter (C-L-C) for additional SMPS ripple attenuation downstream of the converter's built-in Cout. |
    | `design_rc_snubber` | Design an RC snubber that damps switch-node ringing. |
    | `estimate_digital_to_analog_crosstalk` | Estimate digital-to-analog crosstalk via mutual capacitance. |
    | `estimate_supply_noise_injection` | Estimate supply-rail droop from digital switching activity. |
    | `evaluate_filter_spec_tool` | Evaluate a Touchstone .s2p file against a coex-aware spec. |
    | `extract_sparameters` | Parse a SPICE .raw AC analysis output and write 2-port S-parameters to a Touchstone .s2p file. |
    | `find_mosfet_for_application` | Filter the MOSFET catalog by polarity, Vds, Id, Rds_on, Vgs threshold. |
    | `find_opamp_for_application` | Filter the op-amp catalog by spec constraints (GBW, noise, offset, supply, RRIO flags, family) and return ranked candidates. |
    | `find_transmission_zeros` | Locate notches (transmission zeros) in S21 by peak detection. |
    | `list_bjts` | List all BJT part numbers. |
    | `list_diodes` | List all diode part numbers (signal / Schottky / TVS / zener / ESD). |
    | `list_mosfets` | List all MOSFET part numbers in the bundled catalog. |
    | `list_opamps` | List all op-amp part numbers in the bundled catalog. |
    | `list_references` | List all voltage reference part numbers. |
    | `list_vendor_parts` | List the value catalogue for a vendor part series. |
    | `lookup_bjt` | Look up a BJT by part number. |
    | `lookup_diode` | Look up a diode by part number. |
    | `lookup_mosfet` | Look up a MOSFET by part number. |
    | `lookup_opamp` | Look up an op-amp by part number (returns full datasheet params). |
    | `lookup_reference` | Look up a voltage reference by part number. |
    | `mfb_band_pass` | Synthesize a Multiple-Feedback (MFB) 2nd-order BPF. |
    | `mfb_low_pass` | Synthesize a Multiple-Feedback (MFB) 2nd-order LPF. |
    | `monte_carlo_analysis` | Run Monte Carlo trials with Gaussian-distributed component tolerances. |
    | `optimize_filter` | Iteratively tune component values against a spec via Nelder-Mead. |
    | `parameter_sweep` | Sweep one or more component values across a Cartesian product grid and report per-point spec margins + overall yield. |
    | `place_transmission_zero` | Move the transmission zero of a shunt LC trap to a target frequency, preserving (by default) the L/C ratio for impedance match. |
    | `predict_conducted_emissions` | Predict conducted-emission spectrum at the LISN port for an SMPS and compare to CISPR 22 / 32 limits (Class A / B, QP / AVG detector). |
    | `propagation_delay` | Estimate combinational propagation delay (gates + wires + fanout). |
    | `register_user_vendor_dir` | Index a directory of user-supplied vendor models (.s2p / .lib) so they appear as substitution candidates under a namespace. |
    | `render_asc_as_schematic` | Parse an existing LTspice .asc and re-render it as a clean schemdraw SVG/PNG (the .asc only renders inside LTspice). |
    | `render_lc_ladder_schematic` | Render a clean publication-quality schematic of an LC ladder filter. |
    | `render_response` | Render an S₂ₚ Bode plot as PNG. |
    | `required_psrr_for_ripple` | Compute the PSRR (dB) an LDO needs to meet an output-ripple target. |
    | `run_simulation` | Run an LTspice / ngspice simulation headlessly. |
    | `sallen_key_band_pass` | Synthesize a Sallen-Key 2nd-order BPF (single op-amp). |
    | `sallen_key_high_pass` | Synthesize a Sallen-Key 2nd-order HPF. |
    | `sallen_key_low_pass` | Synthesize a Sallen-Key 2nd-order LPF (op-amp + 2R + 2C). |
    | `sensitivity_analysis` | Perturb each component by +/-pct and report the dB/% sensitivity of every spec criterion. |
    | `srf_audit` | Flag inductors / capacitors whose self-resonant frequency is within margin_pct of the highest spec target. |
    | `stability_check` | Compute Rollett K-factor, |Δ|, and Edwards-Sinsky μ-factor across frequency for a 2-port network. |
    | `substitute_real_components` | Replace ideal L/C values with vendor parts. |
    | `synthesize_for_coex_target` | Closed-loop coex-driven synthesis: iterate elliptic LPF order until the coexistence matrix meets a desense target. |
    | `synthesize_lc_bpf_filter` | Synthesize a band-pass LC ladder via the LPF→BPF frequency transformation (Pozar §8.5). |
    | `synthesize_lc_bsf_filter` | Synthesize a band-stop LC ladder via the LPF→BSF frequency transformation (Pozar §8.5). |
    | `synthesize_lc_filter` | Synthesize an LC ladder LPF (Butterworth / Chebyshev I / elliptic), write the .asc schematic, and return the component values plus a Touchstone .s2p of the a… |
    | `synthesize_lc_hpf_filter` | Synthesize a high-pass LC ladder via the LPF→HPF frequency transformation (Pozar §8.5). |
    | `type2_compensator` | Type-II compensator design (1 zero + 1 pole) for current-mode SMPS loops. |
    | `validate_against_spice` | Run a real SPICE simulation on a schematic and reconcile it against the closed-form analytical S2P for the same components. |

=== "mcp-qucs-s (17 tools)"

    | Tool | Purpose |
    |---|---|
    | `analyze_microstrip_tool` | Analyze an existing microstrip line: Z0, eps_eff, wavelength. |
    | `export_touchstone` | Run Qucs-S sim and export S-parameters to Touchstone in one call. |
    | `extract_noise_parameters` | Run a Qucs-S noise analysis and return the four classical noise parameters per frequency: NF50 (dB), Fmin (dB), Gamma_opt (magnitude and angle) and Rn (ohms). |
    | `list_substrate_presets_tool` | List curated substrate presets (FR4, Rogers RO4350B / RO4003C, Duroid 5880 / 6002, PTFE, Isola FR408HR, Taconic TLY5) with their {er, h_mm, t_um, tan_d} values. |
    | `lumped_to_distributed` | Convert a lumped LC ladder to its distributed-element microstrip equivalent via Richards transformation + Kuroda identities. |
    | `run_harmonic_balance` | Run harmonic-balance analysis via the Xyce backend. |
    | `run_sp_analysis` | Run native Qucs-S S-parameter analysis on a qucsator netlist (generate one with simulate_lc_ladder, or hand-write it; this is the netlist format, not the GUI… |
    | `simulate_lc_ladder` | Design-to-Touchstone in one call: build a Qucs netlist for a lumped LC ladder, simulate it with qucsator, and write S-parameters as a .s2p file. |
    | `status` | Report whether Qucs-S and Xyce are installed and discoverable. |
    | `sweep_compression_point` | Sweep drive level through harmonic balance and locate the 1 dB gain-compression point (P1dB). |
    | `synthesize_combline_bpf` | Synthesize a combline microstrip BPF from LPF prototype g-coefficients: N coupled lines shorted at the same end, each tuned by a lumped capacitor at the open… |
    | `synthesize_coupled_line_bpf` | Synthesize an edge-coupled (parallel coupled-line) microstrip BPF (Pozar §8.7) from LPF prototype g-coefficients (pass the g_coefficients list a lumped synth… |
    | `synthesize_coupler` | Synthesize a directional coupler: branch_line / rat_race / coupled_line / lange. |
    | `synthesize_hairpin_bpf` | Synthesize a hairpin microstrip BPF (folded edge-coupled / Cristal-Frankel hairpin-line) from LPF prototype g-coefficients. |
    | `synthesize_interdigital_bpf` | Synthesize an interdigital microstrip BPF from LPF prototype g-coefficients: N coupled λ/4 resonators, alternately shorted, tapped I/O. |
    | `synthesize_microstrip_line` | Synthesize microstrip line dimensions for a target characteristic impedance and electrical length. |
    | `synthesize_stepped_impedance_lpf` | Synthesize a stepped-impedance microstrip LPF (Pozar §8.6) from a lumped LPF ladder: series inductors become short high-Z sections (βl = ω_c·L/Z_h), shunt ca… |

=== "mcp-rf-analysis (34 tools)"

    | Tool | Purpose |
    |---|---|
    | `cascade_networks` | Cascade two or more 2-port networks (left-to-right). |
    | `check_coex_matrix` | Compute the multi-radio coex aggressor × victim matrix with predicted desense. |
    | `check_passband_compliance` | Check passband insertion loss + return loss across [f_start, f_stop]. |
    | `check_rejection_at` | Check |S21| at a single frequency against a min-rejection target. |
    | `cispr_limit_at` | Conducted-emissions limit (dBuV) at a frequency for CISPR 22 / FCC 15B. |
    | `compare_sparameters` | Element-wise diff between two .s2p files (S21 dB / S11 dB / mag / phase). |
    | `compute_antenna_isolation_estimate` | Estimate antenna-to-antenna isolation in dB. |
    | `compute_desense` | Predict RX desense from an aggressor TX with filter and antenna isolation. |
    | `compute_path_loss` | Compute Friis or log-distance path loss in dB. |
    | `compute_stability` | Compute Rollett K-factor + |Δ| + μ-factor across frequency. |
    | `deembed_network` | De-embed left/right fixtures from a measured 2-port network. |
    | `estimate_fext_db` | Far-End Crosstalk (FEXT) estimate for two coupled traces. |
    | `estimate_next_db` | Near-End Crosstalk (NEXT) estimate for two coupled traces. |
    | `evaluate_against_spec_template` | Evaluate a .s2p against a bundled spec template (FCC / ETSI / 3GPP). |
    | `extract_delay` | Compute group delay (or unwrapped phase) of S21. |
    | `eye_diagram_from_s2p` | Compute eye-diagram metrics for a channel given its S2P. |
    | `fcc_part15_radiated_limit_at` | FCC Part 15.109 Class B radiated-emissions limit (dBuV/m) at distance. |
    | `fit_equivalent_circuit` | Fit a lumped equivalent circuit to a measured 2-port network. |
    | `is_in_restricted_band_tool` | Check whether a frequency falls into an FCC restricted band. |
    | `list_5gnr_bands_tool` | List 5G NR bands. |
    | `list_fcc_restricted_bands_tool` | List FCC §15.205 restricted bands. |
    | `list_gnss_bands_tool` | List GNSS signals (GPS / GLONASS / Galileo / BeiDou). |
    | `list_halow_channels_tool` | List 802.11ah HaLow channels for a region (US, EU, JP, KR, CN, SG, AU_NZ, IN). |
    | `list_ism_bands_tool` | List ISM band allocations. |
    | `list_lte_bands_tool` | List LTE bands. |
    | `list_spec_templates_tool` | List names of bundled spec templates. |
    | `lookup_band_by_freq_tool` | Find every band/system that contains a given frequency. |
    | `lookup_harmonic_victims` | For a TX center frequency, find which RX bands its 2nd / 3rd / etc. |
    | `place_zeros_for_coex` | Compute optimal elliptic-filter transmission-zero frequencies for coexistence: for each harmonic landing [n·f_lo, n·f_hi], the zero goes at the severity-weig… |
    | `predict_conducted_emissions` | Convert an AC line-current spectrum to LISN voltage and check against CISPR 22 / FCC 15B conducted-emissions limits. |
    | `predict_radiated_emissions_loop` | Estimate radiated E-field from a current-carrying loop (small-loop approximation). |
    | `renormalize_impedance` | Renormalize an S-parameter file to a new reference impedance. |
    | `smith_chart_data` | Return Smith chart data (S_ii real/imag + normalized impedance) for plotting. |
    | `tdr_from_s11` | Time-Domain Reflectometry from S11. |

<!-- END GENERATED -->

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
