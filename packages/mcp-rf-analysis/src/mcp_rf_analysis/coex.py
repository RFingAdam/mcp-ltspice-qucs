"""Multi-radio coexistence analysis: harmonic victims and TX × RX matrix."""

from __future__ import annotations

import math
from typing import Any

from mcp_rf_analysis.bands import (
    is_in_restricted_band,
    list_5gnr_bands,
    list_gnss_bands,
    list_halow_channels,
    list_ism_bands,
    list_lte_bands,
    lookup_band_by_freq,
)
from mcp_rf_analysis.link import compute_desense, db_minus

#: Accepted values for ``lookup_harmonic_victims(source_type=...)``.
HARMONIC_SOURCE_TYPES = ("intentional", "unintentional", "unspecified")

_RULE_INTENTIONAL_RESTRICTED = (
    "47 CFR 15.205 restricted band: only spurious emissions permitted; the emission "
    "must meet the 15.209 field-strength limits (15.205(b))."
)
_RULE_INTENTIONAL_CLEAR = (
    "Not in a 15.205 restricted band: spurious limit per the intentional-radiator "
    "rule for the device (e.g. 15.247(d) / 15.407(b)) or 15.209."
)
_RULE_UNINTENTIONAL = (
    "check 15.109 limit: 15.205 restricted bands apply to intentional radiators; an "
    "unintentional radiator (clock, oscillator, digital logic) is judged against the "
    "15.109 radiated limits (15.107 conducted) at this frequency."
)
_RULE_UNSPECIFIED = (
    "source_type not given: if this is an intentional radiator's spurious emission "
    "and the frequency is in a 15.205 band, 15.209 limits apply (15.205(b)); if it "
    "is an unintentional radiator (clock/oscillator), check 15.109 limit instead. "
    "Pass source_type to get a single answer."
)


def lookup_harmonic_victims(
    f_center_hz: float,
    harmonic_orders: list[int] | None = None,
    *,
    source_type: str = "unspecified",
) -> list[dict[str, Any]]:
    """For a TX / oscillator center frequency, find which RX bands its
    2nd / 3rd / etc. harmonics fall into, and which FCC rule governs them.

    ``source_type`` selects the FCC framework:

    - ``"intentional"``: a transmitter (Wi-Fi, BLE, HaLow PA output).
      47 CFR 15.205 restricted-band overlap is reported
      (``in_fcc_restricted`` True/False).
    - ``"unintentional"``: a clock oscillator, crystal, digital bus.
      15.205 restricted bands do not apply to it, so
      ``in_fcc_restricted`` is False and ``fcc_rule`` says
      "check 15.109 limit".
    - ``"unspecified"`` (default): the overlap with the 15.205 table is
      reported as a frequency fact only (``frequency_in_15_205_table``)
      and ``in_fcc_restricted`` is None, because whether it matters
      depends on the radiator class.

    Returns a list of ``{harmonic, freq_hz, victims, source_type,
    in_fcc_restricted, frequency_in_15_205_table, fcc_restricted_info,
    fcc_rule}`` where victims is the output of
    :func:`bands.lookup_band_by_freq`.
    """
    if source_type not in HARMONIC_SOURCE_TYPES:
        raise ValueError(
            f"Unknown source_type {source_type!r}; available: {list(HARMONIC_SOURCE_TYPES)}"
        )
    if harmonic_orders is None:
        harmonic_orders = [2, 3, 4, 5]
    out: list[dict[str, Any]] = []
    for n in harmonic_orders:
        f_h = f_center_hz * n
        victims = lookup_band_by_freq(f_h)
        # Squash empty categories
        victims = {k: v for k, v in victims.items() if v}
        overlap, restricted_info = is_in_restricted_band(f_h)
        in_restricted: bool | None
        if source_type == "intentional":
            in_restricted = overlap
            rule = _RULE_INTENTIONAL_RESTRICTED if overlap else _RULE_INTENTIONAL_CLEAR
        elif source_type == "unintentional":
            in_restricted = False
            rule = _RULE_UNINTENTIONAL
        else:
            in_restricted = None
            rule = _RULE_UNSPECIFIED
        out.append(
            {
                "harmonic": n,
                "freq_hz": f_h,
                "victims": victims,
                "source_type": source_type,
                "in_fcc_restricted": in_restricted,
                "frequency_in_15_205_table": overlap,
                "fcc_restricted_info": restricted_info,
                "fcc_rule": rule,
            }
        )
    return out


