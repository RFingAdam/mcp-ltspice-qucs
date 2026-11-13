"""compute_desense must be told which coupling mechanism applies.

Regression for a design-review finding: compute_desense(blocker 23 dBm,
filter rejection 37.8 dB, isolation 15 dB, "noise floor" -94 dBm) treated
an out-of-band blocker as in-band noise and reported a -64.2 dB margin.
An out-of-band blocker does not add to the noise floor; it is judged
against the receiver's blocking spec / compression point. The three
mechanisms (in-band, out-of-band blocker, reciprocal mixing) now have
separate, documented formulas and the caller must declare one.
"""

from __future__ import annotations

import math

import pytest

from mcp_rf_analysis import server
from mcp_rf_analysis.coex import check_coex_matrix
from mcp_rf_analysis.link import DesenseInputError, compute_desense

# The design-review numbers.
REVIEW = {"aggressor_power_dbm": 23.0, "filter_rejection_db": 37.8, "antenna_iso_db": 15.0}
P_RX = 23.0 - 37.8 - 15.0  # -29.8 dBm at the victim input


def test_review_case_without_mechanism_is_an_error_not_a_noise_margin() -> None:
    """The exact review call. Before the fix it returned ok with
    desense_margin_db = -64.2 (blocker compared with the noise floor)."""
    env = server.compute_desense(**REVIEW, victim_noise_floor_dbm=-94.0)
    assert env.status == "error"
    assert env.error is not None and env.error.startswith("compute_desense")
    assert env.metadata["missing_inputs"] == ["mechanism"]
    assert set(env.metadata["valid_mechanisms"]) == {
        "in_band",
        "out_of_band_blocker",
        "reciprocal_mixing",
    }


def test_review_case_as_out_of_band_blocker_uses_blocking_spec() -> None:
    # -15 dBm is a hypothetical receiver blocking spec at this offset.
    res = compute_desense(
        **REVIEW,
        mechanism="out_of_band_blocker",
        victim_blocking_spec_dbm=-15.0,
        victim_sensitivity_dbm=-94.0,
    )
    assert res["received_at_rx_dbm"] == pytest.approx(P_RX)
    assert res["adds_to_noise_floor"] is False
    assert res["desense_db"] is None
    assert res["blocking_margin_db"] == pytest.approx(-15.0 - P_RX)  # +14.8 dB
    assert res["desense_margin_db"] == pytest.approx(14.8)
    # Nothing in the result is the old noise-floor comparison.
    assert res["desense_margin_db"] != pytest.approx(-64.2)


def test_out_of_band_blocker_takes_worst_of_blocking_and_compression() -> None:
    res = compute_desense(
        **REVIEW,
        mechanism="out_of_band_blocker",
        victim_blocking_spec_dbm=-15.0,
        victim_p1db_in_dbm=-25.0,
    )
    assert res["compression_margin_db"] == pytest.approx(-25.0 - P_RX)  # +4.8
    assert res["desense_margin_db"] == pytest.approx(4.8)


def test_out_of_band_blocker_without_receiver_limit_is_structured_error() -> None:
    with pytest.raises(DesenseInputError) as ei:
        compute_desense(**REVIEW, victim_noise_floor_dbm=-94.0, mechanism="out_of_band_blocker")
    assert ei.value.missing == ["victim_blocking_spec_dbm|victim_p1db_in_dbm"]
    env = server.compute_desense(
        **REVIEW, victim_noise_floor_dbm=-94.0, mechanism="out_of_band_blocker"
    )
    assert env.status == "error"
    assert env.metadata["mechanism"] == "out_of_band_blocker"


def test_in_band_hand_pin_equal_power_is_3db() -> None:
    """I = N  ->  desense = 10log10(2) = 3.0103 dB."""
    res = compute_desense(
        aggressor_power_dbm=-60.0,
        filter_rejection_db=10.0,
        antenna_iso_db=30.0,
        victim_noise_floor_dbm=-100.0,
        mechanism="in_band",
    )
    assert res["i_over_n_db"] == pytest.approx(0.0)
    assert res["desense_db"] == pytest.approx(10 * math.log10(2), abs=1e-9)
    assert res["desense_margin_db"] == pytest.approx(1.0 - 10 * math.log10(2))
    assert res["adds_to_noise_floor"] is True


def test_in_band_requires_noise_floor() -> None:
    with pytest.raises(DesenseInputError) as ei:
        compute_desense(**REVIEW, mechanism="in_band")
    assert ei.value.missing == ["victim_noise_floor_dbm"]


def test_reciprocal_mixing_hand_pin() -> None:
    """I_rm = P_rx + PN + 10log10(BW) = -29.8 - 130 + 60 = -99.8 dBm;
    against N = -100 dBm the floor rises 10log10(1 + 10^0.02)."""
    res = compute_desense(
        **REVIEW,
        victim_noise_floor_dbm=-100.0,
        mechanism="reciprocal_mixing",
        lo_phase_noise_dbc_hz=-130.0,
        victim_bandwidth_hz=1e6,
    )
    assert res["reciprocal_mixing_power_dbm"] == pytest.approx(-99.8)
    assert res["desense_db"] == pytest.approx(10 * math.log10(1 + 10**0.02), abs=1e-9)


def test_reciprocal_mixing_requires_phase_noise_and_bandwidth() -> None:
    with pytest.raises(DesenseInputError) as ei:
        compute_desense(**REVIEW, victim_noise_floor_dbm=-100.0, mechanism="reciprocal_mixing")
    assert ei.value.missing == ["lo_phase_noise_dbc_hz", "victim_bandwidth_hz"]


def test_unknown_mechanism_rejected() -> None:
    with pytest.raises(DesenseInputError):
        compute_desense(**REVIEW, victim_noise_floor_dbm=-94.0, mechanism="magic")


def test_coex_matrix_in_band_rows_declare_noise_floor_assumption() -> None:
    """check_coex_matrix only scores emissions landing in the victim band
    (in-band). Without rx.noise_floor_dbm it must say it fell back to the
    sensitivity, and use noise_floor_dbm when given."""
    tx = [
        {
            "name": "HaLow",
            "f_center_hz": 915e6,
            "power_dbm": 23,
            "filtered_harmonic_dbc": {"2H": -60},
        }
    ]
    rx = [{"name": "LTE_B3_DL", "f_range_hz": [1805e6, 1880e6], "sensitivity_dbm": -97}]
    row = check_coex_matrix(tx, rx, antenna_iso_db=20)["matrix"][0]
    assert "noise_floor_dbm" in row["assumptions"]
    rx[0]["noise_floor_dbm"] = -104.0
    row = check_coex_matrix(tx, rx, antenna_iso_db=20)["matrix"][0]
    assert row["assumptions"] == {}
    # 23 - 60 - 20 = -57 dBm in-band vs -104 dBm floor.
    assert row["desense_db"] == pytest.approx(10 * math.log10(1 + 10**4.7), abs=1e-6)
