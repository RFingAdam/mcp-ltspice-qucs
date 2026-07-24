from __future__ import annotations

import shutil

import pytest

from mcp_ltspice.asc_io import generate_lpf_asc
from mcp_ltspice.server import extract_sparameters
from mcp_qucs_s.netlist import generate_ladder_netlist
from mcp_qucs_s.runner import run_qucs
from mcp_qucs_s.sparams import network_from_dat
from rf_mcp_common.backend import (
    ResultAxis,
    ResultDataset,
    compare_datasets,
    trace_from_array,
)

HAS_NGSPICE = shutil.which("ngspice") is not None
HAS_QUCS = shutil.which("qucsator_rf") is not None or shutil.which("qucsator") is not None


def _dataset(backend: str, network) -> ResultDataset:
    traces = {}
    for output_port in range(2):
        for input_port in range(2):
            name = f"S[{output_port + 1},{input_port + 1}]"
            traces[name] = trace_from_array(
                name,
                network.s[:, output_port, input_port],
                unit="1",
                quantity="sparameter",
            )
    return ResultDataset(
        backend=backend,  # type: ignore[arg-type]
        analysis="sparameters",
        axis=ResultAxis(name="frequency", unit="Hz", values=network.f.tolist()),
        traces=traces,
        method="independent_known_answer_simulator",
    )


@pytest.mark.skipif(
    not (HAS_NGSPICE and HAS_QUCS),
    reason="ngspice and qucsator are both required",
)
@pytest.mark.integration
def test_equivalent_asymmetric_two_port_agrees_on_ngspice_and_qucsator(tmp_path) -> None:
    """One explicit topology, two unrelated engines, all four S terms."""
    components = {"L1": 10e-9, "C2": 2e-12}
    asc = generate_lpf_asc(
        components,
        tmp_path / "network.asc",
        f_start_hz=1e7,
        f_stop_hz=3e9,
        npoints_per_decade=100,
    )
    ng_result = extract_sparameters(
        asc_path=str(asc),
        output_s2p=str(tmp_path / "ngspice.s2p"),
        prefer="ngspice",
    )
    assert ng_result.status == "ok", ng_result.error

    import skrf as rf

    ngspice = rf.Network(ng_result.data["s2p_path"])
    qucs_netlist = generate_ladder_netlist(
        [
            ("series_l", {"L": components["L1"]}),
            ("shunt_c", {"C": components["C2"]}),
        ],
        tmp_path / "network.net",
        f_start_hz=1e7,
        f_stop_hz=3e9,
        points=251,
        sweep="log",
    )
    qucs_run = run_qucs(
        qucs_netlist,
        output_path=tmp_path / "qucs.dat",
        workspace_root=tmp_path / "qucs-runs",
    )
    qucsator = network_from_dat(qucs_run.output_path)

    comparison = compare_datasets(
        _dataset("ngspice", ngspice),
        _dataset("qucsator", qucsator),
    )
    assert comparison.passed, comparison.model_dump()
    assert {trace.trace for trace in comparison.traces} == {
        "S[1,1]",
        "S[1,2]",
        "S[2,1]",
        "S[2,2]",
    }
