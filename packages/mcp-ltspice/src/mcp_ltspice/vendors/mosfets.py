"""Power MOSFET parameters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class MOSFETModel:
    part_number: str
    vendor: str
    polarity: Literal["N", "P"]
    package: str
    vds_max_v: float
    id_continuous_a: float
    rds_on_max_mohm: float  # at the spec'd Vgs
    vgs_threshold_v: float  # typ
    qg_total_nc: float  # gate charge total
    qgd_nc: float  # Miller charge (drives switching loss)
    typical_use: str
    notes: str = ""


_MOSFET_TABLE: list[MOSFETModel] = [
    MOSFETModel(
        "BSS138",
        "Diodes Inc",
        "N",
        "SOT-23",
        50.0,
        0.22,
        3500.0,
        1.5,
        1.0,
        0.5,
        "low-side level shifter / signal switch",
        "logic-level",
    ),
    MOSFETModel(
        "AO3400A",
        "Alpha & Omega",
        "N",
        "SOT-23",
        30.0,
        5.7,
        50.0,
        1.7,
        6.0,
        1.5,
        "low-current power switch",
        "",
    ),
    MOSFETModel(
        "IRF7341",
        "Infineon",
        "N",
        "SO-8",
        55.0,
        4.7,
        33.0,
        2.0,
        11.0,
        4.0,
        "DC motor driver / load switch",
        "dual N-FET",
    ),
    MOSFETModel(
        "FDC6301N",
        "ON Semi",
        "N",
        "SuperSOT-6",
        30.0,
        0.2,
        1500.0,
        1.5,
        0.6,
        0.3,
        "logic-level signal switch",
        "dual N-FET",
    ),
    MOSFETModel(
        "NTMFS5C604NL",
        "ON Semi",
        "N",
        "SO-8FL",
        60.0,
        100.0,
        4.7,
        2.5,
        30.0,
        6.0,
        "buck SMPS sync rectifier",
        "low Rds_on for high efficiency",
    ),
    MOSFETModel(
        "CSD17552Q5A",
        "TI",
        "N",
        "VSON-8",
        30.0,
        60.0,
        7.5,
        1.6,
        14.0,
        2.7,
        "buck high-side switch",
        "very low Qg for fast switching",
    ),
    MOSFETModel(
        "AOZ8200CIL",
        "Alpha & Omega",
        "N",
        "DFN3x3",
        100.0,
        30.0,
        30.0,
        3.0,
        22.0,
        7.0,
        "high-voltage switch / 12-48V industrial",
        "",
    ),
    MOSFETModel(
        "DMP3098L",
        "Diodes Inc",
        "P",
        "SOT-23",
        30.0,
        2.4,
        95.0,
        1.0,
        5.0,
        1.5,
        "high-side load switch (low-side driver)",
        "P-FET",
    ),
    MOSFETModel(
        "AO3401A",
        "Alpha & Omega",
        "P",
        "SOT-23",
        30.0,
        4.0,
        60.0,
        1.0,
        6.5,
        1.6,
        "P-channel high-side switch",
        "",
    ),
    MOSFETModel(
        "SI4435",
        "Vishay",
        "P",
        "SO-8",
        30.0,
        9.0,
        28.0,
        1.5,
        13.0,
        3.5,
        "high-current P-FET load switch",
        "",
    ),
    MOSFETModel(
        "BSS84",
        "Diodes Inc",
        "P",
        "SOT-23",
        50.0,
        0.13,
        10000.0,
        1.5,
        0.5,
        0.3,
        "logic-level P-FET signal switch",
        "",
    ),
    MOSFETModel(
        "STD15N06L",
        "ST",
        "N",
        "DPAK",
        60.0,
        15.0,
        90.0,
        2.0,
        14.0,
        4.5,
        "automotive 12V industrial",
        "logic-level Vgs",
    ),
]


def list_mosfets() -> list[str]:
    return [m.part_number for m in _MOSFET_TABLE]


def lookup_mosfet(part_number: str) -> MOSFETModel:
    for m in _MOSFET_TABLE:
        if m.part_number.upper() == part_number.upper():
            return m
    raise ValueError(f"Unknown MOSFET '{part_number}'. Available: {list_mosfets()}")


def find_mosfet_for_application(
    *,
    polarity: Literal["N", "P"] = "N",
    min_vds_v: float = 0.0,
    min_id_a: float = 0.0,
    max_rds_on_mohm: float = 1e9,
    max_vgs_threshold_v: float = 1e9,
    sort_by: str = "rds_on_max_mohm",
) -> list[MOSFETModel]:
    """Filter and rank MOSFET candidates by spec.

    Default sort: ascending Rds_on (lowest loss first). Other useful
    sorts: 'qg_total_nc' (fastest switching), 'qgd_nc' (lowest Miller).
    """
    out = [
        m
        for m in _MOSFET_TABLE
        if m.polarity == polarity
        and m.vds_max_v >= min_vds_v
        and m.id_continuous_a >= min_id_a
        and m.rds_on_max_mohm <= max_rds_on_mohm
        and m.vgs_threshold_v <= max_vgs_threshold_v
    ]
    out.sort(key=lambda m: getattr(m, sort_by))
    return out
