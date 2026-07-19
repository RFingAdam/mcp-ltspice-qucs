"""Shared fixtures and simulator-availability markers."""

from __future__ import annotations

import os
import shutil

import pytest


def _have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


HAS_NGSPICE = _have("ngspice")

HAS_WINE = _have("wine") or _have("wine64")

ltspice_path = os.getenv("LTSPICE_PATH")
HAS_LTSPICE = (
    _have("LTspice")
    or _have("ltspice")
    or _have("XVIIx64.exe")
    or (
        bool(ltspice_path)
        and os.path.isfile(ltspice_path)
        and HAS_WINE
    )
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    skip_ngspice = pytest.mark.skip(reason="ngspice not installed")
    skip_ltspice = pytest.mark.skip(reason="LTspice (Wine) not installed")
    for item in items:
        if "ngspice" in item.keywords and not HAS_NGSPICE:
            item.add_marker(skip_ngspice)
        if "ltspice" in item.keywords and not HAS_LTSPICE:
            item.add_marker(skip_ltspice)
