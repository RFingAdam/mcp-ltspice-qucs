from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "gen_tool_contract_snapshot",
    ROOT / "scripts" / "gen_tool_contract_snapshot.py",
)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
SNAPSHOT = MODULE.SNAPSHOT
build_snapshot = MODULE.build_snapshot


def test_registered_tool_contract_matches_reviewed_snapshot() -> None:
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert build_snapshot() == expected


def test_dotted_aliases_are_explicitly_deprecated() -> None:
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    aliases = [tool for tool in snapshot["servers"]["mcp-ltspice"] if "." in tool["name"]]
    assert aliases
    assert all(tool["deprecated"] for tool in aliases)
    assert all(tool["canonical_name"] for tool in aliases)
