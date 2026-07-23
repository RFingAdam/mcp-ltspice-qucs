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


def lookup_harmonic_victims(
    f_center_hz: float,
    harmonic_orders: list[int] | None = None,
) -> list[dict[str, Any]]:
    """For a TX center frequency, find which RX bands its 2nd / 3rd / etc.
    harmonics fall into.

    Returns a list of {harmonic, freq, victims} where victims is the
    output of :func:`bands.lookup_band_by_freq`.
    """
    if harmonic_orders is None:
        harmonic_orders = [2, 3, 4, 5]
    out: list[dict[str, Any]] = []
    for n in harmonic_orders:
        f_h = f_center_hz * n
        victims = lookup_band_by_freq(f_h)
        # Squash empty categories
        victims = {k: v for k, v in victims.items() if v}
        in_restricted, restricted_info = is_in_restricted_band(f_h)
        out.append(
            {
                "harmonic": n,
                "freq_hz": f_h,
                "victims": victims,
                "in_fcc_restricted": in_restricted,
                "fcc_restricted_info": restricted_info,
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
#: GPS C/A code rate — the correlator spreads an in-band CW tone over
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


def check_coex_matrix(
    tx_list: list[dict[str, Any]],
    rx_list: list[dict[str, Any]],
    *,
    antenna_iso_db: float = 20.0,
    default_filter_rejection_db: float = 0.0,
) -> dict[str, Any]:
    """Compute the aggressor × victim matrix.

    Each TX entry should provide at least:
        - ``name``
        - ``f_center_hz`` (or ``f_range_hz`` = [low, high])
        - ``power_dbm`` (TX conducted power into antenna)
        - optional ``filtered_harmonic_dbc``: dict mapping "2H" / "3H"
          → dBc (relative to fundamental). Defaults to broadband
          PA assumptions: 2H = -30 dBc, 3H = -40 dBc.

    Each RX entry needs:
        - ``name``
        - ``f_center_hz`` (or ``f_range_hz`` = [low, high])
        - ``sensitivity_dbm`` (e.g. -97 for LTE, -130 for GNSS)

    Returns a list of (aggressor, victim, mechanism, predicted_desense_db).
    """
    matrix: list[dict[str, Any]] = []

    for tx in tx_list:
        tx_name = tx["name"]
        tx_f = tx.get("f_center_hz")
        if tx_f is None and "f_range_hz" in tx:
            tx_f = sum(tx["f_range_hz"]) / 2
        if tx_f is None:
            continue
        tx_pwr = tx.get("power_dbm", 0.0)
        harm = tx.get("filtered_harmonic_dbc", {"2H": -30.0, "3H": -40.0, "4H": -50.0, "5H": -55.0})

        # Fundamental and harmonics
        for n in (1, 2, 3, 4, 5):
            f_emit = tx_f * n
            if n == 1:
                emit_dbm = tx_pwr
                mech = "fundamental"
            else:
                key = f"{n}H"
                if key not in harm:
                    continue
                emit_dbm = tx_pwr + harm[key]  # dBc is negative
                mech = f"harmonic_{n}"

            for rx in rx_list:
                if rx["name"] == tx_name:
                    continue
                rx_f = rx.get("f_center_hz")
                rx_bw = rx.get("bandwidth_hz", 0)
                rx_low, rx_high = (
                    (rx["f_range_hz"][0], rx["f_range_hz"][1])
                    if "f_range_hz" in rx
                    else (rx_f - rx_bw / 2, rx_f + rx_bw / 2)
                )
                if rx_low is None or rx_high is None:
                    continue
                if not (rx_low <= f_emit <= rx_high):
                    continue

                if rx.get("victim_type") == "gnss":
                    # In-band CW landing: the correlator spreads the tone
                    # over the code rate, I₀ = J − 10·log₁₀(chip_rate).
                    chip = rx.get("chip_rate_hz", GNSS_DEFAULT_CHIP_RATE_HZ)
                    j_dbm = emit_dbm - rx.get("filter_rejection_db", 0.0) - antenna_iso_db
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
                            },
                        )
                    )
                    continue

                filt_rej = tx.get("filter_rejection_db", default_filter_rejection_db)
                predicted_at_rx = compute_desense(
                    aggressor_power_dbm=emit_dbm,
                    filter_rejection_db=filt_rej if mech == "fundamental" else 0.0,
                    antenna_iso_db=antenna_iso_db,
                    victim_noise_floor_dbm=rx["sensitivity_dbm"],
                )

                matrix.append(
                    {
                        "aggressor": tx_name,
                        "victim": rx["name"],
                        "mechanism": mech,
                        "f_emit_hz": f_emit,
                        "emit_dbm": emit_dbm,
                        "victim_band_hz": [rx_low, rx_high],
                        "predicted_at_rx_dbm": predicted_at_rx["received_at_rx_dbm"],
                        "victim_sensitivity_dbm": rx["sensitivity_dbm"],
                        "desense_margin_db": predicted_at_rx["desense_margin_db"],
                        "concern": predicted_at_rx["concern_level"],
                    }
                )

    # Broadband PA noise at each GNSS victim — evaluated once per TX×RX
    # pair, independent of harmonic landings (the dominant real-world
    # GNSS desense mechanism for co-located transmitters).
    for tx in tx_list:
        for rx in rx_list:
            if rx.get("victim_type") != "gnss" or rx["name"] == tx["name"]:
                continue
            pa_noise = rx.get(
                "pa_broadband_noise_dbm_hz_at_offset",
                tx.get("broadband_noise_dbm_hz", GNSS_DEFAULT_PA_NOISE_DBM_HZ),
            )
            i0 = pa_noise - rx.get("filter_rejection_db", 0.0) - antenna_iso_db
            matrix.append(
                _gnss_entry(
                    tx["name"],
                    rx,
                    "broadband_noise",
                    i0,
                    {
                        "pa_broadband_noise_dbm_hz": pa_noise,
                        "filter_rejection_at_victim_db": rx.get("filter_rejection_db", 0.0),
                        "antenna_iso_db": antenna_iso_db,
                    },
                )
            )

    matrix.sort(key=lambda r: r["desense_margin_db"])  # worst (smallest margin) first
    return {
        "n_aggressors": len(tx_list),
        "n_victims": len(rx_list),
        "n_pairs_analyzed": len(tx_list) * len(rx_list) * 5,
        "matrix": matrix,
    }


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