# --- GNSS-specific desense model (issue #15) --------------------------------
#
# GNSS victims break the generic power-vs-sensitivity model: the real
# mechanism for a co-located TX is broadband PA noise at the GNSS
# frequency, and the industry metric is ΔC/N₀ (dB-Hz). Documented
# assumptions, overridable per entry:

#: PA broadband noise floor far from the carrier when neither the TX
#: entry (``broadband_noise_dbm_hz``) nor the victim entry
#: (``pa_broadband_noise_dbm_hz_at_offset``) specifies one.
GNSS_DEFAULT_PA_NOISE_DBM_HZ = -150.0
GNSS_DEFAULT_NF_DB = 2.0
#: GPS C/A code rate. The correlator spreads an in-band CW tone over
#: this bandwidth (spectral separation coefficient Q = 1 assumed).
GNSS_DEFAULT_CHIP_RATE_HZ = 1.023e6
#: C/N₀ degradation budget used to express GNSS entries as a
#: ``desense_margin_db`` (budget − ΔC/N₀) so mixed matrices stay sortable.
GNSS_CN0_BUDGET_DB = 1.0


def _delta_cn0_db(i0_dbm_hz: float, n0_dbm_hz: float) -> float:
    """Effective-noise-floor rise: ΔC/N₀ = 10·log₁₀(1 + 10^((I₀−N₀)/10))."""
    return 10.0 * math.log10(1.0 + 10.0 ** ((i0_dbm_hz - n0_dbm_hz) / 10.0))


def _gnss_concern(delta_cn0_db: float) -> str:
    if delta_cn0_db >= 3.0:
        return "critical"
    if delta_cn0_db >= 1.0:
        return "high"
    if delta_cn0_db >= 0.5:
        return "medium"
    if delta_cn0_db >= 0.25:
        return "low"
    return "none"


def _gnss_entry(
    tx_name: str,
    rx: dict[str, Any],
    mechanism: str,
    i0_dbm_hz: float,
    assumptions: dict[str, Any],
) -> dict[str, Any]:
    nf_db = rx.get("noise_figure_db", GNSS_DEFAULT_NF_DB)
    n0 = -174.0 + nf_db
    delta = _delta_cn0_db(i0_dbm_hz, n0)
    assumptions = {
        "noise_figure_db": nf_db,
        "cn0_budget_db": GNSS_CN0_BUDGET_DB,
        **assumptions,
    }
    return {
        "aggressor": tx_name,
        "victim": rx["name"],
        "mechanism": mechanism,
        "i0_dbm_hz": i0_dbm_hz,
        "gnss_noise_floor_dbm_hz": n0,
        "delta_cn0_db_hz": delta,
        "desense_margin_db": GNSS_CN0_BUDGET_DB - delta,
        "concern": _gnss_concern(delta),
        "assumptions": assumptions,
    }


#: Unfiltered PA harmonic levels (dBc) assumed when a TX entry gives
#: neither ``harmonic_dbc`` nor ``filtered_harmonic_dbc``.
DEFAULT_PA_HARMONIC_DBC = {"2H": -30.0, "3H": -40.0, "4H": -50.0, "5H": -55.0}


def _band_of(entry: dict[str, Any]) -> tuple[float, float] | None:
    """[low, high] Hz of a radio entry from f_range_hz or f_center_hz +/- bandwidth/2."""
    if "f_range_hz" in entry:
        lo, hi = entry["f_range_hz"]
        return float(lo), float(hi)
    f = entry.get("f_center_hz")
    if f is None:
        return None
    bw = entry.get("bandwidth_hz", 0)
    return f - bw / 2, f + bw / 2


def _tx_center(tx: dict[str, Any]) -> float | None:
    f = tx.get("f_center_hz")
    if f is None and "f_range_hz" in tx:
        f = sum(tx["f_range_hz"]) / 2
    return f


