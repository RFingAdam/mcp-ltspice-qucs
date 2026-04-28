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
from typing import Any, Literal


class SrfRejectionError(ValueError):
    """Raised when no candidate part in a vendor table satisfies the SRF margin.

    Carries diagnostic data so the caller can either relax ``srf_margin``,
    pick a different vendor series, or restructure the trap to use a
    smaller-value (higher-SRF) component.
    """

    def __init__(
        self,
        refdes: str,
        kind: str,
        vendor: str,
        target_value: float,
        threshold_hz: float,
        candidates: list[dict[str, Any]],
    ):
        self.refdes = refdes
        self.kind = kind
        self.vendor = vendor
        self.target_value = target_value
        self.threshold_hz = threshold_hz
        self.candidates = candidates
        super().__init__(
            f"{refdes} ({kind}, vendor={vendor}, target={target_value:.3e}): "
            f"no candidate part has SRF ≥ {threshold_hz / 1e9:.2f} GHz. "
            f"Inspected {len(candidates)} candidates."
        )


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


def lookup_part_with_srf_margin(
    vendor: str,
    value: float,
    *,
    kind: Literal["L", "C"],
    min_srf_hz: float,
    max_value_drift_pct: float | None = None,
) -> tuple[ParasiticInductor | ParasiticCapacitor, list[dict[str, Any]]]:
    """Find the part closest to ``value`` whose SRF ≥ ``min_srf_hz``.

    Search strategy:

    1. Find the nearest catalogue value, ``v_nearest``.
    2. If its SRF qualifies, return it.
    3. Otherwise, expand the search to neighbours in the catalogue. For
       inductors, smaller L → higher SRF (Cp roughly fixed). For
       capacitors, smaller C → higher SRF (Ls roughly fixed). So we
       explore in the direction that *raises* SRF first.
    4. ``max_value_drift_pct`` (default ``None``) bounds how far from
       ``value`` the substituted part may be. If set, candidates whose
       value drifts beyond this bound are skipped — preventing silent
       substitution of a wildly different value just to chase SRF.
    5. Return the first qualifying part, or raise :class:`SrfRejectionError`.

    Returns a tuple ``(part, rejected_candidates)`` so the caller can
    surface the rejection trail in a report.
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
    rejected: list[dict[str, Any]] = []

    nearest_idx = min(range(len(keys)), key=lambda i: abs(keys[i] - value))
    # Build a search order: start at nearest, then alternate down/up.
    # SRF rises as the component value drops (Cp/Ls are roughly constant).
    # So search smaller values first when the nearest is rejected.
    order: list[int] = [nearest_idx]
    down_i, up_i = nearest_idx - 1, nearest_idx + 1
    while down_i >= 0 or up_i < len(keys):
        if down_i >= 0:
            order.append(down_i)
            down_i -= 1
        if up_i < len(keys):
            order.append(up_i)
            up_i += 1

    for idx in order:
        cand_value = keys[idx]
        candidate = table[cand_value]
        drift_pct = abs(cand_value - value) / value * 100.0
        if max_value_drift_pct is not None and drift_pct > max_value_drift_pct:
            rejected.append(
                {
                    "candidate_value": cand_value,
                    "candidate_srf_hz": candidate.srf_hz,
                    "threshold_hz": min_srf_hz,
                    "value_drift_pct": drift_pct,
                    "rejected_for": "value_drift",
                }
            )
            continue
        if candidate.srf_hz >= min_srf_hz:
            return candidate, rejected  # type: ignore[return-value]
        rejected.append(
            {
                "candidate_value": cand_value,
                "candidate_srf_hz": candidate.srf_hz,
                "threshold_hz": min_srf_hz,
                "value_drift_pct": drift_pct,
                "rejected_for": "srf",
            }
        )

    raise SrfRejectionError(
        refdes="?",
        kind=kind,
        vendor=vendor,
        target_value=value,
        threshold_hz=min_srf_hz,
        candidates=rejected,
    )


def _resolve_max_spec_freq_hz(
    spec: dict | None,
    max_spec_freq_hz: float | None,
) -> float | None:
    """Coerce a FilterSpec dict (or explicit Hz) into a single max-target Hz."""
    if max_spec_freq_hz is not None:
        return float(max_spec_freq_hz)
    if spec is None:
        return None
    pb = spec.get("passband") or {}
    f_stop_pb = pb.get("f_stop")
    targets = spec.get("stopband_targets") or []
    target_freqs = [t["freq"] for t in targets if "freq" in t]
    candidates = [f for f in [f_stop_pb, *target_freqs] if f is not None]
    if not candidates:
        return None
    return float(max(candidates))


def substitute_real_components(
    components: dict[str, float],
    inductor_vendor: str = "coilcraft_0402hp",
    capacitor_vendor: str = "murata_gjm_c0g",
    *,
    srf_margin: float = 0.0,
    max_spec_freq_hz: float | None = None,
    spec: dict | None = None,
    max_value_drift_pct: float | None = 25.0,
) -> dict[str, dict[str, float]]:
    """Return a mapping of refdes → {ideal_value, snapped_value, Cp/Ls,
    Rs, SRF} describing the realized vendor part for each ideal component.

    Parameters
    ----------
    components
        Mapping refdes → ideal value (henrys for L*, farads for C*).
    inductor_vendor, capacitor_vendor
        Vendor series keys (e.g. ``"coilcraft_0402hp"``).
    srf_margin
        If > 0, parts whose ``SRF < srf_margin × max_spec_freq_hz``
        are rejected and the nearest qualifying neighbour is substituted
        instead. ``0.0`` (default) preserves legacy behaviour.
    max_spec_freq_hz
        Highest spec target frequency. Required when ``srf_margin > 0``
        unless ``spec`` is provided.
    spec
        FilterSpec dict — if given, ``max_spec_freq_hz`` is auto-derived
        as ``max(passband.f_stop, *stopband_targets[*].freq)``.

    Returns a ``dict[refdes, info_dict]``. When SRF rejection is active,
    each entry includes a ``rejected_candidates`` field listing parts
    that failed the SRF check before the substitution settled.
    """
    out: dict[str, dict[str, float]] = {}
    if srf_margin < 0:
        raise ValueError(f"srf_margin must be ≥ 0, got {srf_margin}")

    min_srf_hz: float | None = None
    if srf_margin > 0:
        max_freq = _resolve_max_spec_freq_hz(spec, max_spec_freq_hz)
        if max_freq is None:
            raise ValueError(
                "srf_margin > 0 requires either max_spec_freq_hz or a spec dict "
                "with passband.f_stop / stopband_targets[*].freq"
            )
        min_srf_hz = srf_margin * max_freq

    for refdes, value in components.items():
        if refdes.startswith("L"):
            kind: Literal["L", "C"] = "L"
            vendor = inductor_vendor
        elif refdes.startswith("C"):
            kind = "C"
            vendor = capacitor_vendor
        else:
            raise ValueError(f"Unsupported refdes prefix: {refdes!r}")

        rejected: list[dict[str, Any]] = []
        if min_srf_hz is None:
            part = lookup_part(vendor, value, kind=kind)
        else:
            try:
                part, rejected = lookup_part_with_srf_margin(
                    vendor,
                    value,
                    kind=kind,
                    min_srf_hz=min_srf_hz,
                    max_value_drift_pct=max_value_drift_pct,
                )
            except SrfRejectionError as e:
                # Re-raise with the actual refdes filled in
                raise SrfRejectionError(
                    refdes=refdes,
                    kind=kind,
                    vendor=vendor,
                    target_value=value,
                    threshold_hz=e.threshold_hz,
                    candidates=e.candidates,
                ) from None

        if kind == "L":
            assert isinstance(part, ParasiticInductor)
            entry: dict[str, Any] = {
                "ideal_value": value,
                "snapped_value": part.L_h,
                "Cp": part.Cp_f,
                "Rs": part.Rs_ohm,
                "srf_hz": part.srf_hz,
                "vendor": vendor,
                "kind": "L",
            }
        else:
            assert isinstance(part, ParasiticCapacitor)
            entry = {
                "ideal_value": value,
                "snapped_value": part.C_f,
                "Ls": part.Ls_h,
                "Rs": part.Rs_ohm,
                "srf_hz": part.srf_hz,
                "vendor": vendor,
                "kind": "C",
            }
        if rejected:
            entry["rejected_candidates"] = rejected
        out[refdes] = entry

    return out
