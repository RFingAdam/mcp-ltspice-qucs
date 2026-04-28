"""Power-supply design tools.

- :mod:`.ldo` — Linear regulator analysis (PSRR, ripple, dropout, efficiency)
- :mod:`.buck` — Buck (step-down) SMPS sizing (L, Cout, switching freq, ripple)
- :mod:`.boost` — Boost (step-up) SMPS sizing
- :mod:`.loop` — Control-loop bode + phase margin analysis
- :mod:`.emc` — Pre-compliance: Pi output filter, DM input filter,
  conducted-emissions prediction with CISPR limits, RC snubber, CM choke
  selection
"""

from mcp_ltspice.power.boost import BoostDesign, design_boost
from mcp_ltspice.power.buck import BuckDesign, design_buck
from mcp_ltspice.power.emc import (
    CmChoke,
    CmChokeRecommendation,
    ConductedEmissionsPrediction,
    DmInputFilterDesign,
    PiFilterDesign,
    RcSnubberDesign,
    design_cm_choke,
    design_dm_input_filter,
    design_pi_output_filter,
    design_rc_snubber,
    predict_conducted_emissions,
)
from mcp_ltspice.power.ldo import LDOAnalysis, analyze_ldo
from mcp_ltspice.power.loop import compute_phase_margin, type2_compensator

__all__ = [
    "BoostDesign",
    "BuckDesign",
    "CmChoke",
    "CmChokeRecommendation",
    "ConductedEmissionsPrediction",
    "DmInputFilterDesign",
    "LDOAnalysis",
    "PiFilterDesign",
    "RcSnubberDesign",
    "analyze_ldo",
    "compute_phase_margin",
    "design_boost",
    "design_buck",
    "design_cm_choke",
    "design_dm_input_filter",
    "design_pi_output_filter",
    "design_rc_snubber",
    "predict_conducted_emissions",
    "type2_compensator",
]
