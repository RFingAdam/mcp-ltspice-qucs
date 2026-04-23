"""Tests for the PDF report builder."""

from __future__ import annotations

import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pytest

from mcp_ltspice.report_pdf import build_design_report_pdf


def _make_dummy_png(path: Path, label: str) -> None:
    """Quick PNG so we can populate test design dirs."""
    fig = plt.figure(figsize=(4, 3))
    plt.text(0.5, 0.5, label, ha="center", va="center", fontsize=20)
    fig.savefig(path)
    plt.close(fig)


@pytest.fixture
def sample_design_dir(tmp_path) -> Path:
    """A fake design dir with a schematic, response, and report.md."""
    d = tmp_path / "test_design"
    d.mkdir()
    _make_dummy_png(d / "schematic.png", "schematic")
    _make_dummy_png(d / "response.png", "response")
    (d / "report.md").write_text(
        "# Test design\n\nThis is the body.\n\n## Components\n\n- L1: 5 nH\n- C1: 2 pF\n"
    )
    return d


def test_build_pdf_returns_path(sample_design_dir, tmp_path) -> None:
    out = tmp_path / "out.pdf"
    result = build_design_report_pdf(sample_design_dir, out)
    assert result == out.resolve()
    assert out.exists()
    # PDF magic number is %PDF
    with out.open("rb") as f:
        head = f.read(4)
    assert head == b"%PDF"


def test_build_pdf_has_multiple_pages(sample_design_dir, tmp_path) -> None:
    """A design with schematic + response + report.md should produce
    at least 4 pages (summary + schematic + response + report)."""
    out = tmp_path / "out.pdf"
    build_design_report_pdf(sample_design_dir, out)
    # Match "/Type /Page" or "/Type/Page" but NOT "/Type /Pages" (the page-tree node).
    pdf_bytes = out.read_bytes()
    n_pages = len(re.findall(rb"/Type\s*/Page(?!s)", pdf_bytes))
    assert n_pages >= 4


def test_build_pdf_with_empty_dir_raises(tmp_path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="no schematic"):
        build_design_report_pdf(empty, tmp_path / "out.pdf")


def test_build_pdf_with_only_md_works(tmp_path) -> None:
    """report.md alone should still produce a PDF (just text pages)."""
    d = tmp_path / "md_only"
    d.mkdir()
    (d / "report.md").write_text("# Hello\n\nJust a doc.\n")
    out = tmp_path / "doc.pdf"
    build_design_report_pdf(d, out)
    assert out.exists()


def test_build_pdf_with_missing_dir_raises(tmp_path) -> None:
    with pytest.raises(NotADirectoryError):
        build_design_report_pdf(tmp_path / "nope", tmp_path / "out.pdf")
