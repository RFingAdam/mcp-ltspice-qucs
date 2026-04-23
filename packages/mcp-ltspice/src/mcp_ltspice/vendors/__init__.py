"""Curated vendor model bundles for active components.

Mirrors the pattern used in :mod:`mcp_ltspice.vendor_models` for
passives, but applied to active devices (op-amps, MOSFETs, BJTs,
diodes, voltage references). Each model captures the parameters that
matter for first-order design choices — full SPICE subcircuits for
the actual simulator are referenced via vendor part number.

- :mod:`.opamps` — small-signal op-amps (TI OPA / LM, ADI ADA / OPA)
- :mod:`.mosfets` — power N-FETs and P-FETs (ON Semi NTM / NTR, Vishay)
- :mod:`.bjts` — small-signal BJTs (Diodes 2N3904 / 2N3906, ON BC547)
- :mod:`.diodes` — schottky / signal / TVS / zener
- :mod:`.references` — voltage references (TI REF, ADI ADR)
"""

from mcp_ltspice.vendors.bjts import BJTModel, list_bjts, lookup_bjt
from mcp_ltspice.vendors.diodes import DiodeModel, list_diodes, lookup_diode
from mcp_ltspice.vendors.mosfets import (
    MOSFETModel,
    find_mosfet_for_application,
    list_mosfets,
    lookup_mosfet,
)
from mcp_ltspice.vendors.opamps import (
    OpAmpModel,
    find_opamp_for_application,
    list_opamps,
    lookup_opamp,
)
from mcp_ltspice.vendors.references import (
    VoltageReferenceModel,
    list_references,
    lookup_reference,
)

__all__ = [
    "BJTModel",
    "DiodeModel",
    "MOSFETModel",
    "OpAmpModel",
    "VoltageReferenceModel",
    "find_mosfet_for_application",
    "find_opamp_for_application",
    "list_bjts",
    "list_diodes",
    "list_mosfets",
    "list_opamps",
    "list_references",
    "lookup_bjt",
    "lookup_diode",
    "lookup_mosfet",
    "lookup_opamp",
    "lookup_reference",
]