def _tx_emissions(tx: dict[str, Any], default_tx_rejection_db: float) -> list[dict[str, Any]]:
    """Fundamental and harmonics of one aggressor, with the TX-filter
    rejection that applies to each.

    The aggressor's TX filter passes its own fundamental (rejection 0) and
    attenuates harmonics/spurs outside its passband:

    - ``harmonic_dbc``: PA harmonic levels before the TX filter. The TX
      filter rejection (``tx_filter_rejection_db``, or the legacy
      ``filter_rejection_db``, or ``default_filter_rejection_db``; a number
      or a ``{"2H": dB, ...}`` dict) is applied to each harmonic.
    - ``filtered_harmonic_dbc``: harmonic levels already measured at the
      antenna port (after the TX filter). No rejection is applied again.
    - neither: :data:`DEFAULT_PA_HARMONIC_DBC` (unfiltered) plus the TX
      filter rejection.
    """
    f0 = _tx_center(tx)
    if f0 is None:
        return []
    p0 = tx.get("power_dbm", 0.0)
    prefiltered = "filtered_harmonic_dbc" in tx
    harm = (
        tx["filtered_harmonic_dbc"]
        if prefiltered
        else tx.get("harmonic_dbc", DEFAULT_PA_HARMONIC_DBC)
    )
    rej = tx.get("tx_filter_rejection_db", tx.get("filter_rejection_db", default_tx_rejection_db))

    out = [
        {
            "n": 1,
            "mechanism": "fundamental",
            "f_emit_hz": f0,
            "emit_dbm": p0,
            "tx_filter_rejection_db": 0.0,
        }
    ]
    for n in (2, 3, 4, 5):
        key = f"{n}H"
        if key not in harm:
            continue
        if prefiltered:
            r = 0.0
        elif isinstance(rej, dict):
            r = float(rej.get(key, 0.0))
        else:
            r = float(rej)
        out.append(
            {
                "n": n,
                "mechanism": f"harmonic_{n}",
                "f_emit_hz": f0 * n,
                "emit_dbm": p0 + harm[key],  # dBc is negative
                "tx_filter_rejection_db": r,
                "harmonic_level_is": "after TX filter (filtered_harmonic_dbc)"
                if prefiltered
                else "before TX filter",
            }
        )
    return out


def check_coex_matrix(
    tx_list: list[dict[str, Any]],
    rx_list: list[dict[str, Any]],
    *,
    antenna_iso_db: float = 20.0,
    default_filter_rejection_db: float = 0.0,
) -> dict[str, Any]:
    """Compute the aggressor x victim coexistence matrix.

    Radios are identified by ``name``; a radio that both transmits and
    receives appears once in ``tx_list`` and once in ``rx_list`` under the
    same name. Every unordered pair of distinct radios is enumerated once
    (N radios -> N(N-1)/2 pairs) and analysed in each direction in which
    one radio transmits and the other receives.

    TX entry: ``name``, ``f_center_hz`` (or ``f_range_hz``), ``power_dbm``
    (conducted, into the antenna), and optionally ``harmonic_dbc`` /
    ``filtered_harmonic_dbc`` / ``tx_filter_rejection_db`` (see
    :func:`_tx_emissions`; ``default_filter_rejection_db`` is the TX-filter
    rejection used for harmonics when the entry gives none).

    RX entry: ``name``, ``f_range_hz`` (or ``f_center_hz`` +
    ``bandwidth_hz``), ``sensitivity_dbm``, optionally ``noise_floor_dbm``
    (channel noise, used by the in-band mechanism), ``blocking_spec_dbm``
    and/or ``p1db_in_dbm`` (used by the blocker mechanism) and
    ``rx_filter_rejection_db`` (victim RX front-end rejection at the
    out-of-band aggressor frequencies; legacy ``filter_rejection_db``).

    Mechanisms (same as :func:`link.compute_desense`):

    ``in_band``: an aggressor emission landing inside the victim band adds
        to the victim noise floor. Path: emit - TX-filter rejection at that
        emission (0 for the fundamental) - antenna_iso. The victim RX
        filter does not attenuate it: it is in the victim's own passband.
    ``out_of_band_blocker``: the aggressor fundamental outside the victim
        band. Path: P_tx - victim RX-filter rejection - antenna_iso,
        compared with the victim blocking spec / input P1dB. It does not
        add to the noise floor. Without a blocking spec or P1dB the pair is
        listed in ``not_evaluated`` instead of being guessed.

    GNSS victims (``victim_type='gnss'``) keep the dedicated C/N0 model
    (broadband PA noise and chip-rate-spread CW landings). Energy inside the
    GNSS passband is reduced only by the aggressor's TX filter
    (``tx_filter_rejection_db`` for harmonics, ``noise_filter_rejection_db``
    for PA noise at the GNSS frequency) and isolation; the GNSS receiver's
    ``filter_rejection_db`` applies only to an out-of-band fundamental, scored
    on the blocker path against ``blocking_spec_dbm`` / ``p1db_in_dbm``.
    """
    matrix: list[dict[str, Any]] = []
    not_evaluated: list[dict[str, Any]] = []

    tx_by_name: dict[str, dict[str, Any]] = {}
    rx_by_name: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for entry, table in [(t, tx_by_name) for t in tx_list] + [(r, rx_by_name) for r in rx_list]:
        table[entry["name"]] = entry
        if entry["name"] not in order:
            order.append(entry["name"])

    pairs: list[dict[str, Any]] = []
    for i, a in enumerate(order):
        for b in order[i + 1 :]:
            directions = [
                (agg, vic)
                for agg, vic in ((a, b), (b, a))
                if agg in tx_by_name and vic in rx_by_name
            ]
            if directions:
                pairs.append(
                    {"radios": [a, b], "directions": [f"{x} -> {y}" for x, y in directions]}
                )
            for agg, vic in directions:
                _analyse_direction(
                    tx_by_name[agg],
                    rx_by_name[vic],
                    antenna_iso_db,
                    default_filter_rejection_db,
                    matrix,
                    not_evaluated,
                )

    matrix.sort(key=lambda r: r["desense_margin_db"])  # worst (smallest margin) first
    return {
        "n_aggressors": len(tx_by_name),
        "n_victims": len(rx_by_name),
        "n_radios": len(order),
        "n_pairs_analyzed": len(pairs),
        "n_directions_analyzed": sum(len(p["directions"]) for p in pairs),
        "pairs": pairs,
        "matrix": matrix,
        "not_evaluated": not_evaluated,
    }


