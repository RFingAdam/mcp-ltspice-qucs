"""Model-preserving vendor realization and simulator handoff."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pytest
import skrf as rf

from mcp_ltspice.analysis_context import FilterAnalysisContext
from mcp_ltspice.extract import ladder_sparams_from_components
from mcp_ltspice.realized_netlist import (
    generate_realized_filter_netlist,
    generate_realized_lpf_netlist,
)
from mcp_ltspice.synthesis import (
    Topology,
    synthesize_lc_bpf,
    synthesize_lc_bsf,
    synthesize_lc_hpf,
    synthesize_lc_lpf,
)
from mcp_ltspice.vendor_fetch import register_user_vendor_dir
from mcp_ltspice.vendor_models import (
    _USER_COMPONENT_MODELS,
    _USER_VENDOR_TABLES,
    substitute_real_components,
)


@pytest.fixture(autouse=True)
def _clean_user_models():
    _USER_VENDOR_TABLES.clear()
    _USER_COMPONENT_MODELS.clear()
    yield
    _USER_VENDOR_TABLES.clear()
    _USER_COMPONENT_MODELS.clear()


def test_curated_realization_embeds_loss_srf_and_checksums(tmp_path: Path) -> None:
    selected = substitute_real_components({"L1": 4.7e-9})
    netlist, manifest = generate_realized_lpf_netlist(selected, tmp_path / "realized.cir")
    text = netlist.read_text(encoding="utf-8")
    record = manifest.read_text(encoding="utf-8")

    assert "Rloss" in text and "Lmain" in text and "Cpar" in text
    assert selected["L1"]["model"]["checksum_sha256"] in record
    assert selected["L1"]["model"]["model_kind"] == "lumped_approximation"


def test_ambiguous_lib_pin_map_blocks_registration(tmp_path: Path) -> None:
    (tmp_path / "part_L_10n.lib").write_text(
        ".subckt THREE_PIN a b shield\nL1 a b 10n\n.ends THREE_PIN\n",
        encoding="utf-8",
    )
    result = register_user_vendor_dir(tmp_path, namespace="ambiguous")
    assert result["n_indexed"] == 0
    assert "pin map is required" in result["errors"][0]["error"]


def _design_for_kind(kind: str, topology: Topology):
    if kind == "lowpass":
        return synthesize_lc_lpf("butterworth", 3, 1e9, topology=topology)
    if kind == "highpass":
        return synthesize_lc_hpf("butterworth", 3, 1e9, topology=topology)
    if kind == "bandpass":
        return synthesize_lc_bpf("butterworth", 3, 700e6, 1.3e9, topology=topology)
    return synthesize_lc_bsf("butterworth", 3, 700e6, 1.3e9, topology=topology)


@pytest.mark.parametrize("kind", ["lowpass", "highpass", "bandpass", "bandstop"])
@pytest.mark.parametrize("topology", list(Topology))
def test_realized_netlist_supports_all_kinds_and_topologies(
    tmp_path: Path, kind: str, topology: Topology
) -> None:
    design = _design_for_kind(kind, topology)
    selected = substitute_real_components(design.components, max_value_drift_pct=None)
    netlist, manifest = generate_realized_filter_netlist(
        selected,
        tmp_path / f"{kind}_{topology.value}.cir",
        kind=kind,
        topology=topology,
    )
    text = netlist.read_text(encoding="utf-8")
    record = manifest.read_text(encoding="utf-8")
    assert "V1 src1 0 AC 1" in text
    assert ".ac dec" in text
    assert f'"kind": "{kind}"' in record
    assert f'"topology": "{topology.value}"' in record


@pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")
@pytest.mark.ngspice
@pytest.mark.integration
@pytest.mark.parametrize("kind", ["lowpass", "highpass", "bandpass", "bandstop"])
@pytest.mark.parametrize("topology", list(Topology))
def test_realized_ngspice_matches_approximate_model_for_topology_matrix(
    tmp_path: Path, kind: str, topology: Topology
) -> None:
    from mcp_ltspice.server import simulate_realized_filter

    design = _design_for_kind(kind, topology)
    result = simulate_realized_filter(
        design.components,
        str(tmp_path / f"{kind}_{topology.value}.s2p"),
        kind=kind,
        topology=topology.value,
        prefer="ngspice",
        f_start_hz=1e8,
        f_stop_hz=3e9,
        points_per_decade=12,
    )
    assert result.status == "ok", result.error
    net = rf.Network(result.data["s2p_path"])
    context = FilterAnalysisContext.create(
        kind=kind,
        topology=topology,
        component_substitution=result.data["substitution"],
    )
    modeled = ladder_sparams_from_components(
        context.elements(
            {
                refdes: float(selected["snapped_value"])
                for refdes, selected in result.data["substitution"].items()
            }
        ),
        net.f,
    )
    simulated_db = 20 * np.log10(np.maximum(np.abs(net.s[:, 1, 0]), 1e-10))
    modeled_db = 20 * np.log10(np.maximum(np.abs(modeled[:, 1, 0]), 1e-10))
    assert np.max(np.abs(simulated_db - modeled_db)) < 0.2


@pytest.mark.skipif(shutil.which("ngspice") is None, reason="ngspice not installed")
@pytest.mark.ngspice
@pytest.mark.integration
def test_changing_registered_lib_changes_simulated_result_and_checksum(tmp_path: Path) -> None:
    from mcp_ltspice.server import simulate_realized_filter

    models = tmp_path / "models"
    models.mkdir()
    inductor = models / "part_L_10n.lib"
    capacitor = models / "part_C_2p2.lib"
    capacitor.write_text(
        ".subckt USER_C p n\nC1 p n 2.2p\n.ends USER_C\n",
        encoding="utf-8",
    )

    def run(resistance: float, label: str):
        inductor.write_text(
            f".subckt USER_L p n\nR1 p x {resistance}\nL1 x n 10n\n.ends USER_L\n",
            encoding="utf-8",
        )
        register_user_vendor_dir(models, namespace="user_models")
        result = simulate_realized_filter(
            {"L1": 10e-9, "C2": 2.2e-12},
            str(tmp_path / f"{label}.s2p"),
            inductor_vendor="user_models",
            capacitor_vendor="user_models",
            prefer="ngspice",
            f_start_hz=1e8,
            f_stop_hz=2e9,
            points_per_decade=40,
        )
        assert result.status == "ok", result.error
        return result, rf.Network(result.data["s2p_path"])

    low_loss, low_net = run(0.1, "low_loss")
    high_loss, high_net = run(20.0, "high_loss")
    assert (
        low_loss.data["substitution"]["L1"]["model"]["checksum_sha256"]
        != high_loss.data["substitution"]["L1"]["model"]["checksum_sha256"]
    )
    low_s21 = 20 * np.log10(np.maximum(np.abs(low_net.s[:, 1, 0]), 1e-15))
    high_s21 = 20 * np.log10(np.maximum(np.abs(high_net.s[:, 1, 0]), 1e-15))
    assert np.max(np.abs(low_s21 - high_s21)) > 0.5
