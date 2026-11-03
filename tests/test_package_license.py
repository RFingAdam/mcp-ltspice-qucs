"""The published distribution carries the repository licence."""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_distribution_ships_repository_license() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert project["license"] == "AGPL-3.0-or-later"
    assert project["license-files"] == ["LICENSE"]
    assert (ROOT / "LICENSE").is_file()