def _analyse_direction(
    tx: dict[str, Any],
    rx: dict[str, Any],
    antenna_iso_db: float,
    default_tx_rejection_db: float,
    matrix: list[dict[str, Any]],
    not_evaluated: list[dict[str, Any]],
) -> None:
    tx_name, rx_name = tx["name"], rx["name"]
    band = _band_of(rx)
    if band is None:
        not_evaluated.append(
            {"aggressor": tx_name, "victim": rx_name, "reason": "victim has no band"}
        )
        return
    rx_low, rx_high = band
    is_gnss = rx.get("victim_type") == "gnss"

    for em in _tx_emissions(tx, default_tx_rejection_db):
        f_emit, emit_dbm, mech = em["f_emit_hz"], em["emit_dbm"], em["mechanism"]
        inside = rx_low <= f_emit <= rx_high

        if is_gnss and inside:
            # In-band CW landing: the correlator spreads the tone over the
            # code rate, I0 = J - 10*log10(chip_rate). The emission is inside
            # the GNSS passband, so the GNSS receiver's own filter passes it:
            # only the aggressor's TX filter and the antenna isolation apply.
            # (Out-of-band aggressor energy falls through to the blocker path
            # below, which is where the RX filter rejection belongs.)
            chip = rx.get("chip_rate_hz", GNSS_DEFAULT_CHIP_RATE_HZ)
            j_dbm = emit_dbm - em["tx_filter_rejection_db"] - antenna_iso_db
            matrix.append(
                _gnss_entry(
                    tx_name,
                    rx,
                    mech,
                    j_dbm - 10.0 * math.log10(chip),
                    {
                        "chip_rate_hz": chip,
                        "spectral_separation_q": 1.0,
                        "cw_power_at_rx_dbm": j_dbm,
                        "f_emit_hz": f_emit,
                        "tx_filter_rejection_db": em["tx_filter_rejection_db"],
                        "rx_filter_rejection_db": 0.0,
                        "filter_path": "aggressor TX filter only (emission is inside the GNSS passband)",
                    },
                )
            )
            continue

        if inside:
            noise_floor = rx.get("noise_floor_dbm")
            assumptions: dict[str, Any] = {}
            if noise_floor is None:
                noise_floor = rx["sensitivity_dbm"]
                assumptions["noise_floor_dbm"] = (
                    "rx.noise_floor_dbm not given; sensitivity_dbm used as the noise "
                    "floor. This understates desense by the victim's required SNR when "
                    "that SNR is > 0 dB. Pass noise_floor_dbm for a real number."
                )
            res = compute_desense(
                aggressor_power_dbm=emit_dbm,
                filter_rejection_db=em["tx_filter_rejection_db"],
                antenna_iso_db=antenna_iso_db,
                victim_noise_floor_dbm=noise_floor,
                mechanism="in_band",
            )
            matrix.append(
                {
                    "aggressor": tx_name,
                    "victim": rx_name,
                    "mechanism": mech,
                    "coupling": "in_band",
                    "f_emit_hz": f_emit,
                    "emit_dbm": emit_dbm,
                    "victim_band_hz": [rx_low, rx_high],
                    "filter_path": "aggressor TX filter only (emission is inside the victim passband)",
                    "tx_filter_rejection_db": em["tx_filter_rejection_db"],
                    "rx_filter_rejection_db": 0.0,
                    "predicted_at_rx_dbm": res["received_at_rx_dbm"],
                    "victim_sensitivity_dbm": rx["sensitivity_dbm"],
                    "victim_noise_floor_dbm": noise_floor,
                    "desense_db": res["desense_db"],
                    "desense_margin_db": res["desense_margin_db"],
                    "concern": res["concern_level"],
                    "desense_formula": res["formula"],
                    "assumptions": assumptions,
                }
            )
            continue

        if em["n"] != 1:
            continue  # an out-of-band harmonic is not a blocker worth scoring
        spec = rx.get("blocking_spec_dbm")
        p1db = rx.get("p1db_in_dbm")
        if spec is None and p1db is None:
            not_evaluated.append(
                {
                    "aggressor": tx_name,
                    "victim": rx_name,
                    "mechanism": "out_of_band_blocker",
                    "f_emit_hz": f_emit,
                    "reason": "victim gives neither blocking_spec_dbm nor p1db_in_dbm; an "
                    "out-of-band blocker cannot be compared with the noise floor",
                }
            )
            continue
        rx_rej = rx.get("rx_filter_rejection_db", rx.get("filter_rejection_db", 0.0))
        res = compute_desense(
            aggressor_power_dbm=emit_dbm,
            filter_rejection_db=rx_rej,
            antenna_iso_db=antenna_iso_db,
            mechanism="out_of_band_blocker",
            victim_blocking_spec_dbm=spec,
            victim_p1db_in_dbm=p1db,
        )
        matrix.append(
            {
                "aggressor": tx_name,
                "victim": rx_name,
                "mechanism": "fundamental",
                "coupling": "out_of_band_blocker",
                "f_emit_hz": f_emit,
                "emit_dbm": emit_dbm,
                "victim_band_hz": [rx_low, rx_high],
                "filter_path": "victim RX filter only (aggressor fundamental is inside the aggressor passband)",
                "tx_filter_rejection_db": 0.0,
                "rx_filter_rejection_db": rx_rej,
                "predicted_at_rx_dbm": res["received_at_rx_dbm"],
                "victim_sensitivity_dbm": rx.get("sensitivity_dbm"),
                "victim_blocking_spec_dbm": spec,
                "victim_p1db_in_dbm": p1db,
                "desense_db": None,
                "desense_margin_db": res["desense_margin_db"],
                "concern": res["concern_level"],
                "desense_formula": res["formula"],
                "assumptions": {},
            }
        )

    if is_gnss:
        # Broadband PA noise at the GNSS victim: once per aggressor -> GNSS
        # direction, independent of harmonic landings (the dominant
        # real-world GNSS desense mechanism for co-located transmitters).
        # The PA noise sits at the GNSS frequency, i.e. inside the GNSS
        # passband, so the GNSS receiver's filter cannot reject it. Only a
        # filter on the aggressor side (``noise_filter_rejection_db``: TX
        # filter rejection at the GNSS frequency) and isolation reduce it.
        pa_noise = rx.get(
            "pa_broadband_noise_dbm_hz_at_offset",
            tx.get("broadband_noise_dbm_hz", GNSS_DEFAULT_PA_NOISE_DBM_HZ),
        )
        tx_noise_rej = tx.get("noise_filter_rejection_db", 0.0)
        i0 = pa_noise - tx_noise_rej - antenna_iso_db
        matrix.append(
            _gnss_entry(
                tx_name,
                rx,
                "broadband_noise",
                i0,
                {
                    "pa_broadband_noise_dbm_hz": pa_noise,
                    "tx_noise_filter_rejection_db": tx_noise_rej,
                    "rx_filter_rejection_db": 0.0,
                    "filter_path": "aggressor TX filter only (PA noise is inside the GNSS passband)",
                    "antenna_iso_db": antenna_iso_db,
                },
            )
        )


# Re-export for convenience
__all__ = [
    "check_coex_matrix",
    "db_minus",
    "list_5gnr_bands",
    "list_gnss_bands",
    "list_halow_channels",
    "list_ism_bands",
    "list_lte_bands",
    "lookup_harmonic_victims",
]
