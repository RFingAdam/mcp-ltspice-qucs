"""Placeholder FastMCP entry for mcp-qucs-s.

Tools are not yet implemented. Running this server right now will respond
to ``list_tools`` with a single ``status`` tool that reports the
implementation status.
"""

from __future__ import annotations

from fastmcp import FastMCP

from mcp_qucs_s import __version__
from rf_mcp_common.envelope import Envelope, ok

mcp = FastMCP(name="mcp-qucs-s", version=__version__)


@mcp.tool(description="Report mcp-qucs-s implementation status (scaffold only).")
def status() -> Envelope[dict[str, str]]:
    return ok(
        {
            "version": __version__,
            "implementation_state": "scaffold",
            "planned_tools": (
                "run_sp_analysis, run_harmonic_balance, "
                "synthesize_microstrip_line, synthesize_microstrip_filter, "
                "synthesize_coupler, extract_noise_parameters, "
                "lumped_to_distributed, export_touchstone"
            ),
            "tracking_issue": "https://github.com/RFingAdam/mcp-ltspice-qucs/issues",
        },
        tool_version=__version__,
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
