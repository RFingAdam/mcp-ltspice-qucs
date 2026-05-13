"""Workspace-level smoke test.

The mcp-ltspice-qucs repo is a uv workspace with four packages:

    packages/rf-mcp-common
    packages/mcp-ltspice
    packages/mcp-qucs-s
    packages/mcp-rf-analysis

Each package has its own pytest suite under `packages/*/tests/`. This
top-level smoke test just verifies the workspace itself is consistent:
all four packages are importable and report a version. Detailed unit
tests live alongside the package they cover.

Run all tests across the workspace:

    uv run pytest packages/
"""
from __future__ import annotations

import importlib
from importlib.metadata import PackageNotFoundError, version as pkg_version

PACKAGES = [
    ("rf_mcp_common", "rf-mcp-common"),
    ("mcp_ltspice", "mcp-ltspice"),
    ("mcp_qucs_s", "mcp-qucs-s"),
    ("mcp_rf_analysis", "mcp-rf-analysis"),
]


def test_all_workspace_packages_importable():
    """Every workspace package imports without error."""
    for module_name, _ in PACKAGES:
        mod = importlib.import_module(module_name)
        assert mod is not None, f"{module_name} imported as None"


def test_all_workspace_packages_installed():
    """Every workspace package is installed (metadata resolves)."""
    missing = []
    for _, dist_name in PACKAGES:
        try:
            v = pkg_version(dist_name)
            assert v, f"{dist_name} reports empty version"
        except PackageNotFoundError:
            missing.append(dist_name)
    assert not missing, f"workspace packages not installed: {missing}"
