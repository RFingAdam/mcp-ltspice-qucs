"""Restricted-band-aware transmission-zero placement (issue #12).

Given a TX passband and the harmonic orders to protect against, choose
where an elliptic filter's finite transmission zeros should go. The
optimal zero for a harmonic landing ``[n·f_lo, n·f_hi]`` is the
severity-weighted centroid of its victim-band overlap intervals —

    TZ = Σ sᵢ·wᵢ·midᵢ / Σ sᵢ·wᵢ

over the intersections of each victim band with the landing — not the
landing's geometric centre, which is suboptimal whenever victims
overlap the landing asymmetrically.

Victim bands come from the caller (with optional severity weights) and,
optionally, from this package's GNSS and FCC-restricted band tables.
Trap-index hints follow the mcp-ltspice elliptic convention: the
synthesis fitter consumes sorted-ascending ω_z, so the lowest zero is
trap 2, the next trap 4, and so on — the output composes directly with
``place_transmission_zero``.

This lives in mcp-rf-analysis (the issue sketched mcp-ltspice) because
the band data and harmonic lookups are here and mcp-ltspice carries no
runtime dependency on this package.
"""

from __future__ import annotations

from typing import Any

from mcp_rf_analysis.bands import list_fcc_restricted_bands, list_gnss_bands


def _normalize_victims(
    victim_bands: list[dict[str, Any]],
    include_gnss: bool,
    include_fcc_restricted: bool,
) -> list[dict[str, Any]]:
    victims: list[dict[str, Any]] = []
    for v in victim_bands:
        lo, hi = (float(x) for x in v["freq_range_hz"])
        if not 0.0 < lo < hi:
            raise ValueError(
                f"victim {v.get('name', '?')!r}: freq_range_hz must be [low, high] "
                f"with 0 < low < high; got [{lo}, {hi}]"
            )
        victims.append(
            {
                "name": str(v.get("name", f"{lo / 1e6:.1f}-{hi / 1e6:.1f} MHz")),
                "lo": lo,
                "hi": hi,
                "severity": float(v.get("severity", 1.0)),
                "source": "user",
            }
        )
    if include_gnss:
        for b in list_gnss_bands():
            half = b["bandwidth"] / 2.0
            victims.append(
                {
                    "name": f"GNSS {b['system']} {b['signal']}",
                    "lo": b["f_center"] - half,
                    "hi": b["f_center"] + half,
                    "severity": 1.0,
                    "source": "gnss",
                }
            )
    if include_fcc_restricted:
        for b in list_fcc_restricted_bands():
            victims.append(
                {
                    "name": f"FCC restricted ({b.get('notes', 'unspecified')})",
                    "lo": b["f_low"],
                    "hi": b["f_high"],
                    "severity": 1.0,
                    "source": "fcc_restricted",
                }
            )
    return victims


