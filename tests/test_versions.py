"""Every public version surface must come from installed package metadata."""

from __future__ import annotations

from importlib import import_module
from importlib.metadata import version

import pytest

from rf_mcp_common import __version__ as common_version
from rf_mcp_common.envelope import error, ok


@pytest.mark.parametrize(
    ("distribution", "module_name", "server_module_name"),
    [
        ("mcp-ltspice", "mcp_ltspice", "mcp_ltspice.server"),
        ("mcp-qucs-s", "mcp_qucs_s", "mcp_qucs_s.server"),
        ("mcp-rf-analysis", "mcp_rf_analysis", "mcp_rf_analysis.server"),
    ],
)
def test_package_module_and_fastmcp_server_versions_agree(
    distribution: str,
    module_name: str,
    server_module_name: str,
) -> None:
    expected = version(distribution)
    package = import_module(module_name)
    server = import_module(server_module_name)

    assert package.__version__ == expected
    assert server.__version__ == expected
    assert server.mcp.version == expected


def test_common_module_and_default_envelopes_use_common_distribution_version() -> None:
    expected = version("rf-mcp-common")
    assert common_version == expected
    assert ok({"value": 1}).metadata["tool_version"] == expected
    assert error("failure").metadata["tool_version"] == expected
