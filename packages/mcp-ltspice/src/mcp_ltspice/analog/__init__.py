"""Analog active filter synthesis (op-amp based).

Closed-form designs for the most common 2nd-order topologies:

- :mod:`.sallen_key` — Sallen-Key (low-pass / high-pass / band-pass)
- :mod:`.mfb` — Multiple-Feedback (low-pass / high-pass / band-pass)
- :mod:`.cascade` — Higher-order filters via cascaded 2nd-order stages
"""

from mcp_ltspice.analog.cascade import (
    CascadeStage,
    cascaded_lpf_design,
    second_order_stages_for_order,
)
from mcp_ltspice.analog.mfb import (
    MFBDesign,
    mfb_band_pass,
    mfb_low_pass,
)
from mcp_ltspice.analog.sallen_key import (
    SallenKeyDesign,
    sallen_key_band_pass,
    sallen_key_high_pass,
    sallen_key_low_pass,
)

__all__ = [
    "CascadeStage",
    "MFBDesign",
    "SallenKeyDesign",
    "cascaded_lpf_design",
    "mfb_band_pass",
    "mfb_low_pass",
    "sallen_key_band_pass",
    "sallen_key_high_pass",
    "sallen_key_low_pass",
    "second_order_stages_for_order",
]
