"""Dialect handling for ``.raw`` readback.

spicelib infers the file dialect from the header, and that inference is not
stable across simulator versions: ngspice 44.2 writes a header it recognises,
while the ngspice in CI writes one it does not, raising

    SpiceReadException: Invalid RAW file. Plot nr. 1: file dialect is not
    specified and could not be auto detected.

The file is perfectly readable once the dialect is named, so extraction
falls back through the known dialects instead of failing.
"""

from __future__ import annotations

import pytest
from spicelib.raw.raw_classes import SpiceReadException

from mcp_ltspice.extract import RAW_DIALECTS, _open_raw


def test_auto_detection_is_used_when_it_works(monkeypatch) -> None:
    calls: list[dict] = []

    class FakeRaw:
        def __init__(self, path, dialect=None, **kw):
            calls.append({"dialect": dialect})

    monkeypatch.setattr("spicelib.RawRead", FakeRaw)
    _open_raw("x.raw")
    assert calls == [{"dialect": None}], "should not try explicit dialects when auto works"


def test_falls_back_through_dialects_when_auto_detection_fails(monkeypatch) -> None:
    """The CI failure mode: auto-detect fails, an explicit dialect succeeds."""
    attempted: list[str | None] = []

    class FakeRaw:
        def __init__(self, path, dialect=None, **kw):
            attempted.append(dialect)
            if dialect is None:
                raise SpiceReadException("file dialect is not specified")
            if dialect != "ngspice":
                raise SpiceReadException(f"not {dialect}")

    monkeypatch.setattr("spicelib.RawRead", FakeRaw)
    _open_raw("x.raw")
    assert attempted[0] is None
    assert "ngspice" in attempted


def test_explicit_dialect_skips_auto_detection(monkeypatch) -> None:
    attempted: list[str | None] = []

    class FakeRaw:
        def __init__(self, path, dialect=None, **kw):
            attempted.append(dialect)

    monkeypatch.setattr("spicelib.RawRead", FakeRaw)
    _open_raw("x.raw", dialect="xyce")
    assert attempted == ["xyce"]


def test_unreadable_file_reports_every_dialect_tried(monkeypatch) -> None:
    class FakeRaw:
        def __init__(self, path, dialect=None, **kw):
            raise SpiceReadException("nope")

    monkeypatch.setattr("spicelib.RawRead", FakeRaw)
    with pytest.raises(SpiceReadException, match="any known dialect"):
        _open_raw("x.raw")


def test_dialect_list_matches_what_spicelib_accepts() -> None:
    """Guard against spicelib renaming or dropping a dialect under us."""
    import inspect
    import re

    import spicelib.raw.raw_read as rr

    m = re.search(r"dialect not in \(([^)]*)\)", inspect.getsource(rr))
    assert m, "could not locate spicelib's dialect whitelist"
    supported = {s.strip().strip("'\"") for s in m.group(1).split(",") if s.strip()}
    assert set(RAW_DIALECTS) <= supported, (
        f"RAW_DIALECTS has entries spicelib rejects: {set(RAW_DIALECTS) - supported}"
    )
