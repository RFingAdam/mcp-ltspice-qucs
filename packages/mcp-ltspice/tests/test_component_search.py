from __future__ import annotations

import pytest

from mcp_ltspice.vendor_models import (
    ComponentModel,
    ComponentSearchQuery,
    ParasiticInductor,
    register_vendor_table,
    search_component_models,
)


def test_curated_search_applies_package_srf_q_and_generic_constraints() -> None:
    report = search_component_models(
        ComponentSearchQuery(
            kind="L",
            target_value=4.7e-9,
            packages=("0402 / 1005 metric",),
            availability="generic",
            min_srf_hz=4e9,
            min_q=10,
            q_frequency_hz=1e9,
            vendors=("coilcraft_0402hp",),
        )
    )
    assert report.hits
    hit = report.hits[0]
    assert hit.value == pytest.approx(4.7e-9)
    assert hit.selection_class == "generic"
    assert hit.q_at_frequency is not None and hit.q_at_frequency > 10
    assert hit.model.checksum_sha256
    assert hit.model.srf_hz is not None and hit.model.srf_hz >= 4e9


def test_unknown_catalog_fields_fail_requested_constraints() -> None:
    report = search_component_models(
        ComponentSearchQuery(
            kind="C",
            vendors=("murata_gjm_c0g",),
            max_tolerance_pct=5,
            min_ratings={"voltage_v": 10},
            operating_temperature_c=85,
        )
    )
    assert not report.hits
    # The first unknown hard constraint is tolerance; it is not silently
    # treated as a match.
    assert report.rejected_by_constraint["tolerance"] > 0


def test_orderable_record_satisfies_all_search_constraints() -> None:
    namespace = "test_orderable_search"
    part = ParasiticInductor(L_h=10e-9, Cp_f=0.2e-12, Rs_ohm=0.3, srf_hz=3e9)
    model = ComponentModel(
        provider=namespace,
        source_reference="local://inventory/ABC-10N",
        manufacturer_part_number="ABC-10N",
        model_kind="subckt",
        pin_map={"positive": 1, "negative": 2},
        valid_frequency_hz=(1e6, 2e9),
        valid_bias={"current_a": 0.5},
        valid_temperature_c=(-40, 125),
        checksum_sha256="a" * 64,
        record_kind="orderable_part",
        orderable=True,
        package="0402",
        tolerance_pct=2,
        ratings={"voltage_v": 25, "current_a": 0.5},
        availability="in_stock",
        nominal_value=10e-9,
        nominal_unit="H",
        srf_hz=3e9,
        electrical_parameters={
            "L_h": 10e-9,
            "Cp_f": 0.2e-12,
            "Rs_ohm": 0.3,
            "srf_hz": 3e9,
        },
    )
    register_vendor_table(namespace, {part.L_h: part}, {("L", part.L_h): model})

    report = search_component_models(
        ComponentSearchQuery(
            kind="L",
            target_value=9.8e-9,
            packages=("0402",),
            availability="in_stock",
            min_q=100,
            q_frequency_hz=1e9,
            min_srf_hz=2.5e9,
            max_tolerance_pct=5,
            min_ratings={"voltage_v": 10, "current_a": 0.25},
            operating_bias={"current_a": 0.2},
            operating_temperature_c=85,
            model_kinds=("subckt",),
            vendors=(namespace,),
        )
    )
    assert len(report.hits) == 1
    assert report.hits[0].model.manufacturer_part_number == "ABC-10N"
    assert report.hits[0].selection_class == "orderable"


@pytest.mark.parametrize(
    "arguments",
    [
        {"kind": "R"},
        {"availability": "maybe"},
        {"model_kinds": ("ideal",)},
        {"min_q": -1, "q_frequency_hz": 1e9},
        {"q_frequency_hz": 0},
        {"min_srf_hz": float("inf")},
        {"max_tolerance_pct": -1},
        {"operating_bias": {"current_a": float("nan")}},
    ],
)
def test_search_rejects_invalid_constraints(arguments) -> None:
    with pytest.raises(ValueError):
        ComponentSearchQuery(**arguments)
