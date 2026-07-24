"""Keep user-facing tool claims synchronized with the live MCP registries."""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from mcp_ltspice.server import mcp as ltspice_mcp
from mcp_qucs_s.server import mcp as qucs_mcp
from mcp_rf_analysis.server import mcp as rf_mcp

ROOT = Path(__file__).resolve().parents[1]


def _primary_count(mcp: Any) -> int:
    return sum("." not in tool.name for tool in asyncio.run(mcp.list_tools()))


def _json_after(text: str, marker: str) -> dict[str, Any]:
    match = re.search(
        rf"{re.escape(marker)}.*?```json\n(.*?)\n```",
        text,
        flags=re.DOTALL,
    )
    assert match is not None, f"no JSON example found after {marker!r}"
    parsed = json.loads(match.group(1))
    assert isinstance(parsed, dict)
    return parsed


def test_documented_primary_tool_counts_match_live_servers() -> None:
    counts = {
        "mcp-ltspice": _primary_count(ltspice_mcp),
        "mcp-qucs-s": _primary_count(qucs_mcp),
        "mcp-rf-analysis": _primary_count(rf_mcp),
    }
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    docs_index = (ROOT / "docs" / "index.md").read_text(encoding="utf-8")

    for server, count in counts.items():
        assert re.search(rf"\*\*`{re.escape(server)}`\*\*\s+\|\s+{count}\s+\|", readme)
        assert re.search(rf"\[`{re.escape(server)}`\].*?\|\s*{count}\s*\|", docs_index)


def test_usage_json_examples_match_live_tool_schemas() -> None:
    usage = (ROOT / "docs" / "usage.md").read_text(encoding="utf-8")
    tools = {tool.name: tool for tool in asyncio.run(ltspice_mcp.list_tools())}

    examples = {
        "synthesize_lc_filter": _json_after(usage, "calls `synthesize_lc_filter`"),
        "substitute_real_components": _json_after(usage, "calls `substitute_real_components`"),
        "evaluate_filter_spec": _json_after(usage, "calls `evaluate_filter_spec`"),
    }
    for name, example in examples.items():
        schema = tools[name].parameters
        properties = set(schema["properties"])
        required = set(schema.get("required", []))
        assert set(example) <= properties, f"{name} example has unknown arguments"
        assert required <= set(example), f"{name} example omits required arguments"


def test_readme_headline_workflow_names_are_registered_tools() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    workflow_table = readme.split("## What it solves", 1)[1].split("Five worked examples", 1)[0]
    documented = set(re.findall(r"`([a-z][a-z0-9_.]+)`", workflow_table))
    registered = {
        tool.name
        for mcp in (ltspice_mcp, qucs_mcp, rf_mcp)
        for tool in asyncio.run(mcp.list_tools())
    }
    assert documented <= registered, f"unknown headline tools: {sorted(documented - registered)}"


def test_generated_tool_catalog_is_current() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/gen_tool_catalog.py", "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_generated_backend_matrix_is_current() -> None:
    result = subprocess.run(
        [sys.executable, "scripts/gen_backend_matrix.py", "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_current_guides_do_not_restore_known_stale_claims() -> None:
    paths = [
        ROOT / "README.md",
        ROOT / "ARCHITECTURE.md",
        ROOT / "docs" / "index.md",
        ROOT / "docs" / "getting-started.md",
        ROOT / "docs" / "installation.md",
        ROOT / "docs" / "suite-architecture.md",
        ROOT / "docs" / "usage.md",
        ROOT / "packages" / "mcp-ltspice" / "README.md",
        ROOT / "packages" / "mcp-qucs-s" / "README.md",
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    stale = [
        "876 passing tests",
        "~180 tests",
        "examples/halow_lpf/design.py",
        "synthesize_sallen_key_lpf",
        "microstrip_synth",
        'error("not yet implemented")',
        "The three servers don't import each other",
        "replaces ideal `L`/`C` with vendor SPICE subcircuits",
    ]
    for claim in stale:
        assert claim not in text
