"""Digital + mixed-signal analysis.

- :mod:`.timing` — propagation / setup / hold checks for synchronous logic
- :mod:`.coupling` — digital-to-analog crosstalk + supply-noise injection
"""

from mcp_ltspice.digital.coupling import (
    DigitalAggressor,
    estimate_digital_to_analog_crosstalk,
    estimate_supply_noise_injection,
)
from mcp_ltspice.digital.timing import (
    SetupHoldResult,
    TimingPath,
    check_setup_hold,
    propagation_delay,
)

__all__ = [
    "DigitalAggressor",
    "SetupHoldResult",
    "TimingPath",
    "check_setup_hold",
    "estimate_digital_to_analog_crosstalk",
    "estimate_supply_noise_injection",
    "propagation_delay",
]
