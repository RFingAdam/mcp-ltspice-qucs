"""Package-level smoke test.

The mcp-ltspice-qucs repo builds one distribution, ``mcp-ltspice-qucs``,
from four import packages kept under ``packages/``:

    packages/rf-mcp-common/src/rf_mcp_common
    packages/mcp-ltspice/src/mcp_ltspice
    packages/mcp-qucs-s/src/mcp_qucs_s
    packages/mcp-rf-analysis/src/mcp_rf_analysis

Each module has its own pytest suite under ``packages/*/tests/``. This
top-level smoke test verifies that all four modules import, that the one
distribution is installed, and that it declares every server command.
"""

from __future__ import annotations

import importlib
from importlib.metadata import distribution

MODULES = ["rf_mcp_common", "mcp_ltspice", "mcp_qucs_s", "mcp_rf_analysis"]
COMMANDS = {
    "mcp-ltspice": "mcp_ltspice.server:main",
    "mcp-qucs-s": "mcp_qucs_s.server:main",
    "mcp-rf-analysis": "mcp_rf_analysis.server:main",
}


def test_all_modules_importable() -> None:
    """Every module in the distribution imports without error."""
    for module_name in MODULES:
        mod = importlib.import_module(module_name)
        assert mod is not None, f"{module_name} imported as None"


def test_distribution_installed_with_every_server_command() -> None:
    """The one distribution is installed and declares all three servers."""
    dist = distribution("mcp-ltspice-qucs")
    assert dist.version
    scripts = {e.name: e.value for e in dist.entry_points if e.group == "console_scripts"}
    assert scripts == COMMANDS
