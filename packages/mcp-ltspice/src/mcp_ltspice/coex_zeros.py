"""Restricted-band-aware transmission-zero placement.

Given a fundamental passband, a list of harmonic orders to suppress, and
a list of victim bands (LTE / 5G NR / GNSS / ISM / FCC-restricted), this
module computes the optimal frequency for each transmission zero in an
elliptic LPF.

For an elliptic LPF with N transmission zeros (i.e. N shunt LC traps),
where each zero should land is a *coexistence* question, not a pure
filter-theory question. A textbook Cauer placement spreads zeros
uniformly across the stopband — but if your harmonic landings only
hit a narrow set of victim bands, that wastes filter "cost" on
spectral regions where there are no real victims.

The algorithm is:

1. For each harmonic order n, compute the harmonic landing band
   [n·f_lo_pass, n·f_hi_pass].
2. For each victim band that overlaps the harmonic landing, compute the
   overlap interval and weight it by the victim's severity (default 1.0;
   GNSS / FCC-restricted carry higher weight).
3. The optimal transmission zero for harmonic n is the severity-weighted
   centroid of the overlap intervals (clipped to the harmonic landing).
4. Rank harmonics by aggregate severity coverage; keep the top
   ``n_zeros`` (defaults to len(harmonics)).
5. Assign trap indices in ascending frequency order so the lowest-
   frequency zero corresponds to the trap closest to the passband
   (L2/C2 in a series-first elliptic ladder).

The function returns the zero frequencies plus a markdown rationale and
a list of victims that no zero covers (so the caller knows the order
should be bumped).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Default severity by victim category. GNSS is the most sensitive
# (−130 dBm acquisition) so a harmonic landing on GNSS RX gets weighted
# heavily. FCC restricted bands carry regulatory force. Generic ISM
# bands are lowest because they are themselves shared.
DEFAULT_SEVERITY: dict[str, float] = {
    "gnss": 5.0,
    "fcc_restricted": 4.0,
    "lte_dl": 3.0,
    "lte_ul": 2.0,
    "5gnr_dl": 3.0,
    "5gnr_ul": 2.0,
    "ism": 1.0,
    "other": 1.5,
}


@dataclass
class VictimBand:
    """Normalised victim entry."""

    name: str
    f_low_hz: float
    f_high_hz: float
    severity: float = 1.0
    category: str = "other"
    metadata: dict[str, Any] = field(default_factory=dict)


def _normalise_victim(v: dict[str, Any]) -> VictimBand:
    """Coerce a dict victim spec into a :class:`VictimBand`."""
    name = v.get("name", "<unnamed>")
    if "freq_range_hz" in v:
        f_low, f_high = v["freq_range_hz"]
    elif "f_low_hz" in v and "f_high_hz" in v:
        f_low, f_high = v["f_low_hz"], v["f_high_hz"]
    elif "f_center_hz" in v:
        bw = v.get("bandwidth_hz", 0.0)
        f_low = v["f_center_hz"] - bw / 2
        f_high = v["f_center_hz"] + bw / 2
    else:
        raise ValueError(
            f"Victim {name!r} missing one of: freq_range_hz, "
            f"(f_low_hz+f_high_hz), or (f_center_hz+bandwidth_hz)"
        )
    if f_high < f_low:
        raise ValueError(f"Victim {name!r}: f_high < f_low ({f_high} < {f_low})")

    category = v.get("category", "other")
    severity = v.get("severity")
    if severity is None:
        severity = DEFAULT_SEVERITY.get(category, DEFAULT_SEVERITY["other"])

    metadata = {k: v[k] for k in v if k not in {"name", "freq_range_hz", "f_low_hz", "f_high_hz", "f_center_hz", "bandwidth_hz", "severity", "category"}}
    return VictimBand(
        name=name,
        f_low_hz=float(f_low),
        f_high_hz=float(f_high),
        severity=float(severity),
        category=category,
        metadata=metadata,
    )


def _interval_overlap(
    a_low: float, a_high: float, b_low: float, b_high: float
) -> tuple[float, float] | None:
    """Return the (low, high) of the overlap interval, or None if disjoint."""
    lo = max(a_low, b_low)
    hi = min(a_high, b_high)
    if hi <= lo:
        return None
    return lo, hi


def _gnss_victims_default() -> list[dict[str, Any]]:
    """Lazy soft-import: pull GNSS bands from mcp_rf_analysis if available.

    Falls back to an empty list (no auto-augmentation) if the package
    is not installed.
    """
    try:
        from mcp_rf_analysis.bands import list_gnss_bands  # type: ignore[import-not-found]
    except Exception:  # pragma: no cover - exercised in env without mcp-rf-analysis
        return []
    out: list[dict[str, Any]] = []
    for b in list_gnss_bands():
        bw = b.get("bandwidth", 2.046e6)
        center = b["f_center"]
        out.append(
            {
                "name": f"{b.get('system', 'GNSS')} {b.get('signal', '')}".strip(),
                "f_low_hz": center - bw / 2,
                "f_high_hz": center + bw / 2,
                "category": "gnss",
            }
        )
    return out


def _fcc_restricted_victims_default() -> list[dict[str, Any]]:
    """Lazy soft-import: FCC §15.205 restricted bands from mcp_rf_analysis."""
    try:
        from mcp_rf_analysis.bands import list_fcc_restricted_bands  # type: ignore[import-not-found]
    except Exception:  # pragma: no cover
        return []
    return [
        {
            "name": f"FCC restricted {b.get('label', '')}".strip(),
            "f_low_hz": b["f_low"],
            "f_high_hz": b["f_high"],
            "category": "fcc_restricted",
        }
        for b in list_fcc_restricted_bands()
    ]


def _trap_index_for_zero_rank(rank: int) -> int:
    """Map zero rank (0=lowest freq) to ladder trap index (L2/C2, L4/C4, ...).

    For a standard series-first elliptic ladder, traps live at even
    refdes indices: L2/C2 is the first trap, L4/C4 the second, etc.
    """
    return 2 * (rank + 1)


def place_zeros_for_coex(
    passband_hz: tuple[float, float],
    harmonics: list[int],
    victim_bands: list[dict[str, Any]] | None = None,
    *,
    n_zeros: int | None = None,
    include_gnss: bool = False,
    include_fcc_restricted: bool = False,
) -> dict[str, Any]:
    """Compute optimal transmission-zero frequencies for an elliptic LPF.

    Parameters
    ----------
    passband_hz
        ``(f_low, f_high)`` of the LPF passband, in Hz.
    harmonics
        Harmonic orders to suppress, e.g. ``[2, 3]`` or ``[2, 3, 5, 6]``.
    victim_bands
        Optional list of victim band dicts. Each dict needs at least a
        ``name`` and a frequency spec (one of ``freq_range_hz``,
        ``(f_low_hz, f_high_hz)``, or ``(f_center_hz, bandwidth_hz)``).
        Optional: ``severity`` (float) and ``category``
        (``"gnss"`` | ``"lte_dl"`` | ``"lte_ul"`` | ``"ism"`` | ``"fcc_restricted"`` | ``"other"``).
    n_zeros
        Maximum number of zeros to return. Defaults to ``len(harmonics)``.
        If fewer harmonics show victim coverage, returns fewer zeros.
    include_gnss
        If ``True``, auto-augments ``victim_bands`` with GNSS signals
        from the in-project band data (requires ``mcp_rf_analysis``).
    include_fcc_restricted
        If ``True``, auto-augments with FCC §15.205 restricted bands.

    Returns
    -------
    dict
        See module docstring; contains ``zeros`` (the placement list),
        ``rationale`` (markdown explanation), and ``unprotected_victims``.
    """
    f_lo, f_hi = passband_hz
    if f_hi <= f_lo:
        raise ValueError(f"passband_hz: f_high ({f_hi}) must be > f_low ({f_lo})")
    if not harmonics:
        raise ValueError("harmonics must be a non-empty list of integers ≥ 2")
    if any(n < 2 for n in harmonics):
        raise ValueError("All harmonic orders must be ≥ 2 (1 = fundamental, not a TZ candidate)")

    raw_victims: list[dict[str, Any]] = list(victim_bands or [])
    if include_gnss:
        raw_victims.extend(_gnss_victims_default())
    if include_fcc_restricted:
        raw_victims.extend(_fcc_restricted_victims_default())

    victims = [_normalise_victim(v) for v in raw_victims]

    # For each harmonic, compute the landing band and the per-victim overlap.
    harmonic_records: list[dict[str, Any]] = []
    for n in sorted(set(harmonics)):
        h_lo = n * f_lo
        h_hi = n * f_hi
        overlaps: list[dict[str, Any]] = []
        for v in victims:
            ov = _interval_overlap(h_lo, h_hi, v.f_low_hz, v.f_high_hz)
            if ov is None:
                continue
            o_lo, o_hi = ov
            overlaps.append(
                {
                    "victim": v,
                    "overlap_low_hz": o_lo,
                    "overlap_high_hz": o_hi,
                    "overlap_width_hz": o_hi - o_lo,
                    "overlap_center_hz": (o_lo + o_hi) / 2,
                    "weight": v.severity * (o_hi - o_lo),
                }
            )
        # Severity-weighted centroid of overlap centers (clipped to landing).
        weight_sum = sum(o["weight"] for o in overlaps)
        if overlaps and weight_sum > 0:
            tz_freq = sum(o["overlap_center_hz"] * o["weight"] for o in overlaps) / weight_sum
            tz_freq = max(h_lo, min(h_hi, tz_freq))  # clip to landing
            agg_severity = sum(o["victim"].severity for o in overlaps)
        else:
            tz_freq = (h_lo + h_hi) / 2
            agg_severity = 0.0
        harmonic_records.append(
            {
                "harmonic": n,
                "landing_low_hz": h_lo,
                "landing_high_hz": h_hi,
                "overlaps": overlaps,
                "tz_freq_hz": tz_freq,
                "n_victims_covered": len(overlaps),
                "aggregate_severity": agg_severity,
                "weight_sum": weight_sum,
            }
        )

    # Pick the top-N harmonics by aggregate severity (with non-zero coverage),
    # falling back to bare harmonic order if no victims overlap any harmonic
    # (so the user still gets at least one TZ proposal).
    if n_zeros is None:
        n_zeros = len(harmonics)
    if n_zeros < 1:
        raise ValueError("n_zeros must be ≥ 1")

    covered = [h for h in harmonic_records if h["n_victims_covered"] > 0]
    uncovered = [h for h in harmonic_records if h["n_victims_covered"] == 0]
    covered.sort(key=lambda h: h["aggregate_severity"], reverse=True)
    uncovered.sort(key=lambda h: h["harmonic"])
    chosen = (covered + uncovered)[:n_zeros]
    chosen.sort(key=lambda h: h["tz_freq_hz"])  # ascending freq → ascending trap index

    zeros: list[dict[str, Any]] = []
    for rank, h in enumerate(chosen):
        victims_covered = [
            {
                "name": o["victim"].name,
                "category": o["victim"].category,
                "severity": o["victim"].severity,
                "overlap_hz": [o["overlap_low_hz"], o["overlap_high_hz"]],
            }
            for o in h["overlaps"]
        ]
        zeros.append(
            {
                "harmonic": h["harmonic"],
                "target_freq_hz": h["tz_freq_hz"],
                "trap_index_hint": _trap_index_for_zero_rank(rank),
                "harmonic_landing_hz": [h["landing_low_hz"], h["landing_high_hz"]],
                "victims_covered": victims_covered,
                "aggregate_severity": h["aggregate_severity"],
            }
        )

    chosen_harmonics = {h["harmonic"] for h in chosen}
    unprotected: list[dict[str, Any]] = []
    for v in victims:
        # A victim is "protected" if at least one chosen harmonic overlaps it.
        is_protected = any(
            n in chosen_harmonics
            and _interval_overlap(n * f_lo, n * f_hi, v.f_low_hz, v.f_high_hz) is not None
            for n in harmonics
        )
        if is_protected:
            continue
        # Was this victim hit by *any* harmonic in the input list?
        for n in harmonics:
            ov = _interval_overlap(n * f_lo, n * f_hi, v.f_low_hz, v.f_high_hz)
            if ov is not None:
                unprotected.append(
                    {
                        "name": v.name,
                        "category": v.category,
                        "severity": v.severity,
                        "harmonic_hit": n,
                        "overlap_hz": list(ov),
                    }
                )
                break

    rationale_lines = [
        f"## Transmission-zero placement for passband {f_lo / 1e6:.1f}–{f_hi / 1e6:.1f} MHz",
        "",
        f"Harmonics evaluated: {sorted(set(harmonics))}",
        f"Victims considered: {len(victims)} "
        f"({sum(1 for v in victims if v.category == 'gnss')} GNSS, "
        f"{sum(1 for v in victims if v.category in {'lte_dl', '5gnr_dl'})} cellular DL, "
        f"{sum(1 for v in victims if v.category == 'fcc_restricted')} FCC-restricted)",
        f"Zeros chosen: {len(zeros)} of {n_zeros} requested",
        "",
    ]
    for z in zeros:
        names = ", ".join(v["name"] for v in z["victims_covered"][:3])
        if len(z["victims_covered"]) > 3:
            names += f", +{len(z['victims_covered']) - 3} more"
        elif not z["victims_covered"]:
            names = "no victim overlap (fallback to centroid of harmonic landing)"
        rationale_lines.append(
            f"- **{z['harmonic']}H @ {z['target_freq_hz'] / 1e6:.1f} MHz** "
            f"(trap L{z['trap_index_hint']}/C{z['trap_index_hint']}): "
            f"covers {names}"
        )
    if unprotected:
        rationale_lines.append("")
        rationale_lines.append(f"**{len(unprotected)} victim(s) UNPROTECTED** — consider increasing n_zeros or filter order:")
        for u in unprotected:
            rationale_lines.append(
                f"- {u['name']} ({u['category']}, severity {u['severity']:.1f}) hit by {u['harmonic_hit']}H"
            )

    return {
        "zeros": zeros,
        "rationale": "\n".join(rationale_lines),
        "unprotected_victims": unprotected,
        "harmonic_records": harmonic_records,
    }
