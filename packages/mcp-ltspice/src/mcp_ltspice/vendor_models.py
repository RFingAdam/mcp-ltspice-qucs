"""Curated parasitic models for representative passive vendor parts.

Each vendor entry lists a series, a value table, and the parasitic R/L/C
that should accompany the ideal element when substituted. Values are
typical for the part series datasheet; users can override or extend
this table with their own measurements.

This is **not** a substitute for the vendor's real SPICE subcircuit when
high accuracy is required. It provides a first-order parasitic estimate
(SRF, Q, ESR) so synthesis sims include realistic loss behavior.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal


@dataclass
class ParasiticInductor:
    """Inductor with shunt parasitic capacitance (sets SRF) and series ESR."""

    L_h: float
    Cp_f: float  # parasitic shunt capacitance — sets SRF
    Rs_ohm: float  # series ESR (DC + AC)
    srf_hz: float


@dataclass
class ParasiticCapacitor:
    """Capacitor with series parasitic inductance (ESL → SRF) and ESR."""

    C_f: float
    Ls_h: float  # series ESL — sets SRF
    Rs_ohm: float
    srf_hz: float


def _srf_from_lc(l_h: float, c_f: float) -> float:
    return 1.0 / (2.0 * math.pi * math.sqrt(l_h * c_f))


# -------- Coilcraft 0402HP series (high-Q wirewound, 0402, RF) ----------
# Values chosen to match the datasheet's typical SRF curves.
COILCRAFT_0402HP: dict[float, ParasiticInductor] = {}
for L_nh, Cp_pf, Rs_ohm, srf_ghz in [
    (1.0, 0.18, 0.04, 11.8),
    (1.5, 0.20, 0.06, 9.0),
    (2.2, 0.22, 0.08, 7.0),
    (3.3, 0.24, 0.10, 5.5),
    (4.7, 0.26, 0.13, 4.5),
    (5.6, 0.28, 0.16, 4.0),
    (6.8, 0.30, 0.20, 3.5),
    (8.2, 0.32, 0.25, 3.1),
    (10.0, 0.36, 0.30, 2.6),
    (12.0, 0.38, 0.35, 2.3),
    (15.0, 0.42, 0.42, 2.0),
    (18.0, 0.46, 0.50, 1.7),
    (22.0, 0.50, 0.60, 1.5),
]:
    L = L_nh * 1e-9
    Cp = Cp_pf * 1e-12
    COILCRAFT_0402HP[L] = ParasiticInductor(
        L_h=L,
        Cp_f=Cp,
        Rs_ohm=Rs_ohm,
        srf_hz=srf_ghz * 1e9,
    )

# -------- Coilcraft 0603CS series (lower frequency, higher inductance) ---
COILCRAFT_0603CS: dict[float, ParasiticInductor] = {}
for L_nh, Cp_pf, Rs_ohm, srf_ghz in [
    (10.0, 0.40, 0.15, 2.4),
    (22.0, 0.55, 0.30, 1.6),
    (47.0, 0.75, 0.55, 1.0),
    (100.0, 1.00, 1.00, 0.65),
    (220.0, 1.50, 1.80, 0.40),
]:
    L = L_nh * 1e-9
    Cp = Cp_pf * 1e-12
    COILCRAFT_0603CS[L] = ParasiticInductor(
        L_h=L,
        Cp_f=Cp,
        Rs_ohm=Rs_ohm,
        srf_hz=srf_ghz * 1e9,
    )


# -------- Murata GJM 0402 C0G/NP0 series (low-loss MLCC) -------
MURATA_GJM_C0G: dict[float, ParasiticCapacitor] = {}
for C_pf, Ls_nh, Rs_ohm in [
    (0.5, 0.5, 0.10),
    (0.8, 0.5, 0.10),
    (1.0, 0.5, 0.10),
    (1.5, 0.5, 0.10),
    (1.8, 0.5, 0.10),
    (2.2, 0.5, 0.10),
    (2.7, 0.55, 0.10),
    (3.3, 0.55, 0.10),
    (3.9, 0.55, 0.10),
    (4.7, 0.55, 0.10),
    (5.6, 0.60, 0.10),
    (6.8, 0.60, 0.10),
    (8.2, 0.60, 0.10),
    (10.0, 0.60, 0.10),
    (12.0, 0.65, 0.10),
    (15.0, 0.65, 0.10),
    (18.0, 0.65, 0.10),
    (22.0, 0.70, 0.10),
]:
    C = C_pf * 1e-12
    Ls = Ls_nh * 1e-9
    MURATA_GJM_C0G[C] = ParasiticCapacitor(
        C_f=C,
        Ls_h=Ls,
        Rs_ohm=Rs_ohm,
        srf_hz=_srf_from_lc(Ls, C),
    )


# -------- Johanson L-series (similar to Coilcraft 0402HP) -----------
JOHANSON_L: dict[float, ParasiticInductor] = COILCRAFT_0402HP.copy()


# -------- TDK MLG / MLK 0402 (similar to Coilcraft 0402HP) ---------
TDK_MLG: dict[float, ParasiticInductor] = COILCRAFT_0402HP.copy()


VendorName = Literal[
    "coilcraft_0402hp", "coilcraft_0603cs", "murata_gjm_c0g", "johanson_l", "tdk_mlg"
]


_VENDOR_TABLES: dict[str, dict[float, object]] = {
    "coilcraft_0402hp": COILCRAFT_0402HP,  # type: ignore[dict-item]
    "coilcraft_0603cs": COILCRAFT_0603CS,  # type: ignore[dict-item]
    "murata_gjm_c0g": MURATA_GJM_C0G,  # type: ignore[dict-item]
    "johanson_l": JOHANSON_L,  # type: ignore[dict-item]
    "tdk_mlg": TDK_MLG,  # type: ignore[dict-item]
}


def list_vendor_parts(vendor: str) -> list[float]:
    """Return the value list (in farads or henrys) available for a vendor."""
    if vendor not in _VENDOR_TABLES:
        raise ValueError(f"Unknown vendor: {vendor}")
    return sorted(_VENDOR_TABLES[vendor].keys())


def lookup_part(
    vendor: str, value: float, *, kind: Literal["L", "C"]
) -> ParasiticInductor | ParasiticCapacitor:
    """Find the closest available part to ``value`` and return its parasitic data.

    Raises ``ValueError`` if the vendor doesn't carry components of the
    requested kind.
    """
    if vendor not in _VENDOR_TABLES:
        raise ValueError(f"Unknown vendor: {vendor}")
    table = _VENDOR_TABLES[vendor]
    sample = next(iter(table.values()))
    if kind == "L" and not isinstance(sample, ParasiticInductor):
        raise ValueError(f"Vendor {vendor} does not carry inductors")
    if kind == "C" and not isinstance(sample, ParasiticCapacitor):
        raise ValueError(f"Vendor {vendor} does not carry capacitors")

    keys = sorted(table.keys())
    nearest = min(keys, key=lambda k: abs(k - value))
    return table[nearest]  # type: ignore[return-value]


def substitute_real_components(
    components: dict[str, float],
    inductor_vendor: str = "coilcraft_0402hp",
    capacitor_vendor: str = "murata_gjm_c0g",
) -> dict[str, dict[str, float]]:
    """Return a mapping of refdes → {ideal_value, snapped_value, Cp/Ls,
    Rs, SRF} describing the realized vendor part for each ideal component.
    """
    out: dict[str, dict[str, float]] = {}
    for refdes, value in components.items():
        if refdes.startswith("L"):
            part = lookup_part(inductor_vendor, value, kind="L")
            assert isinstance(part, ParasiticInductor)
            out[refdes] = {
                "ideal_value": value,
                "snapped_value": part.L_h,
                "Cp": part.Cp_f,
                "Rs": part.Rs_ohm,
                "srf_hz": part.srf_hz,
                "vendor": inductor_vendor,
                "kind": "L",
            }
        elif refdes.startswith("C"):
            part = lookup_part(capacitor_vendor, value, kind="C")
            assert isinstance(part, ParasiticCapacitor)
            out[refdes] = {
                "ideal_value": value,
                "snapped_value": part.C_f,
                "Ls": part.Ls_h,
                "Rs": part.Rs_ohm,
                "srf_hz": part.srf_hz,
                "vendor": capacitor_vendor,
                "kind": "C",
            }
        else:
            raise ValueError(f"Unsupported refdes prefix: {refdes!r}")
    return out
