"""Each published package carries its own copy of the repository licence."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("package", sorted(p.name for p in (ROOT / "packages").iterdir()))
def test_package_license_matches_repository(package: str) -> None:
    packaged = ROOT / "packages" / package / "LICENSE"
    assert packaged.read_bytes() == (ROOT / "LICENSE").read_bytes()
