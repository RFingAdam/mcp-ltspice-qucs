"""Signal-integrity analysis tools.

- :mod:`.tdr` — Time-Domain Reflectometry from S₁₁: impedance vs distance
- :mod:`.eye` — Eye diagram from S-parameters + PRBS source
- :mod:`.crosstalk` — NEXT / FEXT estimation between two coupled lines
"""

from mcp_rf_analysis.si.crosstalk import (
    estimate_fext_db,
    estimate_next_db,
)
from mcp_rf_analysis.si.eye import (
    EyeMetrics,
    eye_diagram_from_s2p,
)
from mcp_rf_analysis.si.tdr import tdr_from_s11

__all__ = [
    "EyeMetrics",
    "estimate_fext_db",
    "estimate_next_db",
    "eye_diagram_from_s2p",
    "tdr_from_s11",
]
