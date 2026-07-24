"""Generate compact hashes of every registered MCP tool contract."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "tests" / "tool_contract_snapshot.json"
SERVERS = {
    "mcp-ltspice": "mcp_ltspice.server",
    "mcp-qucs-s": "mcp_qucs_s.server",
    "mcp-rf-analysis": "mcp_rf_analysis.server",
}


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=False)
    return value


def build_snapshot() -> dict[str, Any]:
    result: dict[str, Any] = {"schema_version": 1, "servers": {}}
    for server_name, module_name in SERVERS.items():
        mcp = importlib.import_module(module_name).mcp
        tools = asyncio.run(mcp.list_tools())
        entries = []
        for tool in sorted(tools, key=lambda item: item.name):
            contract = {
                "description": tool.description,
                "parameters": tool.parameters,
                "output_schema": tool.output_schema,
                "annotations": _jsonable(tool.annotations),
                "meta": tool.meta,
            }
            serialized = json.dumps(
                contract,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            entries.append(
                {
                    "name": tool.name,
                    "sha256": hashlib.sha256(serialized).hexdigest(),
                    "deprecated": bool((tool.meta or {}).get("deprecated", False)),
                    "canonical_name": (tool.meta or {}).get("canonical_name"),
                }
            )
        result["servers"][server_name] = entries
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    rendered = json.dumps(build_snapshot(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not SNAPSHOT.is_file() or SNAPSHOT.read_text(encoding="utf-8") != rendered:
            raise SystemExit(
                "tool contract snapshot is stale; run python scripts/gen_tool_contract_snapshot.py"
            )
        return
    SNAPSHOT.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
