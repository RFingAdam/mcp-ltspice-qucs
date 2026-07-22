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


class TestRunHarmonicBalance:
    def test_returns_error_envelope_when_xyce_missing(self):
        """Without Xyce, the tool's pre-existing 'Xyce not installed' error path applies."""
        if is_xyce_available():
            pytest.skip("Xyce is installed; the missing-Xyce path can't be exercised here")
        env = _call(
            "run_harmonic_balance",
            netlist_path="dummy.net",
            fundamentals_hz=[1e9],
            harmonics=5,
            input_power_dbm=0.0,
        )
        assert env.status == "error"
        assert "Xyce" in env.error

    def test_returns_error_envelope_when_xyce_available(self):
        """With Xyce installed, the tool should NOT return ok() with a placeholder.
        Even when Xyce can be detected, the implementation is incomplete and the
        envelope must signal that explicitly via status='error'.
        """
        if not is_xyce_available():
            pytest.skip("Xyce not installed; this test verifies the with-Xyce path")
        env = _call(
            "run_harmonic_balance",
            netlist_path="dummy.net",
            fundamentals_hz=[1e9],
            harmonics=5,
            input_power_dbm=0.0,
        )
        assert env.status == "error"
        assert "not yet implemented" in env.error.lower()


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

    def test_run_harmonic_balance_never_returns_ok_placeholder(self):
        env = _call(
            "run_harmonic_balance",
            netlist_path="dummy.net",
            fundamentals_hz=[1e9],
            harmonics=5,
            input_power_dbm=0.0,
        )
        # Whether Xyce is installed or not, this tool is currently not
        # implemented end-to-end; it must surface that as an error envelope.
        assert env.status == "error", (
            f"run_harmonic_balance returned status={env.status}; "
            "scaffolded tools must return status='error' until fully implemented."
        )

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
