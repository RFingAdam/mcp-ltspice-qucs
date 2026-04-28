"""Filter synthesis: prototype → LC ladder → .asc schematic."""

from mcp_ltspice.synthesis.lc_filter import (
    FilterDesign,
    Topology,
    g_coefficients,
    lc_ladder,
    synthesize_lc_bpf,
    synthesize_lc_bsf,
    synthesize_lc_hpf,
    synthesize_lc_lpf,
)
from mcp_ltspice.synthesis.zeros import (
    place_transmission_zero,
    set_trap_frequency,
    trap_lc_for_freq,
)

__all__ = [
    "FilterDesign",
    "Topology",
    "g_coefficients",
    "lc_ladder",
    "place_transmission_zero",
    "set_trap_frequency",
    "synthesize_lc_bpf",
    "synthesize_lc_bsf",
    "synthesize_lc_hpf",
    "synthesize_lc_lpf",
    "trap_lc_for_freq",
]
