"""Register user-supplied vendor models (issue #11).

The fixtures are synthesised as ideal series-through S-parameters for a known
L or C, so the extraction is checked against the exact value that went in:
a 3.3 nH file must read back 3.3 nH, a 2.2 pF file 2.2 pF. That also pins the
two conventions worth getting right — the device impedance comes from S21
(``Z = 2·Z0·(1−S21)/S21``), not ``Z11`` (which is singular for a series
element), and the value is averaged over the extracted L/C, not over the
reactance (which is ∝ 1/f for a capacitor).
"""

from __future__ import annotations

import numpy as np
import pytest
import skrf as rf

from mcp_ltspice.vendor_fetch import (
    index_directory,
    parse_value_from_name,
    register_user_vendor_dir,
)
from mcp_ltspice.vendor_models import (
    _USER_VENDOR_TABLES,
    lookup_part,
    substitute_real_components,
)
from rf_mcp_common.touchstone import write_touchstone

Z0 = 50.0


def _series_through(z_of_f, f, name):
    """S-parameters of impedance ``z_of_f`` in a series-through 50 Ω line."""
    z = z_of_f(f)
    s = np.zeros((f.size, 2, 2), dtype=complex)
    den = z + 2 * Z0
    s[:, 0, 0] = s[:, 1, 1] = z / den
    s[:, 0, 1] = s[:, 1, 0] = 2 * Z0 / den
    return rf.Network(frequency=rf.Frequency.from_f(f, unit="Hz"), s=s, z0=Z0, name=name)


def _write_inductor(path, l_h, cp_f=0.24e-12):
    f = np.linspace(1e8, 2e10, 201)
    w = 2 * np.pi * f
    z = 1.0 / (1.0 / (1j * w * l_h) + 1j * w * cp_f)  # L with shunt parasitic
    write_touchstone(_series_through(lambda ff: z, f, path.stem), path)


def _write_capacitor(path, c_f, ls_h=0.5e-9):
    f = np.linspace(1e8, 2e10, 201)
    w = 2 * np.pi * f
    z = 1j * w * ls_h + 1.0 / (1j * w * c_f)  # C with series parasitic
    write_touchstone(_series_through(lambda ff: z, f, path.stem), path)


@pytest.fixture
def vendor_dir(tmp_path):
    _write_inductor(tmp_path / "part_L_3n3.s2p", 3.3e-9)
    _write_capacitor(tmp_path / "part_C_2p2.s2p", 2.2e-12)
    return tmp_path


@pytest.fixture(autouse=True)
def _clean_user_tables():
    """Keep runtime registrations from leaking between tests."""
    _USER_VENDOR_TABLES.clear()
    yield
    _USER_VENDOR_TABLES.clear()


# ---------------------------------------------------------------------------
# Filename value shorthand
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("part_L_3n3.s2p", 3.3e-9),
        ("ind_4N7.s2p", 4.7e-9),
        ("x_10n.s2p", 10e-9),
        ("cap_C_2p2.s2p", 2.2e-12),
        ("c_100p.s2p", 100e-12),
        ("l_1u5.s2p", 1.5e-6),
    ],
)
def test_parse_value_from_name(name, expected):
    assert parse_value_from_name(name) == pytest.approx(expected)


def test_parse_value_from_name_gives_up_gracefully():
    assert parse_value_from_name("measured_lot3.s2p") is None


# ---------------------------------------------------------------------------
# Extraction against known values
# ---------------------------------------------------------------------------


def test_inductor_value_and_kind_are_recovered(vendor_dir):
    _table, indexed, errors = index_directory(vendor_dir)
    ind = next(p for p in indexed if p.filename == "part_L_3n3.s2p")
    assert ind.kind == "L"
    assert ind.value == pytest.approx(3.3e-9, rel=1e-3)
    assert not errors


def test_capacitor_value_is_not_biased_by_averaging(vendor_dir):
    _, indexed, _ = index_directory(vendor_dir)
    cap = next(p for p in indexed if p.filename == "part_C_2p2.s2p")
    assert cap.kind == "C"
    # A naive average of reactance-then-frequency lands near 1.6 pF here;
    # averaging the extracted C gives the true value.
    assert cap.value == pytest.approx(2.2e-12, rel=1e-3)


def test_srf_is_extracted_from_the_reactance_crossing(vendor_dir):
    _, indexed, _ = index_directory(vendor_dir)
    cap = next(p for p in indexed if p.filename == "part_C_2p2.s2p")
    # A 2.2 pF cap with 0.5 nH ESL resonates at 1/(2π√LC) ≈ 4.8 GHz.
    assert cap.srf_hz == pytest.approx(4.8e9, rel=0.05)


