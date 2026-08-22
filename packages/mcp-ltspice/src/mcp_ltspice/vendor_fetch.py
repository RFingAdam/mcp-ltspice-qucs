"""Register user-supplied vendor models as substitution candidates.

Even with automated fetchers for the public catalogues, engineers have
third-party or measured ``.s2p`` files: Würth, AVX, TDK, distributor
exports, in-house lab data. That live on no public URL. Without a way to
register them, the only options are hand-editing ``.asc`` files or forking
the curated table.

:func:`register_user_vendor_dir` scans a directory of ``.s2p`` (and, by
filename, ``.lib``) files, works out each part's kind, value and
self-resonant frequency, and registers them under a namespace so
``substitute_real_components(inductor_vendor="user", ...)`` treats them like
any curated series.

Two independent signals decide inductor-vs-capacitor: the filename (``_L_``
/ ``_C_`` and RF value shorthand like ``3n3``) and the measured reactance
recovered from S21 (positive at low frequency for an inductor, negative for
a capacitor). When both are present and disagree, the measurement wins and
the disagreement is reported, because a mislabelled file is exactly the kind
of thing that should not pass silently.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from mcp_ltspice.vendor_models import (
    ComponentModel,
    ParasiticCapacitor,
    ParasiticInductor,
    ParasiticPart,
    register_vendor_table,
)
from rf_mcp_common.logging import get_logger

log = get_logger("mcp_ltspice.vendor_fetch")

#: RF value shorthand where the SI letter also marks the decimal point:
#: ``3n3`` = 3.3 nH, ``4N7`` = 4.7 nH, ``2p2`` = 2.2 pF, ``10n`` = 10 nH.
_SI = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3}
_VALUE_RE = re.compile(r"(?<![A-Za-z0-9])(\d+)([fpnuµm])(\d*)(?![A-Za-z0-9])", re.IGNORECASE)


@dataclass
class IndexedPart:
    filename: str
    kind: str  # "L" or "C"
    value: float
    srf_hz: float | None
    source: str  # how kind/value were determined
    model: ComponentModel


@dataclass
class LocalDirectoryProvider:
    """Local-first component provider with explicit refresh/cache semantics."""

    root: Path
    provider_id: str = "local-directory"
    _parts: tuple[IndexedPart, ...] = field(default=(), init=False, repr=False)
    _errors: tuple[dict[str, str], ...] = field(default=(), init=False, repr=False)

    def refresh(self) -> tuple[IndexedPart, ...]:
        _table, indexed, errors = index_directory(self.root)
        self._parts = tuple(indexed)
        self._errors = tuple(errors)
        return self._parts

    @property
    def errors(self) -> tuple[dict[str, str], ...]:
        return self._errors

    def list_models(self) -> tuple[ComponentModel, ...]:
        if not self._parts:
            self.refresh()
        return tuple(part.model for part in self._parts)

    def get_model(self, checksum_sha256: str) -> ComponentModel:
        for model in self.list_models():
            if model.checksum_sha256 == checksum_sha256:
                return model
        raise KeyError(f"component model checksum not found: {checksum_sha256}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _subcircuit_from_lib(path: Path) -> tuple[str, tuple[str, str]]:
    declarations: list[tuple[str, list[str]]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("*", ";")):
            continue
        match = re.match(r"(?i)^\.subckt\s+(\S+)\s+(.+)$", stripped)
        if match:
            declarations.append((match.group(1), match.group(2).split()))
    if len(declarations) != 1:
        raise ValueError(
            f".lib must contain exactly one .SUBCKT for automatic attachment; "
            f"found {len(declarations)}"
        )
    name, pins = declarations[0]
    if len(pins) != 2:
        raise ValueError(
            f"subcircuit {name!r} has {len(pins)} pins; a two-terminal pin map is required"
        )
    return name, (pins[0], pins[1])


def parse_value_from_name(name: str) -> float | None:
    """Pull an RF value like ``3n3`` / ``4N7`` / ``2p2`` from a filename."""
    m = _VALUE_RE.search(name)
    if m is None:
        return None
    whole, unit, frac = m.group(1), m.group(2).lower(), m.group(3)
    mantissa = float(f"{whole}.{frac}") if frac else float(whole)
    return mantissa * _SI[unit]


def _kind_from_name(name: str) -> str | None:
    low = name.lower()
    if re.search(r"(?:^|[_\-])l(?:[_\-]|\d)", low) or "ind" in low:
        return "L"
    if re.search(r"(?:^|[_\-])c(?:[_\-]|\d)", low) or "cap" in low:
        return "C"
    return None


def _analyse_s2p(path: Path, z0: float = 50.0) -> tuple[str, float, float | None]:
    """Return ``(kind, value, srf_hz)`` from a Touchstone file's reactance.

    A two-terminal passive is characterised in a **series-through** fixture,
    the RF-industry norm, so the device impedance comes from S21::

        Z_dut = 2·Z₀·(1 − S21) / S21

    The sign of its reactance at low frequency gives the kind (inductor if
    positive, capacitor if negative), the magnitude gives L or C, and the
    lowest zero crossing of the reactance is the self-resonance.
    """
    import skrf as rf

    net = rf.Network(str(path))
    net.renormalize(z0)
    f = np.asarray(net.f, dtype=np.float64)
    s21 = net.s[:, 1, 0]
    # Guard the DC-ish point where S21 → 1 and Z_dut → 0.
    with np.errstate(divide="ignore", invalid="ignore"):
        z_dut = 2.0 * z0 * (1.0 - s21) / s21
    x = np.asarray(z_dut.imag, dtype=np.float64)

    # Lowest few valid points, where a real part behaves as an ideal L or C.
    valid = np.isfinite(x) & (f > 0)
    if not valid.any():
        raise ValueError("could not recover a device reactance from S21")
    fi = f[valid]
    xi = x[valid]
    lo = slice(0, max(1, min(5, fi.size)))
    w = 2.0 * math.pi * fi[lo]

    # Kind from the sign of the reactance at the lowest point; value averaged
    # over the *extracted* L or C (flat vs frequency for an ideal element),
    # not over the reactance itself (which is ∝ 1/f for a capacitor, so
    # averaging it and the frequency separately biases the result).
    if xi[lo][0] > 0:
        kind = "L"
        value = float(np.mean(xi[lo] / w))
    else:
        kind = "C"
        value = float(np.mean(-1.0 / (w * xi[lo])))

    srf_hz = _srf_from_reactance(fi, xi)
    return kind, value, srf_hz


def _srf_from_reactance(f: np.ndarray, x: np.ndarray) -> float | None:
    """Lowest frequency where the reactance crosses zero (series resonance)."""
    sign = np.sign(x)
    crossings = np.where(np.diff(sign) != 0)[0]
    if crossings.size == 0:
        return None
    i = int(crossings[0])
    # Linear interpolation to the zero crossing.
    x0, x1 = x[i], x[i + 1]
    if x1 == x0:
        return float(f[i])
    frac = -x0 / (x1 - x0)
    return float(f[i] + frac * (f[i + 1] - f[i]))


def _default_srf(kind: str, value: float) -> float:
    """A conservative SRF when a file gives no way to measure one.

    Used for ``.lib`` parts, which we index by filename but do not simulate
    here. A rough parasitic (0.3 pF across an inductor, 0.5 nH in series with
    a capacitor) is honest about being an estimate, not a spec.
    """
    if kind == "L":
        return 1.0 / (2.0 * math.pi * math.sqrt(value * 0.3e-12))
    return 1.0 / (2.0 * math.pi * math.sqrt(0.5e-9 * value))


def _build_part(kind: str, value: float, srf_hz: float) -> ParasiticPart:
    if kind == "L":
        cp = 1.0 / ((2.0 * math.pi * srf_hz) ** 2 * value)
        return ParasiticInductor(L_h=value, Cp_f=cp, Rs_ohm=0.0, srf_hz=srf_hz)
    ls = 1.0 / ((2.0 * math.pi * srf_hz) ** 2 * value)
    return ParasiticCapacitor(C_f=value, Ls_h=ls, Rs_ohm=0.0, srf_hz=srf_hz)


def index_directory(
    directory: str | Path,
) -> tuple[dict[float, ParasiticPart], list[IndexedPart], list[dict[str, str]]]:
    """Scan ``directory`` and return ``(table, indexed, errors)``.

    ``table`` is keyed by value for :func:`register_vendor_table`. Per-file
    failures land in ``errors`` and never abort the scan, so one malformed
    file cannot lose an otherwise good directory.
    """
    root = Path(directory).expanduser()
    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: {root}")

    table: dict[float, ParasiticPart] = {}
    indexed: list[IndexedPart] = []
    errors: list[dict[str, str]] = []

    for path in sorted(root.iterdir()):
        suffix = path.suffix.lower()
        if suffix not in (".s2p", ".lib"):
            continue
        try:
            name_kind = _kind_from_name(path.name)
            name_value = parse_value_from_name(path.name)

            if suffix == ".s2p":
                meas_kind, meas_value, srf = _analyse_s2p(path)
                if name_kind and name_kind != meas_kind:
                    errors.append(
                        {
                            "file": path.name,
                            "error": (
                                f"filename says {name_kind} but the measured reactance "
                                f"says {meas_kind}; using the measurement."
                            ),
                        }
                    )
                kind = meas_kind
                # Prefer the filename's nominal value when it is close to the
                # measured one (rounded catalogue value); else trust the file.
                value = (
                    name_value
                    if name_value and abs(name_value - meas_value) / meas_value < 0.2
                    else meas_value
                )
                srf = srf if srf is not None else _default_srf(kind, value)
                source = "s2p+name" if name_value else "s2p"
                model = ComponentModel(
                    provider="user",
                    source_reference=str(path.resolve()),
                    manufacturer_part_number=None,
                    model_kind="touchstone",
                    pin_map={"port1": 1, "port2": 2},
                    valid_frequency_hz=(None, None),
                    valid_bias={},
                    valid_temperature_c=(None, None),
                    checksum_sha256=_sha256(path),
                    source_path=str(path.resolve()),
                    reduction="series_through_s21_to_first_order_lumped",
                    record_kind="technology_model",
                    orderable=False,
                    source_document=str(path.resolve()),
                    retrieved_at="2026-07-23",
                    model_license="user-supplied; caller must verify redistribution rights",
                    checksum_scope="source_file_bytes",
                    provenance_warning=(
                        "Filename is not treated as a verified manufacturer part number."
                    ),
                )
            else:  # .lib: index by filename only
                if name_kind is None or name_value is None:
                    raise ValueError(
                        "cannot determine kind/value from a .lib filename; name it "
                        "like 'part_L_3n3.lib' or 'part_C_2p2.lib'."
                    )
                kind, value = name_kind, name_value
                srf = _default_srf(kind, value)
                source = "lib-name"
                subcircuit_name, _pins = _subcircuit_from_lib(path)
                model = ComponentModel(
                    provider="user",
                    source_reference=str(path.resolve()),
                    manufacturer_part_number=None,
                    model_kind="subckt",
                    pin_map={"positive": 1, "negative": 2},
                    valid_frequency_hz=(None, None),
                    valid_bias={},
                    valid_temperature_c=(None, None),
                    checksum_sha256=_sha256(path),
                    source_path=str(path.resolve()),
                    subcircuit_name=subcircuit_name,
                    record_kind="technology_model",
                    orderable=False,
                    source_document=str(path.resolve()),
                    retrieved_at="2026-07-23",
                    model_license="user-supplied; caller must verify redistribution rights",
                    checksum_scope="source_file_bytes",
                    provenance_warning=(
                        "Filename is not treated as a verified manufacturer part number."
                    ),
                )

            table[value] = _build_part(kind, value, srf)
            indexed.append(
                IndexedPart(
                    filename=path.name,
                    kind=kind,
                    value=value,
                    srf_hz=srf,
                    source=source,
                    model=model,
                )
            )
        except Exception as e:
            errors.append({"file": path.name, "error": str(e)})

    return table, indexed, errors


def register_user_vendor_dir(directory: str | Path, namespace: str = "user") -> dict[str, Any]:
    """Index a directory and register it under ``namespace``.

    Re-registering the same directory replaces the previous index, so new
    files appear and deleted ones drop out.
    """
    if not namespace.strip():
        raise ValueError("namespace must be a non-empty string.")

    provider = LocalDirectoryProvider(
        Path(directory).expanduser().resolve(),
        provider_id=f"local-directory:{namespace}",
    )
    indexed = list(provider.refresh())
    errors = list(provider.errors)
    table = {
        part.value: _build_part(part.kind, part.value, float(part.srf_hz))
        for part in indexed
        if part.srf_hz is not None
    }
    models = {(part.kind, part.value): part.model for part in indexed}
    register_vendor_table(namespace, table, models)

    return {
        "namespace": namespace,
        "directory": str(Path(directory).expanduser().resolve()),
        "n_indexed": len(indexed),
        "parts": [
            {
                "filename": p.filename,
                "kind": p.kind,
                "value": p.value,
                "srf_hz": p.srf_hz,
                "source": p.source,
                "model": p.model.to_dict(),
            }
            for p in indexed
        ],
        "errors": errors,
    }
