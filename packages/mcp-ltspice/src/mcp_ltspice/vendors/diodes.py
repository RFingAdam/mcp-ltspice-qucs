"""Diode parameters: Schottky, signal, TVS, zener, ESD."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DiodeKind = Literal["schottky", "signal", "tvs", "zener", "esd"]


@dataclass
class DiodeModel:
    part_number: str
    vendor: str
    kind: DiodeKind
    package: str
    vrrm_v: float  # peak repetitive reverse voltage
    if_avg_a: float  # average forward current
    vf_typ_v: float  # typ Vf at If_avg
    trr_ns: float  # reverse recovery time (signal/schottky); NA for TVS
    typical_use: str


_DIODE_TABLE: list[DiodeModel] = [
    DiodeModel(
        "1N4148",
        "Diodes Inc",
        "signal",
        "DO-35",
        75,
        0.2,
        1.0,
        4,
        "general-purpose signal switching",
    ),
    DiodeModel("LL4148", "ON Semi", "signal", "MELF", 75, 0.15, 1.0, 4, "SMD 1N4148"),
    DiodeModel("BAS70", "NXP", "schottky", "SOT-23", 70, 0.07, 0.41, 1, "low-Vf signal Schottky"),
    DiodeModel(
        "BAT54",
        "Diodes Inc",
        "schottky",
        "SOT-23",
        30,
        0.2,
        0.32,
        5,
        "general-purpose Schottky, low Vf",
    ),
    DiodeModel(
        "SS14",
        "Diodes Inc",
        "schottky",
        "SMA",
        40,
        1.0,
        0.55,
        5,
        "1A power Schottky for buck rectifier",
    ),
    DiodeModel("MBR340", "ON Semi", "schottky", "DO-201AD", 40, 3.0, 0.45, 5, "3A power Schottky"),
    DiodeModel("SMAJ5.0A", "Littelfuse", "tvs", "SMA", 5.0, 1.0, 6.0, 0, "5V uni-directional TVS"),
    DiodeModel(
        "SMAJ12CA", "Littelfuse", "tvs", "SMA", 12.0, 1.0, 13.3, 0, "12V bi-directional TVS"
    ),
    DiodeModel(
        "ESD9X3.3CT5G",
        "ON Semi",
        "esd",
        "SOD-923",
        3.3,
        0.0,
        0.0,
        0,
        "ESD protection for high-speed signals (USB, HDMI)",
    ),
    DiodeModel("BZX84-C5V1", "NXP", "zener", "SOT-23", 5.1, 0.005, 0.6, 0, "5.1V zener reference"),
    DiodeModel("BZX84-C12", "NXP", "zener", "SOT-23", 12.0, 0.005, 0.6, 0, "12V zener reference"),
]


def list_diodes() -> list[str]:
    return [m.part_number for m in _DIODE_TABLE]


def lookup_diode(part_number: str) -> DiodeModel:
    for m in _DIODE_TABLE:
        if m.part_number.upper() == part_number.upper():
            return m
    raise ValueError(f"Unknown diode '{part_number}'. Available: {list_diodes()}")
