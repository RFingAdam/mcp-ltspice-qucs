"""Regenerate the "At a glance" section of docs/tool-catalog.md from the
live servers, so the catalog can never drift from reality again.

    uv run python scripts/gen_tool_catalog.py

Everything between the BEGIN/END GENERATED markers is replaced; the
hand-written envelope / see-also sections are left alone.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "docs" / "tool-catalog.md"

SERVERS = [
    ("mcp-ltspice", "mcp_ltspice.server"),
    ("mcp-qucs-s", "mcp_qucs_s.server"),
    ("mcp-rf-analysis", "mcp_rf_analysis.server"),
]


def first_sentence(text: str | None) -> str:
    if not text:
        return ""
    flat = " ".join(text.split())
    m = re.match(r"(.+?[.!?])(\s|$)", flat)
    out = m.group(1) if m else flat
    return (out[:157] + "…") if len(out) > 160 else out


def main() -> None:
    import importlib

    blocks: list[str] = []
    total = 0
    for label, module in SERVERS:
        mcp = importlib.import_module(module).mcp
        tools = [t for t in asyncio.run(mcp.list_tools()) if "." not in t.name]
        total += len(tools)
        rows = "\n".join(
            f"    | `{t.name}` | {first_sentence(t.description)} |"
            for t in sorted(tools, key=lambda t: t.name)
        )
        blocks.append(
            f'=== "{label} ({len(tools)} tools)"\n\n    | Tool | Purpose |\n    |---|---|\n{rows}\n'
        )

    generated = (
        f"Three servers, **{total} tools** total. Frequencies are always Hz on the\n"
        "wire; every tool returns the [Envelope](reference/envelope.md) shape.\n"
        "`mcp-ltspice` additionally registers namespaced aliases "
        "(`filter.*`, `power.*`, `analog.*`, `digital.*`, `vendor.*`, `sim.*`) "
        "for every primary tool; only primaries are listed here.\n\n"
        "## At a glance\n\n"
        "*(This section is generated — run `uv run python scripts/gen_tool_catalog.py`\n"
        "after adding or changing tools.)*\n\n" + "\n".join(blocks)
    )

    text = CATALOG.read_text(encoding="utf-8")
    text = re.sub(
        r"<!-- BEGIN GENERATED -->.*<!-- END GENERATED -->",
        f"<!-- BEGIN GENERATED -->\n{generated}\n<!-- END GENERATED -->",
        text,
        flags=re.DOTALL,
    )
    CATALOG.write_text(text, encoding="utf-8")
    print(f"tool-catalog.md regenerated: {total} tools")


if __name__ == "__main__":
    main()
