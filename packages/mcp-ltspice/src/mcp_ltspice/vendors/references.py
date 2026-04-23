"""Voltage reference parameters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class VoltageReferenceModel:
    part_number: str
    vendor: str
    vout_v: float
    initial_accuracy_pct: float
    tempco_ppm_per_c: float
    iq_quiescent_ua: float
    output_noise_uvpp_0p1_to_10hz: float
    typical_use: str


_REF_TABLE: list[VoltageReferenceModel] = [
    VoltageReferenceModel("REF3025", "TI", 2.5, 0.2, 50, 50, 30, "general-purpose ADC reference"),
    VoltageReferenceModel("REF3033", "TI", 3.3, 0.2, 50, 50, 50, "MCU ADC reference at 3.3V"),
    VoltageReferenceModel(
        "ADR4525", "ADI", 2.5, 0.02, 2, 950, 1.25, "ultra-precision 16-bit+ ADC reference"
    ),
    VoltageReferenceModel("LM4040-2.5", "TI", 2.5, 1.0, 100, 0, 60, "low-cost shunt reference"),
    VoltageReferenceModel(
        "ISL21010-2.5", "Renesas", 2.5, 0.05, 5, 100, 5, "precision low-power reference"
    ),
    VoltageReferenceModel(
        "MAX6126-2.5", "ADI", 2.5, 0.02, 3, 550, 1.45, "precision low-noise reference"
    ),
    VoltageReferenceModel(
        "REF50xx", "TI", 5.0, 0.05, 10, 1000, 4, "5V precision reference for instrumentation"
    ),
]


def list_references() -> list[str]:
    return [m.part_number for m in _REF_TABLE]


def lookup_reference(part_number: str) -> VoltageReferenceModel:
    for m in _REF_TABLE:
        if m.part_number.upper() == part_number.upper():
            return m
    raise ValueError(f"Unknown reference '{part_number}'. Available: {list_references()}")
