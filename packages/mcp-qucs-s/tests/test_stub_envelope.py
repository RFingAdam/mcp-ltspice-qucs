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


# extract_noise_parameters is implemented too, and validated in test_noise.py
# against the exact identity NF = insertion loss for a passive pad. Only the
# missing-Qucs path belongs here now.
NOISE_DUT = ['R:R1 _p1 _p2 R="20"', 'R:R2 _p2 gnd R="100"']


class TestExtractNoiseParameters:
    def test_returns_error_envelope_when_qucs_missing(self, monkeypatch):
        monkeypatch.setattr("mcp_qucs_s.server.is_qucs_available", lambda: False)
        env = _call(
            "extract_noise_parameters",
            dut_netlist=NOISE_DUT,
            f_start_hz=1e9,
            f_stop_hz=10e9,
        )
        assert env.status == "error"
        assert "Qucs-S" in env.error
        assert "installation.md" in env.error

    def test_is_no_longer_a_placeholder_when_qucs_is_present(self):
        if not is_qucs_available():
            pytest.skip("Qucs-S not installed; this test verifies the with-Qucs path")
        env = _call(
            "extract_noise_parameters",
            dut_netlist=NOISE_DUT,
            f_start_hz=1e9,
            f_stop_hz=10e9,
            points=3,
        )
        assert env.status == "ok", env.error
        assert env.data["parameters"], "expected per-frequency noise parameters"


class TestNoOkPlaceholders:
    """Regression: a scaffolded tool must never return ok() with placeholder data.

    Both tools this file originally guarded are now implemented, so what is
    left is the contract itself: any *future* scaffold must return an error
    envelope rather than ok() with a 'note: scaffolded' field, which a calling
    agent would read as success.
    """

    def test_no_tool_returns_a_scaffolded_placeholder(self):
        import mcp_qucs_s.server as server_mod

        for name in dir(server_mod):
            tool = getattr(server_mod, name)
            fn = getattr(tool, "fn", None)
            if fn is None or not callable(fn):
                continue
            doc = (fn.__doc__ or "") + (getattr(tool, "description", "") or "")
            assert "scaffolded" not in doc.lower(), (
                f"{name} still advertises itself as scaffolded; implement it or "
                "have it return an error envelope."
            )
