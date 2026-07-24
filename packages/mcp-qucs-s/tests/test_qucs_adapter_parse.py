"""Direct unit coverage for QucsatorAdapter/XyceAdapter.parse().

These adapters were fully implemented but never exercised end to end before
mcp-qucs-s gained a durable simulation job pipeline: compile() and the
sandbox-refusing run() had coverage in tests/test_backend_adapters.py, but
parse() -- turning a RawBackendResult into a normalized ResultDataset -- did
not.
"""

from __future__ import annotations

from pathlib import Path

from mcp_qucs_s.backend_adapters import QucsatorAdapter, XyceAdapter
from rf_mcp_common.backend import RawBackendResult


def test_qucsator_adapter_parse_builds_sparameter_dataset(qucs_dat: Path) -> None:
    raw = RawBackendResult(
        backend="qucsator",
        analysis="sparameters",
        artifact_paths=[qucs_dat],
        returncode=0,
        metadata={"dataset_path": str(qucs_dat)},
    )
    dataset = QucsatorAdapter().parse(raw)

    assert dataset.backend == "qucsator"
    assert dataset.analysis == "sparameters"
    assert dataset.axis.name == "frequency"
    assert dataset.axis.values == [1.0e9, 2.0e9, 3.0e9]
    assert set(dataset.traces) == {"S[1,1]", "S[1,2]", "S[2,1]", "S[2,2]"}
    s11 = dataset.traces["S[1,1]"].complex_array()
    assert s11.tolist() == [
        0.1 - 0.01j,
        0.2 - 0.02j,
        0.3 - 0.03j,
    ]
    s21 = dataset.traces["S[2,1]"].complex_array()
    assert s21.tolist() == [0.9 + 0.05j, 0.8 + 0.06j, 0.7 + 0.07j]
    assert dataset.method == "qucsator_dataset"


def test_xyce_adapter_parse_builds_hb_dataset(tmp_path: Path) -> None:
    prn = tmp_path / "hb.cir.HB.FD.prn"
    prn.write_text(
        "Index   FREQ   Re(V(OUT))   Im(V(OUT))\n"
        "0   -1.00000000e+09   0.25   0.0\n"
        "1    0.00000000e+00   0.10   0.0\n"
        "2    1.00000000e+09   0.25   0.0\n",
        encoding="utf-8",
    )
    raw = RawBackendResult(
        backend="xyce",
        analysis="harmonic_balance",
        artifact_paths=[prn],
        returncode=0,
        metadata={"hb_path": str(prn)},
    )
    dataset = XyceAdapter().parse(raw)

    assert dataset.backend == "xyce"
    assert dataset.analysis == "harmonic_balance"
    assert dataset.axis.values == [0.0, 1e9]
    assert set(dataset.traces) == {"V(out)", "P(out)"}
    v = dataset.traces["V(out)"].complex_array().real.tolist()
    assert v[0] == 0.10, "DC must not be doubled"
    assert v[1] == 0.50, "a positive-frequency bin folds to 2x"
    assert dataset.method == "xyce_harmonic_balance_frequency_domain"
