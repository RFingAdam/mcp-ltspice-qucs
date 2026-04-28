"""Tests for substrate presets and microstrip loss calculation."""

from __future__ import annotations

import pytest

from mcp_qucs_s.microstrip import analyze_microstrip, synthesize_microstrip_line
from mcp_qucs_s.substrates import (
    SUBSTRATE_PRESETS,
    get_substrate,
    list_substrate_presets,
)


class TestSubstratePresets:
    def test_at_least_5_presets_available(self):
        """The catalogue should cover the common substrate families."""
        assert len(SUBSTRATE_PRESETS) >= 5

    def test_required_presets_present(self):
        for name in [
            "FR4_0254",
            "Rogers4350B_0508",
            "Rogers4003C_0508",
            "Duroid5880_0508",
        ]:
            assert name in SUBSTRATE_PRESETS, f"Missing preset {name!r}"

    def test_get_substrate_returns_dataclass(self):
        sub = get_substrate("Rogers4350B_0508")
        assert sub.er == pytest.approx(3.66)
        assert sub.h_mm == pytest.approx(0.508)
        assert sub.tan_d == pytest.approx(0.0037)

    def test_unknown_preset_raises(self):
        with pytest.raises(KeyError, match="Unknown substrate"):
            get_substrate("nonexistent_substrate")

    def test_list_includes_descriptions(self):
        listed = list_substrate_presets()
        assert all("description" in p for p in listed)
        assert all("er" in p and "h_mm" in p and "tan_d" in p for p in listed)


class TestMicrostripLoss:
    def test_loss_fields_present(self):
        sub = get_substrate("Rogers4350B_0508")
        line = synthesize_microstrip_line(50.0, 90, 5e9, sub)
        a = analyze_microstrip(line.width_mm, sub, freq_hz=5e9)
        assert "alpha_d_db_per_mm" in a
        assert "alpha_c_db_per_mm" in a
        assert "alpha_total_db_per_mm" in a
        # Loss is positive
        assert a["alpha_d_db_per_mm"] > 0
        assert a["alpha_c_db_per_mm"] > 0
        # Total = sum
        assert a["alpha_total_db_per_mm"] == pytest.approx(
            a["alpha_d_db_per_mm"] + a["alpha_c_db_per_mm"], rel=1e-9
        )

    def test_fr4_loss_higher_than_rogers(self):
        """FR-4 has tan_d ~0.020 vs Rogers RO4350B's 0.0037 — same Z₀
        line should be lossier on FR-4."""
        a_fr4 = analyze_microstrip(
            synthesize_microstrip_line(50.0, 90, 5e9, get_substrate("FR4_0254")).width_mm,
            get_substrate("FR4_0254"),
            freq_hz=5e9,
        )
        a_rog = analyze_microstrip(
            synthesize_microstrip_line(50.0, 90, 5e9, get_substrate("Rogers4350B_0508")).width_mm,
            get_substrate("Rogers4350B_0508"),
            freq_hz=5e9,
        )
        assert a_fr4["alpha_d_db_per_mm"] > a_rog["alpha_d_db_per_mm"]

    def test_duroid_lowest_loss(self):
        """Duroid 5880 (PTFE-glass) should have the lowest loss of the lot."""
        loss_per_substrate = {}
        for name in ["FR4_0254", "Rogers4350B_0508", "Rogers4003C_0508", "Duroid5880_0508"]:
            sub = get_substrate(name)
            line = synthesize_microstrip_line(50.0, 90, 5e9, sub)
            a = analyze_microstrip(line.width_mm, sub, freq_hz=5e9)
            loss_per_substrate[name] = a["alpha_total_db_per_mm"]
        assert (
            loss_per_substrate["Duroid5880_0508"]
            < loss_per_substrate["Rogers4003C_0508"]
            < loss_per_substrate["Rogers4350B_0508"]
            < loss_per_substrate["FR4_0254"]
        )

    def test_loss_rises_with_frequency(self):
        """Both dielectric and conductor losses are monotone-increasing in frequency."""
        sub = get_substrate("Rogers4350B_0508")
        line = synthesize_microstrip_line(50.0, 90, 5e9, sub)
        a_low = analyze_microstrip(line.width_mm, sub, freq_hz=1e9)
        a_high = analyze_microstrip(line.width_mm, sub, freq_hz=10e9)
        assert a_high["alpha_d_db_per_mm"] > a_low["alpha_d_db_per_mm"]
        assert a_high["alpha_c_db_per_mm"] > a_low["alpha_c_db_per_mm"]

    def test_aluminium_more_lossy_than_copper(self):
        """σ_aluminium (3.5e7) < σ_copper (5.8e7), so conductor loss is higher."""
        sub = get_substrate("Rogers4350B_0508")
        line = synthesize_microstrip_line(50.0, 90, 5e9, sub)
        a_cu = analyze_microstrip(line.width_mm, sub, freq_hz=5e9, sigma_s_per_m=5.8e7)
        a_al = analyze_microstrip(line.width_mm, sub, freq_hz=5e9, sigma_s_per_m=3.5e7)
        assert a_al["alpha_c_db_per_mm"] > a_cu["alpha_c_db_per_mm"]
