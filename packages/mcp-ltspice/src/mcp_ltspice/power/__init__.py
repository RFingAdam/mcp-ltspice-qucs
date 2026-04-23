"""Power-supply design tools.

- :mod:`.ldo` — Linear regulator analysis (PSRR, ripple, dropout, efficiency)
- :mod:`.buck` — Buck (step-down) SMPS sizing (L, Cout, switching freq, ripple)
- :mod:`.boost` — Boost (step-up) SMPS sizing
- :mod:`.loop` — Control-loop bode + phase margin analysis
"""

from mcp_ltspice.power.boost import BoostDesign, design_boost
from mcp_ltspice.power.buck import BuckDesign, design_buck
from mcp_ltspice.power.ldo import LDOAnalysis, analyze_ldo
from mcp_ltspice.power.loop import compute_phase_margin, type2_compensator

__all__ = [
    "BoostDesign",
    "BuckDesign",
    "LDOAnalysis",
    "analyze_ldo",
    "compute_phase_margin",
    "design_boost",
    "design_buck",
    "type2_compensator",
]
