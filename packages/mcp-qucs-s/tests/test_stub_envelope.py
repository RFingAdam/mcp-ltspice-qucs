"""Verify scaffolded tools return error() envelopes, not ok() with placeholders.

Calling agents must be able to tell the difference between "tool ran and
returned an answer" and "tool is not yet implemented." Earlier versions
returned ok() with a `note: "scaffolded"` field, which agents could
mistake for success. These tools now return error() with a clear
"not yet implemented" message.
"""

from __future__ import annotations

import pytest

from mcp_qucs_s import server
from mcp_qucs_s.runner import is_qucs_available, is_xyce_available


def _call(tool_name: str, /, **kwargs):
    """Call a registered MCP tool by its function name and return the envelope."""
    fn = getattr(server, tool_name)
    return fn(**kwargs)


# run_harmonic_balance is no longer scaffolded — it is implemented against
# Xyce and validated in test_harmonic_balance.py, including IIP3 against a
# closed-form cubic. Only its missing-Xyce path belongs in this file now.
HB_DUT = ["Rin in 0 50", "Bnl out 0 V={1.0*V(in) + 10.0*V(in)*V(in)*V(in)}"]


class TestRunHarmonicBalance:
    def test_returns_error_envelope_when_xyce_missing(self, monkeypatch):
        """Missing Xyce must be an error envelope with an actionable hint."""
        monkeypatch.setattr("mcp_qucs_s.server.is_xyce_available", lambda: False)
        env = _call(
            "run_harmonic_balance",
            dut_netlist=HB_DUT,
            fundamentals_hz=[1e9],
            harmonics=5,
            input_power_dbm=-20.0,
        )
        assert env.status == "error"
        assert "Xyce" in env.error
        assert "installation.md" in env.error

    def test_is_no_longer_a_placeholder_when_xyce_is_present(self):
        """The stub used to return error('not yet implemented') regardless."""
        if not is_xyce_available():
            pytest.skip("Xyce not installed; this test verifies the with-Xyce path")
        env = _call(
            "run_harmonic_balance",
            dut_netlist=HB_DUT,
            fundamentals_hz=[1e9],
            harmonics=5,
            input_power_dbm=-20.0,
        )
        assert env.status == "ok", env.error
        assert "fundamental_dbm" in env.data


class TestExtractNoiseParameters:
    def test_returns_error_envelope_when_qucs_missing(self):
        if is_qucs_available():
            pytest.skip("Qucs-S is installed; missing-Qucs path not testable here")
        env = _call(
            "extract_noise_parameters",
            netlist_path="dummy.net",
            f_start_hz=1e9,
            f_stop_hz=10e9,
        )
        assert env.status == "error"
        assert "Qucs-S" in env.error

    def test_returns_error_envelope_when_qucs_available(self):
        if not is_qucs_available():
            pytest.skip("Qucs-S not installed; this test verifies the with-Qucs path")
        env = _call(
            "extract_noise_parameters",
            netlist_path="dummy.net",
            f_start_hz=1e9,
            f_stop_hz=10e9,
        )
        assert env.status == "error"
        assert "not yet implemented" in env.error.lower()


class TestNoOkPlaceholders:
    """Regression: the scaffolded tools must NEVER return ok() with placeholder data.

    This is a contract test: if someone re-introduces the 'note: scaffolded'
    pattern, this test catches it.
    """

    def test_extract_noise_parameters_never_returns_ok_placeholder(self):
        env = _call(
            "extract_noise_parameters",
            netlist_path="dummy.net",
            f_start_hz=1e9,
            f_stop_hz=10e9,
        )
        assert env.status == "error", (
            f"extract_noise_parameters returned status={env.status}; "
            "scaffolded tools must return status='error' until fully implemented."
        )
