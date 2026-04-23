"""Small-signal BJT parameters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass
class BJTModel:
    part_number: str
    vendor: str
    polarity: Literal["NPN", "PNP"]
    package: str
    vceo_max_v: float
    ic_max_ma: float
    hfe_typ: float
    ft_mhz: float
    typical_use: str


_BJT_TABLE: list[BJTModel] = [
    BJTModel(
        "2N3904",
        "Diodes Inc",
        "NPN",
        "TO-92",
        40,
        200,
        200,
        270,
        "general-purpose small-signal NPN",
    ),
    BJTModel(
        "2N3906",
        "Diodes Inc",
        "PNP",
        "TO-92",
        40,
        200,
        100,
        250,
        "general-purpose small-signal PNP",
    ),
    BJTModel("BC547", "ON Semi", "NPN", "TO-92", 45, 100, 300, 300, "low-noise audio NPN"),
    BJTModel("BC557", "ON Semi", "PNP", "TO-92", 45, 100, 200, 200, "low-noise audio PNP"),
    BJTModel("MMBT3904", "ON Semi", "NPN", "SOT-23", 40, 200, 200, 270, "SMD 2N3904"),
    BJTModel("MMBT3906", "ON Semi", "PNP", "SOT-23", 40, 200, 100, 250, "SMD 2N3906"),
    BJTModel("BFR93A", "NXP", "NPN", "SOT-23", 12, 35, 80, 6000, "RF / VHF amplifier"),
    BJTModel("MMBT2222A", "ON Semi", "NPN", "SOT-23", 40, 600, 100, 300, "general-purpose SMD"),
]


def list_bjts() -> list[str]:
    return [m.part_number for m in _BJT_TABLE]


def lookup_bjt(part_number: str) -> BJTModel:
    for m in _BJT_TABLE:
        if m.part_number.upper() == part_number.upper():
            return m
    raise ValueError(f"Unknown BJT '{part_number}'. Available: {list_bjts()}")
