"""Generate the documented backend/CI matrix from capability declarations."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from mcp_ltspice.capabilities import SUPPORTED_ANALYSES as SPICE_ANALYSES
from mcp_qucs_s.capabilities import SUPPORTED_ANALYSES as QUCS_ANALYSES
from mcp_rf_analysis.capabilities import SUPPORTED_ANALYSES as RF_ANALYSES

ROOT = Path(__file__).resolve().parent.parent
DOCUMENT = ROOT / "docs" / "backend-matrix.md"


def rendered_matrix() -> str:
    rows = [
        (
            "LTspice",
            "mcp-ltspice",
            SPICE_ANALYSES["ltspice"],
            "Known-answer file run",
            "Scheduled native Windows + Wine self-hosted",
        ),
        (
            "ngspice",
            "mcp-ltspice",
            SPICE_ANALYSES["ngspice"],
            "Known-answer netlist run",
            "Required Linux per-commit",
        ),
        (
            "Qucsator-RF",
            "mcp-qucs-s",
            QUCS_ANALYSES["qucsator"],
            "Known-answer S-parameter run",
            "Scheduled capability-labelled self-hosted",
        ),
        (
            "Xyce",
            "mcp-qucs-s",
            QUCS_ANALYSES["xyce"],
            "Known-answer harmonic-balance run",
            "Scheduled capability-labelled self-hosted",
        ),
        (
            "Python numerical stack",
            "mcp-rf-analysis",
            RF_ANALYSES,
            "Known-answer matched-through cascade",
            "Required Linux/Windows per-commit",
        ),
    ]
    lines = [
        "| Backend | Server | Declared analyses | Readiness validation | CI tier |",
        "|---|---|---|---|---|",
    ]
    lines.extend(
        "| "
        + " | ".join(
            (
                backend,
                f"`{server}`",
                ", ".join(f"`{analysis}`" for analysis in analyses),
                validation,
                ci_tier,
            )
        )
        + " |"
        for backend, server, analyses, validation, ci_tier in rows
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = DOCUMENT.read_text(encoding="utf-8")
    updated = re.sub(
        r"<!-- BEGIN GENERATED -->.*<!-- END GENERATED -->",
        f"<!-- BEGIN GENERATED -->\n{rendered_matrix()}\n<!-- END GENERATED -->",
        text,
        flags=re.DOTALL,
    )
    if args.check:
        if updated != text:
            print(
                "backend-matrix.md is stale; run `uv run python scripts/gen_backend_matrix.py`.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        return
    DOCUMENT.write_text(updated, encoding="utf-8")


if __name__ == "__main__":
    main()
