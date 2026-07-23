"""schematic_render.py coverage (issue #34).

The module has the heaviest third-party surface in the repo (schemdraw
plus a matplotlib backend lock) and renders user-facing artifacts, so
it is the most likely thing to break on a dependency bump or a headless
machine. These tests render real designs to both formats, verify every
refdes and formatted value appears in the SVG text (a file-size check
would miss silent mislabeling), and confirm headless operation — the
suite runs with no DISPLAY in CI already, and the DISPLAY-less
assertion below makes that explicit rather than incidental.
"""

from __future__ import annotations

import pytest

from mcp_ltspice.schematic_render import (
    _fmt_value,
    render_asc_as_schematic,
    render_lc_ladder_schematic,
)
from mcp_ltspice.synthesis.lc_filter import synthesize_lc_lpf

BUTTER = {"L1": 6.2e-9, "C2": 3.3e-12, "L3": 6.2e-9}


@pytest.fixture(scope="module")
def elliptic_components() -> dict[str, float]:
    return dict(synthesize_lc_lpf("elliptic", 5, 1e9, stopband_atten_db=40).components)


def test_runs_headless(tmp_path, monkeypatch) -> None:
    """Rendering must work with no DISPLAY at all — delete it and render,
    rather than asserting the environment happens to be headless (which
    would fail on any developer machine with X running)."""
    monkeypatch.delenv("DISPLAY", raising=False)
    out = render_lc_ladder_schematic(BUTTER, tmp_path / "headless.png")
    assert out.is_file() and out.stat().st_size > 500


@pytest.mark.parametrize("ext", ["svg", "png"])
def test_ladder_renders_nonempty_file(tmp_path, ext) -> None:
    out = render_lc_ladder_schematic(BUTTER, tmp_path / f"lpf.{ext}")
    assert out.is_file() and out.stat().st_size > 500


@pytest.mark.parametrize("ext", ["svg", "png"])
def test_elliptic_renders_nonempty_file(tmp_path, ext, elliptic_components) -> None:
    out = render_lc_ladder_schematic(
        elliptic_components, tmp_path / f"ell.{ext}", transmission_zeros=True
    )
    assert out.is_file() and out.stat().st_size > 500


def test_svg_contains_every_refdes_and_value(tmp_path) -> None:
    """Silent mislabeling is the failure a size check misses: every
    refdes and its engineering-formatted value must appear in the SVG."""
    out = render_lc_ladder_schematic(BUTTER, tmp_path / "lpf.svg")
    text = out.read_text()
    for name, value in BUTTER.items():
        assert name in text, f"refdes {name} missing from SVG"
        assert _fmt_value(value, name[0]) in text, f"value of {name} missing from SVG"


def test_elliptic_svg_labels_the_traps(tmp_path, elliptic_components) -> None:
    out = render_lc_ladder_schematic(
        elliptic_components, tmp_path / "ell.svg", transmission_zeros=True
    )
    text = out.read_text()
    for name in elliptic_components:
        assert name in text, f"refdes {name} missing from elliptic SVG"


def test_render_asc_round_trip(tmp_path) -> None:
    """generate_lpf_asc → render_asc_as_schematic recovers the refdes set."""
    from mcp_ltspice.asc_io import generate_lpf_asc

    asc = generate_lpf_asc(BUTTER, tmp_path / "lpf.asc")
    out = render_asc_as_schematic(asc, tmp_path / "from_asc.svg")
    text = out.read_text()
    for name in BUTTER:
        assert name in text


def test_server_tools_ok_and_error(tmp_path) -> None:
    from mcp_ltspice import server

    env = server.render_lc_ladder_schematic(components=BUTTER, output_path=str(tmp_path / "ok.svg"))
    assert env.status == "ok"

    blocker = tmp_path / "not_a_dir.txt"
    blocker.write_text("occupied")
    bad = server.render_lc_ladder_schematic(
        components=BUTTER, output_path=str(blocker / "x.svg")  # file as parent dir fails everywhere
    )
    assert bad.status == "error"

    from mcp_ltspice.asc_io import generate_lpf_asc

    asc = generate_lpf_asc(BUTTER, tmp_path / "t.asc")
    env2 = server.render_asc_as_schematic(asc_path=str(asc), output_path=str(tmp_path / "t.svg"))
    assert env2.status == "ok"
    bad2 = server.render_asc_as_schematic(
        asc_path=str(tmp_path / "missing.asc"), output_path=str(tmp_path / "x.svg")
    )
    assert bad2.status == "error"
