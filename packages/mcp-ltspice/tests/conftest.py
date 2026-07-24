"""Shared fixtures and simulator-availability markers."""

from __future__ import annotations

import pytest

from mcp_ltspice.capabilities import probe_spice_backend

# Integration markers mean the backend completed its tiny known-answer run,
# not merely that a binary exists. This prevents a broken Wine prefix,
# first-run dialog, or unlaunchable install from turning an ordinary local
# suite into a false product failure while the capability diagnostic retains
# the precise reason.
HAS_NGSPICE = bool(probe_spice_backend("ngspice", timeout_sec=20.0)["validated"])
HAS_LTSPICE = bool(probe_spice_backend("ltspice", timeout_sec=30.0)["validated"])


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    skip_ngspice = pytest.mark.skip(reason="ngspice is not known-answer validated")
    skip_ltspice = pytest.mark.skip(reason="LTspice is not known-answer validated")
    for item in items:
        # get_closest_marker, not `"ngspice" in item.keywords`: keywords also
        # contains test names and parametrize ids, so a case parametrized over
        # the string "ngspice" was being skipped as though it needed the binary.
        if item.get_closest_marker("ngspice") and not HAS_NGSPICE:
            item.add_marker(skip_ngspice)
        if item.get_closest_marker("ltspice") and not HAS_LTSPICE:
            item.add_marker(skip_ltspice)
