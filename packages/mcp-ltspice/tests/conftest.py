"""Shared fixtures and simulator-availability markers."""

from __future__ import annotations

import shutil

import pytest

from mcp_ltspice.runner import find_ltspice


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


HAS_NGSPICE = _have("ngspice")

# Ask the runner itself rather than re-probing $PATH here. The runner also
# honours $LTSPICE_PATH, $WINEPREFIX, and the standard Windows / macOS / Wine
# install locations — a conftest that only checked $PATH silently skipped the
# ltspice-marked tests on machines where the runner would have found LTspice.
HAS_LTSPICE = find_ltspice() is not None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    skip_ngspice = pytest.mark.skip(reason="ngspice not installed")
    skip_ltspice = pytest.mark.skip(reason="LTspice (Wine) not installed")
    for item in items:
        # get_closest_marker, not `"ngspice" in item.keywords`: keywords also
        # contains test names and parametrize ids, so a case parametrized over
        # the string "ngspice" was being skipped as though it needed the binary.
        if item.get_closest_marker("ngspice") and not HAS_NGSPICE:
            item.add_marker(skip_ngspice)
        if item.get_closest_marker("ltspice") and not HAS_LTSPICE:
            item.add_marker(skip_ltspice)
