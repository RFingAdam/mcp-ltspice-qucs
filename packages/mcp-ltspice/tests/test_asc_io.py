"""Tests for LTspice .asc generation, parsing, and value formatting."""

from __future__ import annotations

import pytest

from mcp_ltspice.asc_io import (
    from_ltspice_value,
    generate_lpf_asc,
    read_components,
    to_ltspice_value,
    update_component,
)
from mcp_ltspice.synthesis import synthesize_lc_lpf


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (4.7e-9, "4.7n"),
        (2.2e-12, "2.2p"),
        (1e-9, "1n"),
        (50.0, "50"),
        (1e6, "1Meg"),
        (1e-6, "1u"),
        (15e3, "15k"),
    ],
)
def test_to_ltspice_value(value: float, expected: str) -> None:
    assert to_ltspice_value(value) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("4.7n", 4.7e-9),
        ("2.2p", 2.2e-12),
        ("1n", 1e-9),
        ("50", 50.0),
        ("1Meg", 1e6),
        ("1MEG", 1e6),
        ("15k", 15e3),
        ("3.3u", 3.3e-6),
    ],
)
def test_from_ltspice_value(text: str, expected: float) -> None:
    assert from_ltspice_value(text) == pytest.approx(expected)


def test_round_trip_value_preserves_within_4_sigfigs() -> None:
    for v in (4.7e-9, 1.234e-12, 8.2e-9, 50.0, 1e3):
        text = to_ltspice_value(v)
        parsed = from_ltspice_value(text)
        assert parsed == pytest.approx(v, rel=1e-3)


def test_generate_lpf_asc_butterworth(tmp_path) -> None:
    design = synthesize_lc_lpf("butterworth", order=5, cutoff_hz=500e6)
    out = generate_lpf_asc(
        design.components,
        tmp_path / "lpf5.asc",
        topology="lpf_t_butterworth_chebyshev",
    )
    assert out.exists()
    text = out.read_text()
    # Schematic has the source, load, and 5 reactive elements
    assert "V1" in text and "Rs1" in text and "RL1" in text
    assert "L1" in text and "C2" in text and "L3" in text and "C4" in text and "L5" in text
    # AC directive present
    assert ".ac dec" in text


def test_generate_lpf_asc_elliptic(tmp_path) -> None:
    design = synthesize_lc_lpf(
        "elliptic",
        order=5,
        cutoff_hz=1e9,
        ripple_db=0.1,
        stopband_atten_db=30,
    )
    out = generate_lpf_asc(
        design.components,
        tmp_path / "ellip5.asc",
        topology="lpf_t_elliptic",
    )
    text = out.read_text()
    assert "L1" in text and "L2" in text and "C2" in text
    # Trap pairs use both L and C with same index
    assert text.count("InstName L2") == 1
    assert text.count("InstName C2") == 1


def test_read_components_round_trip(tmp_path) -> None:
    design = synthesize_lc_lpf("butterworth", order=3, cutoff_hz=1e9)
    out = generate_lpf_asc(design.components, tmp_path / "rt.asc")
    parsed = read_components(out)
    # Refdes set should match (excluding source / load resistors)
    expected_keys = set(design.components.keys())
    assert expected_keys.issubset(set(parsed.keys()))
    # Values should round-trip within 0.1%
    for k, v in design.components.items():
        assert parsed[k] == pytest.approx(v, rel=1e-3)


def test_update_component_changes_value_in_place(tmp_path) -> None:
    design = synthesize_lc_lpf("butterworth", order=3, cutoff_hz=1e9)
    out = generate_lpf_asc(design.components, tmp_path / "upd.asc")
    update_component(out, "L1", 3.3e-9)
    parsed = read_components(out)
    assert parsed["L1"] == pytest.approx(3.3e-9, rel=1e-3)
    # Other components unchanged
    assert parsed["C2"] == pytest.approx(design.components["C2"], rel=1e-3)
