"""EMC pre-compliance estimation tools.

- :mod:`.conducted` — conducted-emissions prediction via LISN model;
  CISPR / FCC limit lookup
- :mod:`.radiated` — radiated-emissions estimate (small-loop and short-
  dipole approximations) at 3m / 10m antenna distance
"""

from mcp_rf_analysis.emc.conducted import (
    LISNModel,
    cispr_limit_at,
    predict_conducted_emissions,
)
from mcp_rf_analysis.emc.radiated import (
    fcc_part15_radiated_limit_at,
    predict_radiated_emissions_loop,
)

__all__ = [
    "LISNModel",
    "cispr_limit_at",
    "fcc_part15_radiated_limit_at",
    "predict_conducted_emissions",
    "predict_radiated_emissions_loop",
]
