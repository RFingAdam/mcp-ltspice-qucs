"""Curated substrate preset library.

Saves engineers from typing ``{er, h, t, tan_d}`` for the same FR4 / Rogers
substrate every call. Values from each manufacturer's datasheet (typical
values at 10 GHz unless noted).

Usage:
    from mcp_qucs_s.substrates import SUBSTRATE_PRESETS, get_substrate
    sub = get_substrate("Rogers4350B_0508")  # returns a Substrate dataclass

To extend, add an entry to ``SUBSTRATE_PRESETS`` with documented source.
"""

from __future__ import annotations

from mcp_qucs_s.microstrip import Substrate

# Each preset entry: (er, h_mm, t_um, tan_d, brief description)
_PRESETS_SPEC: dict[str, tuple[float, float, float, float, str]] = {
    # --- FR-4 (general-purpose epoxy / glass; loss tangent rises with frequency) ---
    "FR4_0254": (
        4.4,
        0.254,
        35.0,
        0.020,
        "FR-4 standard 10 mil (0.254 mm), 1 oz copper. Generic; values @ 1 GHz. "
        "tan_d rises to 0.025-0.03 above 5 GHz.",
    ),
    "FR4_0508": (
        4.4,
        0.508,
        35.0,
        0.020,
        "FR-4 standard 20 mil (0.508 mm), 1 oz copper. Generic; values @ 1 GHz.",
    ),
    "FR4_0813": (
        4.4,
        0.813,
        35.0,
        0.020,
        "FR-4 standard 32 mil (0.813 mm), 1 oz copper. Generic; values @ 1 GHz.",
    ),
    "FR4_1524": (
        4.4,
        1.524,
        35.0,
        0.020,
        "FR-4 standard 60 mil (1.524 mm), 1 oz copper. Generic; values @ 1 GHz.",
    ),
    # --- Rogers 4000-series (low-loss hydrocarbon ceramic; widely used for RF) ---
    "Rogers4350B_0254": (
        3.66,
        0.254,
        35.0,
        0.0037,
        "Rogers RO4350B 10 mil, 1 oz copper. εr at 10 GHz, tan_d at 10 GHz. "
        "Standard for X-band and below.",
    ),
    "Rogers4350B_0508": (
        3.66,
        0.508,
        35.0,
        0.0037,
        "Rogers RO4350B 20 mil, 1 oz copper. εr at 10 GHz.",
    ),
    "Rogers4350B_0813": (
        3.66,
        0.813,
        35.0,
        0.0037,
        "Rogers RO4350B 32 mil, 1 oz copper.",
    ),
    "Rogers4003C_0203": (
        3.55,
        0.203,
        35.0,
        0.0027,
        "Rogers RO4003C 8 mil, 1 oz copper. Lower loss than 4350B; popular for high-Q resonators.",
    ),
    "Rogers4003C_0508": (
        3.55,
        0.508,
        35.0,
        0.0027,
        "Rogers RO4003C 20 mil, 1 oz copper.",
    ),
    # --- Rogers RT/Duroid 5000-series (PTFE / glass; ultra-low loss) ---
    "Duroid5880_0508": (
        2.20,
        0.508,
        35.0,
        0.0009,
        "Rogers RT/Duroid 5880 20 mil, 1 oz copper. εr at 10 GHz, tan_d "
        "at 10 GHz. Extremely low-loss; phased-array radars, mm-wave.",
    ),
    "Duroid5880_1575": (
        2.20,
        1.575,
        35.0,
        0.0009,
        "Rogers RT/Duroid 5880 62 mil, 1 oz copper.",
    ),
    "Duroid6002_0508": (
        2.94,
        0.508,
        35.0,
        0.0012,
        "Rogers RT/Duroid 6002 20 mil, 1 oz copper. Higher εr than 5880, still very low loss.",
    ),
    # --- PTFE / Teflon (legacy, high-frequency) ---
    "PTFE_0508": (
        2.10,
        0.508,
        35.0,
        0.0010,
        "Generic PTFE / Teflon 20 mil, 1 oz copper. Mostly superseded by "
        "Duroid 5880 in modern designs.",
    ),
    # --- Isola (general-purpose mid-range) ---
    "Isola_FR408HR_0203": (
        3.69,
        0.203,
        35.0,
        0.0094,
        "Isola FR408HR 8 mil, 1 oz copper. Improved FR-4 with lower loss; "
        "popular for high-speed digital + mid-frequency RF.",
    ),
    "Isola_FR408HR_0508": (
        3.69,
        0.508,
        35.0,
        0.0094,
        "Isola FR408HR 20 mil, 1 oz copper.",
    ),
    # --- Taconic (RF / microwave) ---
    "Taconic_TLY5_0508": (
        2.20,
        0.508,
        35.0,
        0.0009,
        "Taconic TLY-5 20 mil, 1 oz copper. εr stable to 110 GHz.",
    ),
}


SUBSTRATE_PRESETS: dict[str, dict[str, float]] = {
    name: {"er": er, "h_mm": h, "t_um": t, "tan_d": td}
    for name, (er, h, t, td, _) in _PRESETS_SPEC.items()
}


SUBSTRATE_DESCRIPTIONS: dict[str, str] = {
    name: desc for name, (_, _, _, _, desc) in _PRESETS_SPEC.items()
}


def get_substrate(name: str) -> Substrate:
    """Look up a preset by name and return a :class:`Substrate` dataclass.

    Raises ``KeyError`` if the preset is not registered.
    """
    if name not in SUBSTRATE_PRESETS:
        raise KeyError(f"Unknown substrate preset {name!r}. Available: {sorted(SUBSTRATE_PRESETS)}")
    p = SUBSTRATE_PRESETS[name]
    return Substrate(
        er=p["er"],
        h_mm=p["h_mm"],
        t_um=p["t_um"],
        tan_d=p["tan_d"],
    )


def list_substrate_presets() -> list[dict[str, float | str]]:
    """List all registered presets with their parameters and brief description."""
    return [
        {
            "name": name,
            "er": SUBSTRATE_PRESETS[name]["er"],
            "h_mm": SUBSTRATE_PRESETS[name]["h_mm"],
            "t_um": SUBSTRATE_PRESETS[name]["t_um"],
            "tan_d": SUBSTRATE_PRESETS[name]["tan_d"],
            "description": SUBSTRATE_DESCRIPTIONS[name],
        }
        for name in sorted(SUBSTRATE_PRESETS)
    ]


__all__ = [
    "SUBSTRATE_DESCRIPTIONS",
    "SUBSTRATE_PRESETS",
    "get_substrate",
    "list_substrate_presets",
]