# ---------------------------------------------------------------------------
# Registration, partial indexing, refresh, isolation
# ---------------------------------------------------------------------------


def test_registration_exposes_parts_to_lookup(vendor_dir):
    result = register_user_vendor_dir(vendor_dir, namespace="user")
    assert result["n_indexed"] == 2
    assert lookup_part("user", 3.3e-9, kind="L").L_h == pytest.approx(3.3e-9, rel=1e-3)
    assert lookup_part("user", 2.2e-12, kind="C").C_f == pytest.approx(2.2e-12, rel=1e-3)


def test_mixed_directory_lookup_filters_by_kind(vendor_dir):
    """The user dir holds both an L and a C; lookup must not sample-and-reject."""
    register_user_vendor_dir(vendor_dir, namespace="user")
    assert lookup_part("user", 3.3e-9, kind="L").L_h > 0
    assert lookup_part("user", 2.2e-12, kind="C").C_f > 0


def test_partial_indexing_survives_bad_files(vendor_dir):
    (vendor_dir / "broken.s2p").write_text("not a touchstone file", encoding="utf-8")
    (vendor_dir / "notes.txt").write_text("ignore me", encoding="utf-8")
    result = register_user_vendor_dir(vendor_dir, namespace="user")
    assert result["n_indexed"] == 2, "the two good files must still index"
    assert any(e["file"] == "broken.s2p" for e in result["errors"])
    assert not any(e["file"] == "notes.txt" for e in result["errors"]), "non-models are skipped"


def test_mislabelled_file_is_flagged_but_indexed(tmp_path):
    """A file named _C_ that measures inductive: measurement wins, note emitted."""
    _write_inductor(tmp_path / "wrong_C_5n.s2p", 5e-9)
    result = register_user_vendor_dir(tmp_path, namespace="user")
    assert result["n_indexed"] == 1
    assert result["parts"][0]["kind"] == "L", "the measurement decides the kind"
    assert any("measured reactance" in e["error"] for e in result["errors"])


def test_reregister_refreshes_the_index(vendor_dir):
    register_user_vendor_dir(vendor_dir, namespace="user")
    (vendor_dir / "part_L_3n3.s2p").unlink()
    result = register_user_vendor_dir(vendor_dir, namespace="user")
    assert result["n_indexed"] == 1
    with pytest.raises(ValueError, match="does not carry inductors"):
        lookup_part("user", 3.3e-9, kind="L")


def test_namespaces_do_not_collide(vendor_dir, tmp_path):
    other = tmp_path.parent / "other_dir"
    other.mkdir()
    _write_inductor(other / "part_L_10n.s2p", 10e-9)
    register_user_vendor_dir(vendor_dir, namespace="user_a")
    register_user_vendor_dir(other, namespace="user_b")
    assert lookup_part("user_a", 3.3e-9, kind="L").L_h == pytest.approx(3.3e-9, rel=1e-3)
    assert lookup_part("user_b", 10e-9, kind="L").L_h == pytest.approx(10e-9, rel=1e-3)


def test_cannot_shadow_a_curated_catalogue(vendor_dir):
    with pytest.raises(ValueError, match="curated catalogue"):
        register_user_vendor_dir(vendor_dir, namespace="coilcraft_0402hp")


def test_substitute_real_components_uses_registered_parts(vendor_dir):
    register_user_vendor_dir(vendor_dir, namespace="user")
    result = substitute_real_components(
        {"L1": 3.3e-9, "C2": 2.2e-12},
        inductor_vendor="user",
        capacitor_vendor="user",
    )
    assert "L1" in result and "C2" in result


# ---------------------------------------------------------------------------
# MCP tool
# ---------------------------------------------------------------------------


def test_mcp_tool_returns_parts_and_warns_on_errors(vendor_dir):
    import mcp_ltspice.server as S

    (vendor_dir / "broken.s2p").write_text("garbage", encoding="utf-8")
    fn = getattr(S.register_user_vendor_dir, "fn", S.register_user_vendor_dir)
    env = fn(str(vendor_dir), namespace="user").model_dump()
    assert env["status"] == "ok"
    assert env["data"]["n_indexed"] == 2
    assert any("broken.s2p" in w for w in env["warnings"])


def test_mcp_tool_reports_a_missing_directory():
    import mcp_ltspice.server as S

    fn = getattr(S.register_user_vendor_dir, "fn", S.register_user_vendor_dir)
    env = fn("/no/such/dir", namespace="user").model_dump()
    assert env["status"] == "error"
