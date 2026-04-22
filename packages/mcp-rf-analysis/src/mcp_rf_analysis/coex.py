"""Multi-radio coexistence analysis: harmonic victims and TX × RX matrix."""

from __future__ import annotations

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
    "list_5gnr_bands",
    "list_gnss_bands",
    "list_halow_channels",
    "list_ism_bands",
    "list_lte_bands",
    "lookup_harmonic_victims",
    "db_minus",
]
