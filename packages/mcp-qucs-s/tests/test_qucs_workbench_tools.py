from __future__ import annotations

from mcp_qucs_s import server

NETLIST = """# divider
Pac:P1 in gnd Num="1" Z="50 Ohm" P="0 dBm" f="1 GHz"
R:R1 in out R="50 Ohm"
Pac:P2 out gnd Num="2" Z="50 Ohm" P="0 dBm" f="1 GHz"
.SP:SP1 Type="log" Start="1 MHz" Stop="1 GHz" Points="101"
"""


def test_qucs_workspace_parse_and_export_round_trip(tmp_path) -> None:
    source = tmp_path / "divider.net"
    source.write_text(NETLIST, encoding="utf-8")
    workspace = server.workspace_create("qucs-ir")
    assert workspace.status == "ok"
    workspace_id = workspace.data["workspace_id"]
    imported = server.artifact_import(workspace_id, str(source))
    assert imported.status == "ok"

    parsed = server.circuit_parse(
        workspace_id,
        imported.data["artifact_id"],
        "netlist",
    )
    assert parsed.status == "ok"
    assert parsed.data["is_supported"]
    assert parsed.data["components"][1]["pins"] == {"1": "in", "2": "out"}

    exported = server.circuit_export(
        workspace_id,
        parsed.data,
        "netlist",
        "roundtrip.net",
    )
    assert exported.status == "ok"
    content = server.artifact_resource(workspace_id, exported.data["artifact_id"])
    assert b"R:R1 in out" in content
