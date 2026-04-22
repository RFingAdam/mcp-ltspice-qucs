# mcp-rf-analysis

Simulator-agnostic MCP server. Consumes Touchstone files from any
source — LTspice, Qucs-S, CST, ADS, or VNA measurements — and exposes
RF domain knowledge that's the same regardless of where the S-parameters
came from.

Part of the [mcp-ltspice-qucs](../../README.md) suite.

## Tool catalog

### Network operations
- `cascade_networks`, `deembed_network`, `renormalize_impedance`,
  `compute_stability`, `smith_chart_data`

### Spec evaluation
- `check_rejection_at`, `check_passband_compliance`,
  `evaluate_against_spec_template`

### Regulatory + coex
- `list_lte_bands`, `list_5gnr_bands`, `list_gnss_bands`, `list_ism_bands`,
  `list_halow_channels`
- `lookup_harmonic_victims`, `check_coex_matrix`

### Link budget
- `compute_path_loss`, `compute_antenna_isolation_estimate`,
  `compute_desense`

### Touchstone utilities
- `compare_sparameters`, `extract_delay`, `fit_equivalent_circuit`

## Bundled databases

`resources/bands/` — LTE, 5G NR, GNSS, ISM, HaLow band allocations.
Sources cited in each JSON file (3GPP TS 36.101, 38.101, IEEE 802.11ah,
FCC Part 15, ETSI EN 300 220 / 300 328).

`resources/limits/` — FCC, ETSI, 3GPP regulatory limit tables.

`resources/templates/` — Reusable spec sheets for common pass/fail
evaluations.
