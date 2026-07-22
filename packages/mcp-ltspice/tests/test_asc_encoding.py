"""Encoding and line-ending handling for ``.asc`` files (issue #31).

LTspice XVII writes UTF-16LE with a BOM; LTspice 24+ writes UTF-8. Both use
CRLF. The old code read every file as UTF-8 with ``errors="replace"``, so a
UTF-16 schematic decoded to mojibake, matched no ``SYMBOL`` line, and
``read_components`` returned ``{}`` as though the file simply had no parts.

``update_component`` then wrote ``"\\n".join(...)`` back as UTF-8 — silently
converting a user-authored schematic to a different encoding and line ending,
destroying a file it had never successfully read.

Every pre-existing test round-trips through ``generate_lpf_asc``, which
writes UTF-8/LF, so this whole class of bug was structurally invisible.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mcp_ltspice.asc_io import (
    AscDecodeError,
    read_asc_text,
    read_components,
    update_component,
)

SCHEMATIC = "\r\n".join(
    [
        "Version 4",
        "SHEET 1 1280 720",
        "SYMBOL ind 96 48 R90",
        "SYMATTR InstName L1",
        "SYMATTR Value 7.958n",
        "SYMBOL cap 224 80 R0",
        "SYMATTR InstName C2",
        "SYMATTR Value 6.366p",
        "",
    ]
)

EXPECTED = {"L1": pytest.approx(7.958e-9), "C2": pytest.approx(6.366e-12)}


def _write(path: Path, encoding: str) -> Path:
    path.write_bytes(SCHEMATIC.encode(encoding))
    return path


@pytest.mark.parametrize(
    "encoding",
    ["utf-8", "utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"],
)
def test_reads_every_encoding_ltspice_writes(tmp_path, encoding: str) -> None:
    """UTF-16 without a BOM is out of scope; with one it must work."""
    if encoding in ("utf-16-le", "utf-16-be"):
        bom = "﻿"
        path = tmp_path / f"{encoding}.asc"
        path.write_bytes((bom + SCHEMATIC).encode(encoding))
    else:
        path = _write(tmp_path / f"{encoding}.asc", encoding)
    assert read_components(path) == EXPECTED


def test_utf16_and_utf8_give_identical_results(tmp_path) -> None:
    """The acceptance criterion from #31."""
    a = read_components(_write(tmp_path / "a.asc", "utf-16"))
    b = read_components(_write(tmp_path / "b.asc", "utf-8"))
    assert a == b == EXPECTED


def test_undecodable_file_raises_instead_of_reporting_no_components(tmp_path) -> None:
    path = tmp_path / "bad.asc"
    # Lone continuation bytes: invalid UTF-8, odd length so not UTF-16 either.
    path.write_bytes(b"\xff\xfe\xfd")
    with pytest.raises(AscDecodeError, match="Could not decode"):
        read_components(path)


def test_valid_file_with_no_components_returns_empty_dict(tmp_path) -> None:
    """A genuinely empty schematic must stay distinguishable from a failure."""
    path = tmp_path / "empty.asc"
    path.write_bytes(b"Version 4\r\nSHEET 1 1280 720\r\n")
    assert read_components(path) == {}


def test_detects_encoding_and_newline(tmp_path) -> None:
    doc = read_asc_text(_write(tmp_path / "x.asc", "utf-16"))
    assert doc.encoding == "utf-16"
    assert doc.newline == "\r\n"

    doc8 = read_asc_text(_write(tmp_path / "y.asc", "utf-8"))
    assert doc8.encoding == "utf-8"
    assert doc8.newline == "\r\n"


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_update_component_preserves_encoding_and_line_endings(tmp_path, encoding: str) -> None:
    """The data-loss half of #31: only the edited line may change."""
    path = _write(tmp_path / f"edit-{encoding}.asc", encoding)
    before = path.read_bytes()

    update_component(path, "L1", 1.0e-8)
    after = path.read_bytes()

    assert after != before, "the edit should have changed something"
    # Still the same encoding: it must decode cleanly and round-trip.
    decoded = after.decode(encoding)
    assert "\r\n" in decoded, "CRLF line endings were not preserved"
    assert "\n" not in decoded.replace("\r\n", ""), "a bare LF crept in"

    reread = read_components(path)
    assert reread["L1"] == pytest.approx(1.0e-8)
    assert reread["C2"] == pytest.approx(6.366e-12), "an untouched part changed"


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_update_component_leaves_other_lines_byte_identical(tmp_path, encoding: str) -> None:
    path = _write(tmp_path / f"bytes-{encoding}.asc", encoding)
    before = path.read_bytes().decode(encoding).splitlines()

    update_component(path, "C2", 1.0e-12)
    after = path.read_bytes().decode(encoding).splitlines()

    assert len(before) == len(after)
    for i, (b, a) in enumerate(zip(before, after, strict=True)):
        if b.startswith("SYMATTR Value") and before[i - 1].endswith("C2"):
            assert a != b, "the targeted value should have changed"
        else:
            assert a == b, f"line {i} changed unexpectedly: {b!r} -> {a!r}"


def test_updating_an_absent_refdes_raises(tmp_path) -> None:
    """Rewriting the file unchanged would look like a successful edit."""
    path = _write(tmp_path / "z.asc", "utf-16")
    before = path.read_bytes()
    with pytest.raises(KeyError, match="L99"):
        update_component(path, "L99", 1e-9)
    assert path.read_bytes() == before, "file must be untouched on failure"
