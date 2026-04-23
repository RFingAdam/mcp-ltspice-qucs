"""Small-signal / general-purpose op-amp parameters.

Each entry captures the parameters needed for filter / amplifier design
selection: GBW, slew rate, input noise, supply range, offset. Full
SPICE subckts for sim should be downloaded from the vendor site
(referenced via ``part_number`` for traceability).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OpAmpModel:
    part_number: str
    vendor: str
    family: str  # "JFET" | "CMOS" | "BIPOLAR" | "RRIO"
    supply_min_v: float
    supply_max_v: float
    rail_to_rail_input: bool
    rail_to_rail_output: bool
    gbw_mhz: float
    slew_rate_v_per_us: float
    input_noise_nv_per_rthz: float
    input_offset_max_uv: float
    iq_quiescent_ua_per_amp: float
    typical_use: str
    notes: str = ""


# A curated set of ~15 op-amps spanning common applications. Datasheet
# typ values @ Vs = ±5V or +5V single-supply, 25°C.
_OPAMP_TABLE: list[OpAmpModel] = [
    OpAmpModel(
        "LM358",
        "TI",
        "BIPOLAR",
        3.0,
        32.0,
        False,
        False,
        1.0,
        0.5,
        50.0,
        5000.0,
        350.0,
        "general-purpose dual, low cost",
        "single-supply OK, output can't reach +Vs",
    ),
    OpAmpModel(
        "LM324",
        "TI",
        "BIPOLAR",
        3.0,
        32.0,
        False,
        False,
        1.0,
        0.5,
        50.0,
        5000.0,
        350.0,
        "general-purpose quad",
        "quad version of LM358",
    ),
    OpAmpModel(
        "MCP6004",
        "Microchip",
        "CMOS",
        1.8,
        6.0,
        True,
        True,
        1.0,
        0.6,
        35.0,
        4500.0,
        100.0,
        "general-purpose RRIO, low power",
        "",
    ),
    OpAmpModel(
        "OPA350",
        "TI",
        "CMOS",
        2.5,
        7.0,
        True,
        True,
        38.0,
        22.0,
        7.0,
        500.0,
        5200.0,
        "high-speed audio / instrumentation",
        "",
    ),
    OpAmpModel(
        "OPA827",
        "TI",
        "JFET",
        8.0,
        36.0,
        False,
        False,
        22.0,
        28.0,
        4.0,
        150.0,
        4800.0,
        "low-noise precision JFET",
        "",
    ),
    OpAmpModel(
        "OPA188",
        "TI",
        "CMOS",
        4.0,
        36.0,
        False,
        True,
        2.0,
        0.8,
        8.8,
        25.0,
        415.0,
        "zero-drift, very low offset",
        "auto-zero topology",
    ),
    OpAmpModel(
        "ADA4528",
        "ADI",
        "CMOS",
        2.2,
        5.5,
        True,
        True,
        4.0,
        0.5,
        5.6,
        2.5,
        1500.0,
        "ultra-low noise zero-drift",
        "",
    ),
    OpAmpModel(
        "LME49710",
        "TI",
        "BIPOLAR",
        5.0,
        36.0,
        False,
        False,
        55.0,
        20.0,
        2.5,
        700.0,
        9100.0,
        "audio precision (ultra-low THD)",
        "",
    ),
    OpAmpModel(
        "AD8629",
        "ADI",
        "CMOS",
        2.7,
        5.5,
        True,
        True,
        2.5,
        1.0,
        22.0,
        1.0,
        850.0,
        "low offset / drift, single supply",
        "",
    ),
    OpAmpModel(
        "LT6275",
        "ADI",
        "BIPOLAR",
        5.0,
        60.0,
        False,
        False,
        45.0,
        25.0,
        1.9,
        130.0,
        11500.0,
        "low-noise audio / sensors",
        "",
    ),
    OpAmpModel(
        "OPA2376",
        "TI",
        "CMOS",
        2.2,
        5.5,
        True,
        True,
        5.5,
        2.0,
        7.5,
        25.0,
        760.0,
        "precision low-noise low-power",
        "",
    ),
    OpAmpModel(
        "THS3491",
        "TI",
        "BIPOLAR",
        9.0,
        33.0,
        False,
        False,
        900.0,
        8000.0,
        2.0,
        1000.0,
        30000.0,
        "high-speed current-feedback ADC driver",
        "",
    ),
    OpAmpModel(
        "ADA4350",
        "ADI",
        "JFET",
        5.0,
        30.0,
        True,
        True,
        13.0,
        17.0,
        7.5,
        100.0,
        3300.0,
        "transimpedance amp / photodiode front-end",
        "",
    ),
    OpAmpModel(
        "LM7171",
        "TI",
        "BIPOLAR",
        5.0,
        36.0,
        False,
        False,
        200.0,
        4100.0,
        14.0,
        800.0,
        6500.0,
        "high-speed video amplifier",
        "",
    ),
    OpAmpModel(
        "AD8542",
        "ADI",
        "CMOS",
        2.7,
        5.5,
        True,
        True,
        1.0,
        0.92,
        42.0,
        1500.0,
        45.0,
        "micropower RRIO",
        "ultra-low Iq",
    ),
]


def list_opamps() -> list[str]:
    return [m.part_number for m in _OPAMP_TABLE]


def lookup_opamp(part_number: str) -> OpAmpModel:
    for m in _OPAMP_TABLE:
        if m.part_number.upper() == part_number.upper():
            return m
    raise ValueError(f"Unknown op-amp '{part_number}'. Available: {list_opamps()}")


def find_opamp_for_application(
    *,
    min_gbw_mhz: float = 0.0,
    max_input_noise_nv_per_rthz: float = 1000.0,
    max_input_offset_uv: float = 1e9,
    min_supply_max_v: float = 0.0,
    rail_to_rail_input: bool | None = None,
    rail_to_rail_output: bool | None = None,
    family: str | None = None,
    sort_by: str = "gbw_mhz",
) -> list[OpAmpModel]:
    """Filter the catalog by spec constraints and rank candidates.

    - ``sort_by``: one of 'gbw_mhz', 'iq_quiescent_ua_per_amp',
      'input_noise_nv_per_rthz', 'input_offset_max_uv'. Defaults to
      sorting by GBW descending (best speed first).
    """
    out = [
        m
        for m in _OPAMP_TABLE
        if m.gbw_mhz >= min_gbw_mhz
        and m.input_noise_nv_per_rthz <= max_input_noise_nv_per_rthz
        and m.input_offset_max_uv <= max_input_offset_uv
        and m.supply_max_v >= min_supply_max_v
        and (rail_to_rail_input is None or m.rail_to_rail_input == rail_to_rail_input)
        and (rail_to_rail_output is None or m.rail_to_rail_output == rail_to_rail_output)
        and (family is None or m.family == family.upper())
    ]
    reverse = sort_by in ("gbw_mhz",)
    out.sort(key=lambda m: getattr(m, sort_by), reverse=reverse)
    return out