def place_zeros_for_coex(
    passband_hz: tuple[float, float],
    harmonics: list[int],
    victim_bands: list[dict[str, Any]],
    *,
    n_zeros: int | None = None,
    include_gnss: bool = True,
    include_fcc_restricted: bool = True,
) -> dict[str, Any]:
    """Compute optimal transmission-zero frequencies for the given
    harmonic landings and victim bands. See the module docstring for the
    placement rule; returns ``{zeros, rationale, unprotected_victims,
    victims_not_at_risk}``.
    """
    f_lo, f_hi = (float(x) for x in passband_hz)
    if not 0.0 < f_lo < f_hi:
        raise ValueError(f"passband_hz must be (low, high) with 0 < low < high; got {passband_hz}")
    if not harmonics or any(int(n) < 2 for n in harmonics):
        raise ValueError(f"harmonics must be a non-empty list of orders ≥ 2; got {harmonics}")
    if n_zeros is not None and n_zeros < 1:
        raise ValueError(f"n_zeros must be ≥ 1; got {n_zeros}")
    orders = sorted({int(n) for n in harmonics})

    victims = _normalize_victims(victim_bands, include_gnss, include_fcc_restricted)

    # Per-harmonic overlap analysis
    analysis: list[dict[str, Any]] = []
    for n in orders:
        land_lo, land_hi = n * f_lo, n * f_hi
        overlaps: list[dict[str, Any]] = []
        for v in victims:
            iv_lo = max(land_lo, v["lo"])
            iv_hi = min(land_hi, v["hi"])
            if iv_hi <= iv_lo:
                continue
            overlaps.append(
                {
                    "name": v["name"],
                    "source": v["source"],
                    "severity": v["severity"],
                    "overlap_hz": [iv_lo, iv_hi],
                    "weight": v["severity"] * (iv_hi - iv_lo),
                    "mid": (iv_lo + iv_hi) / 2.0,
                }
            )
        score = sum(o["weight"] for o in overlaps)
        centroid = sum(o["weight"] * o["mid"] for o in overlaps) / score if score > 0.0 else None
        analysis.append(
            {
                "harmonic": n,
                "landing_hz": [land_lo, land_hi],
                "overlaps": overlaps,
                "score": score,
                "centroid": centroid,
            }
        )

    # Zero budget: scored harmonics first (highest aggregate severity·width),
    # then spare zeros to unscored landings at their geometric centres.
    budget = n_zeros if n_zeros is not None else len(orders)
    scored = sorted((a for a in analysis if a["score"] > 0.0), key=lambda a: -a["score"])
    unscored = [a for a in analysis if a["score"] == 0.0]

    zeros: list[dict[str, Any]] = []
    for a in scored[:budget]:
        zeros.append(
            {
                "harmonic": a["harmonic"],
                "target_freq_hz": a["centroid"],
                "placement": "severity_weighted_centroid",
                "landing_hz": a["landing_hz"],
                "victims_covered": [
                    {
                        "name": o["name"],
                        "source": o["source"],
                        "severity": o["severity"],
                        "overlap_hz": o["overlap_hz"],
                    }
                    for o in a["overlaps"]
                ],
            }
        )
    for a in unscored[: max(0, budget - len(zeros))]:
        zeros.append(
            {
                "harmonic": a["harmonic"],
                "target_freq_hz": sum(a["landing_hz"]) / 2.0,
                "placement": "landing_centre_fallback",
                "landing_hz": a["landing_hz"],
                "victims_covered": [],
            }
        )

    # Trap hints: mcp-ltspice's elliptic fitter pins traps to the
    # sorted-ascending zero list, so trap 2 gets the lowest frequency.
    for i, z in enumerate(sorted(zeros, key=lambda z: z["target_freq_hz"])):
        z["trap_index_hint"] = 2 * (i + 1)
    zeros.sort(key=lambda z: z["harmonic"])

    zeroed = {z["harmonic"] for z in zeros}
    unprotected: dict[str, dict[str, Any]] = {}
    for a in analysis:
        if a["harmonic"] in zeroed:
            continue
        for o in a["overlaps"]:
            unprotected.setdefault(
                o["name"],
                {"name": o["name"], "source": o["source"], "harmonic": a["harmonic"]},
            )

    at_risk = {o["name"] for a in analysis for o in a["overlaps"]}
    not_at_risk = [
        {"name": v["name"]} for v in victims if v["source"] == "user" and v["name"] not in at_risk
    ]

    lines = [
        f"Passband {f_lo / 1e6:.2f}-{f_hi / 1e6:.2f} MHz; "
        f"zero budget {budget} across harmonics {orders}."
    ]
    for a in analysis:
        tag = f"**{a['harmonic']}H** landing {a['landing_hz'][0] / 1e6:.2f}-{a['landing_hz'][1] / 1e6:.2f} MHz"
        assigned: dict[str, Any] | None = next(
            (zz for zz in zeros if zz["harmonic"] == a["harmonic"]), None
        )
        if assigned is None:
            lines.append(
                f"- {tag}: no zero assigned (budget exhausted); "
                f"{len(a['overlaps'])} victim overlap(s) left unprotected."
            )
        elif assigned["placement"] == "severity_weighted_centroid":
            names = ", ".join(
                f"{o['name']} (s={o['severity']:g}, "
                f"{o['overlap_hz'][0] / 1e6:.2f}-{o['overlap_hz'][1] / 1e6:.2f} MHz)"
                for o in a["overlaps"]
            )
            lines.append(
                f"- {tag}: zero at {assigned['target_freq_hz'] / 1e6:.2f} MHz "
                f"(severity-weighted centroid) covering {names}; "
                f"trap hint L{assigned['trap_index_hint']}/C{assigned['trap_index_hint']}."
            )
        else:
            lines.append(
                f"- {tag}: no victim overlap — spare zero at the landing centre "
                f"{assigned['target_freq_hz'] / 1e6:.2f} MHz (fallback); "
                f"trap hint L{assigned['trap_index_hint']}/C{assigned['trap_index_hint']}."
            )
    if include_gnss or include_fcc_restricted:
        lines.append(
            "Auto-loaded victims (severity 1.0): "
            + ", ".join(
                s
                for s, on in (("GNSS", include_gnss), ("FCC-restricted", include_fcc_restricted))
                if on
            )
            + " — pass them explicitly with a severity to re-weight."
        )

    return {
        "zeros": zeros,
        "rationale": "\n".join(lines),
        "unprotected_victims": list(unprotected.values()),
        "victims_not_at_risk": not_at_risk,
    }
