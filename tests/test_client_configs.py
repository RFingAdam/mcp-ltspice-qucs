from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_COMMANDS = {
    "mcp-ltspice",
    "mcp-qucs-s",
    "mcp-rf-analysis",
}


def _assert_uv_commands(commands: list[dict[str, object]]) -> None:
    scripts = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "scripts"
    ]
    launched: set[str] = set()
    for command in commands:
        assert command["command"] == "uv"
        args = command["args"]
        assert isinstance(args, list)
        assert all(isinstance(value, str) for value in args)
        assert "--frozen" in args
        assert "--package" not in args, "the repo is one package; --package is not needed"
        server = args[-1]
        assert server in scripts
        launched.add(server)
    assert launched == EXPECTED_COMMANDS


def test_codex_project_config_declares_required_stdio_servers() -> None:
    config = tomllib.loads((ROOT / ".codex" / "config.toml").read_text(encoding="utf-8"))
    servers = config["mcp_servers"]
    assert set(servers) == {"ltspice", "qucs_s", "rf_analysis"}
    commands = list(servers.values())
    assert all(command["required"] is True for command in commands)
    assert all(command["cwd"] == ".." for command in commands)
    _assert_uv_commands(commands)


def test_claude_project_config_declares_portable_stdio_servers() -> None:
    config = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
    servers = config["mcpServers"]
    assert set(servers) == {"ltspice", "qucs-s", "rf-analysis"}
    commands = list(servers.values())
    assert all(command["type"] == "stdio" for command in commands)
    assert all("${CLAUDE_PROJECT_DIR:-.}" in command["args"] for command in commands)
    _assert_uv_commands(commands)
