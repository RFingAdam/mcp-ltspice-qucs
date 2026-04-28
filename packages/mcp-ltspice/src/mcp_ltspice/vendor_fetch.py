"""Fetch real-vendor S-parameter and SPICE library files for filter
synthesis with high-fidelity component models.

Three entry points:

- :func:`fetch_coilcraft_s2p` — pulls a Touchstone ``.s2p`` for a given
  Coilcraft inductor or capacitor part number, caches it locally.
- :func:`fetch_murata_spice` — pulls a Murata GRM/GJM SPICE ``.lib`` (or
  ``.s2p`` if available), caches it locally.
- :func:`register_user_vendor_dir` — indexes a local directory of
  user-supplied ``.s2p`` / ``.lib`` files so they appear as substitution
  candidates with the same metadata shape as the curated tables.

Design principles:

1. **Stdlib-only HTTP** (urllib.request). No new dependency.
2. **Cache by part number** at ``~/.cache/mcp-ltspice/<vendor>/<part>.s2p``.
   Cache survives reruns — first call hits the network, subsequent calls
   are offline.
3. **Soft fail**: if the network is unreachable, raise a clear error;
   never silently fall back to wrong data.
4. **Mockable**: the HTTP layer is a single ``_http_get`` function that
   tests monkey-patch.
5. **Source-of-truth flexibility**: each helper accepts either a part
   number (which is mapped through a URL template registry) or an
   explicit ``source_url`` (always wins). For unknown parts the user
   provides the URL once; subsequent calls hit the cache.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
import skrf as rf

from rf_mcp_common.touchstone import read_touchstone

DEFAULT_CACHE_DIR = Path("~/.cache/mcp-ltspice").expanduser()
USER_AGENT = "mcp-ltspice/0.1 (https://github.com/RFingAdam/mcp-ltspice-qucs)"


# ----------------------------------------------------------------------
# HTTP helpers (stdlib-only, mockable)
# ----------------------------------------------------------------------


class FetchError(RuntimeError):
    """Raised when an HTTP fetch fails after retries."""


def _http_get(
    url: str,
    *,
    timeout: float = 30.0,
    max_retries: int = 3,
    backoff_seconds: float = 1.0,
) -> bytes:
    """GET the URL, return body bytes. Retries with exponential backoff.

    Tests monkey-patch this function to return fixture bytes without
    network access.
    """
    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - public RF vendor sites
                return resp.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last_err = e
            if attempt < max_retries - 1:
                time.sleep(backoff_seconds * (2**attempt))
    raise FetchError(f"Failed to GET {url} after {max_retries} retries: {last_err}")


# ----------------------------------------------------------------------
# Touchstone / SPICE inspection
# ----------------------------------------------------------------------


def _looks_like_touchstone(data: bytes) -> bool:
    """Heuristic: Touchstone files start with ``!`` or ``#`` comment lines."""
    head = data[:512].lstrip()
    return head.startswith(b"!") or head.startswith(b"#")


def _looks_like_spice_lib(data: bytes) -> bool:
    """Heuristic: SPICE libs contain a ``.SUBCKT`` directive (case-insensitive)."""
    return b".SUBCKT" in data.upper() or b".subckt" in data


def _extract_subckt_name(data: bytes) -> str | None:
    """Return the subcircuit name from a SPICE .lib's ``.SUBCKT <name>`` line."""
    m = re.search(rb"^\s*\.subckt\s+(\S+)", data, re.IGNORECASE | re.MULTILINE)
    return m.group(1).decode("ascii", errors="replace") if m else None


def _extract_srf_from_s2p(s2p_path: Path) -> float | None:
    """Estimate SRF from a 2-port S2P: frequency where |Im(Z11)| crosses zero
    going from inductive (positive) to capacitive (negative).

    For a parasitic-inductor S2P this is the well-known SRF.
    """
    try:
        net: rf.Network = read_touchstone(s2p_path)
    except Exception:
        return None
    if net.nports < 1:
        return None
    z11 = net.z[:, 0, 0]
    f = net.f
    im = z11.imag
    # Find sign change locations
    sign = np.sign(im)
    crossings = np.where(np.diff(sign) != 0)[0]
    if crossings.size == 0:
        return None
    # Linear interpolate the first crossing for sub-bin precision.
    i = crossings[0]
    f1, f2 = f[i], f[i + 1]
    y1, y2 = im[i], im[i + 1]
    if y2 == y1:
        return float(f1)
    return float(f1 - y1 * (f2 - f1) / (y2 - y1))


def _classify_kind_from_s2p(s2p_path: Path) -> Literal["L", "C", "unknown"]:
    """Classify whether a 2-port file looks like an inductor or capacitor.

    For a small two-port:
    - Inductor: low-freq Z11 imaginary part is positive (jωL) and rises with freq.
    - Capacitor: low-freq Z11 imaginary part is negative (-1/(jωC)) and rises (becomes less negative) with freq.
    """
    try:
        net: rf.Network = read_touchstone(s2p_path)
    except Exception:
        return "unknown"
    if net.nports < 1 or net.f.size < 2:
        return "unknown"
    z_im_low = net.z[: max(3, net.f.size // 50), 0, 0].imag
    if np.all(np.isnan(z_im_low)):
        return "unknown"
    mean_im_low = float(np.nanmean(z_im_low))
    if mean_im_low > 0:
        return "L"
    if mean_im_low < 0:
        return "C"
    return "unknown"


# ----------------------------------------------------------------------
# Coilcraft fetcher
# ----------------------------------------------------------------------


# Known stable URL patterns. Coilcraft hosts S-parameter Touchstone files
# under getmedia/{guid}/{part_number}.s2p — the GUIDs are opaque but we
# can scrape them off the part-detail pages. For widely-used parts in the
# 0402DF / 0402HP series we cache a small registry of known URLs to keep
# the example reproducible without scraping. Users can register more.
_COILCRAFT_URL_REGISTRY: dict[str, str] = {
    # (Bootstrap with empty registry. The repo can grow this over time
    # via PRs; users override per-call with `source_url`.)
}


def _coilcraft_part_page_url(part_number: str) -> str:
    """Build the canonical Coilcraft part page URL.

    Pattern: ``https://www.coilcraft.com/en-us/products/rf/.../{base-series}/{part}/``.
    The exact category path varies per series. As a generic fallback we
    use the search-by-part-number endpoint which is very stable.
    """
    return f"https://www.coilcraft.com/en-us/search/?q={urllib.request.quote(part_number)}"


def _coilcraft_s2p_url_from_part_page(html: bytes) -> str | None:
    """Parse Coilcraft part-search HTML and extract the first .s2p link.

    The site renders S-parameter download links as
    ``<a href="/getmedia/{guid}/{part}.s2p">``. We grab the first match.
    """
    m = re.search(
        rb'href="(?P<rel>/getmedia/[a-f0-9-]+/[^"]+\.s2p)"',
        html,
        re.IGNORECASE,
    )
    if not m:
        return None
    rel = m.group("rel").decode("ascii")
    return f"https://www.coilcraft.com{rel}"


def fetch_coilcraft_s2p(
    part_number: str,
    *,
    source_url: str | None = None,
    cache_dir: str | Path | None = None,
    refresh: bool = False,
    http_get=_http_get,
) -> dict[str, Any]:
    """Fetch and cache a Coilcraft S-parameter Touchstone file.

    Parameters
    ----------
    part_number
        Coilcraft part number, e.g. ``"0402DF-152XJL"``.
    source_url
        Optional explicit URL. If omitted, looks up the registered URL
        for ``part_number`` and falls back to scraping the part page.
    cache_dir
        Override the default cache directory
        (``~/.cache/mcp-ltspice/coilcraft``).
    refresh
        If ``True``, re-download even if a cached copy exists.
    http_get
        Internal: HTTP getter. Tests inject a fixture-returning function.

    Returns
    -------
    dict
        ``{s2p_path, cached, source_url, n_freq_points, freq_range_hz,
        srf_hz, kind}``.
    """
    cache_root = Path(cache_dir).expanduser() if cache_dir else DEFAULT_CACHE_DIR / "coilcraft"
    cache_root.mkdir(parents=True, exist_ok=True)
    target = cache_root / f"{part_number}.s2p"

    if target.exists() and not refresh:
        return _coilcraft_result(target, source_url=None, cached=True)

    # Resolve a URL.
    url = source_url or _COILCRAFT_URL_REGISTRY.get(part_number)
    if not url:
        # Scrape the part page.
        page_html = http_get(_coilcraft_part_page_url(part_number))
        url = _coilcraft_s2p_url_from_part_page(page_html)
        if not url:
            raise FetchError(
                f"Could not discover an S2P URL for Coilcraft part {part_number}. "
                f"Pass `source_url=...` explicitly (find the .s2p link on the part page)."
            )

    body = http_get(url)
    if not _looks_like_touchstone(body):
        raise FetchError(
            f"Response from {url} does not look like a Touchstone file (first bytes: {body[:80]!r})"
        )
    target.write_bytes(body)

    return _coilcraft_result(target, source_url=url, cached=False)


def _coilcraft_result(s2p_path: Path, *, source_url: str | None, cached: bool) -> dict[str, Any]:
    net = read_touchstone(s2p_path)
    return {
        "s2p_path": str(s2p_path),
        "cached": cached,
        "source_url": source_url,
        "n_freq_points": int(net.f.size),
        "freq_range_hz": [float(net.f.min()), float(net.f.max())],
        "srf_hz": _extract_srf_from_s2p(s2p_path),
        "kind": _classify_kind_from_s2p(s2p_path),
    }


# ----------------------------------------------------------------------
# Murata fetcher
# ----------------------------------------------------------------------


_MURATA_URL_REGISTRY: dict[str, str] = {
    # Same bootstrapping pattern: extend over time / per-call.
}


def fetch_murata_spice(
    part_number: str,
    *,
    source_url: str | None = None,
    cache_dir: str | Path | None = None,
    refresh: bool = False,
    http_get=_http_get,
) -> dict[str, Any]:
    """Fetch and cache a Murata SPICE library (or S2P) for a part number.

    Returns
    -------
    dict
        ``{lib_path | s2p_path, cached, source_url, subckt_name, kind}``.
    """
    cache_root = Path(cache_dir).expanduser() if cache_dir else DEFAULT_CACHE_DIR / "murata"
    cache_root.mkdir(parents=True, exist_ok=True)

    cached_lib = cache_root / f"{part_number}.lib"
    cached_s2p = cache_root / f"{part_number}.s2p"

    if not refresh and (cached_lib.exists() or cached_s2p.exists()):
        return _murata_result(
            cached_lib if cached_lib.exists() else None,
            cached_s2p if cached_s2p.exists() else None,
            source_url=None,
            cached=True,
        )

    url = source_url or _MURATA_URL_REGISTRY.get(part_number)
    if not url:
        raise FetchError(
            f"No registered URL for Murata part {part_number}. Provide `source_url=...` "
            f"(grab the SPICE .lib download link from the part page on https://psearch.en.murata.com/)."
        )

    body = http_get(url)
    is_lib = _looks_like_spice_lib(body)
    is_s2p = _looks_like_touchstone(body)
    if not (is_lib or is_s2p):
        raise FetchError(
            f"Response from {url} is not a SPICE .lib or Touchstone .s2p (first bytes: {body[:80]!r})"
        )

    if is_lib:
        cached_lib.write_bytes(body)
        return _murata_result(cached_lib, None, source_url=url, cached=False)
    cached_s2p.write_bytes(body)
    return _murata_result(None, cached_s2p, source_url=url, cached=False)


def _murata_result(
    lib_path: Path | None,
    s2p_path: Path | None,
    *,
    source_url: str | None,
    cached: bool,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "lib_path": str(lib_path) if lib_path else None,
        "s2p_path": str(s2p_path) if s2p_path else None,
        "cached": cached,
        "source_url": source_url,
        "subckt_name": None,
        "kind": "C",  # Murata SPICE libs are MLCC capacitor models
    }
    if lib_path:
        out["subckt_name"] = _extract_subckt_name(lib_path.read_bytes())
    if s2p_path:
        out["srf_hz"] = _extract_srf_from_s2p(s2p_path)
        out["kind"] = _classify_kind_from_s2p(s2p_path)
    return out


# ----------------------------------------------------------------------
# User-drop directory indexer
# ----------------------------------------------------------------------


@dataclass
class UserVendorPart:
    """Metadata about a user-supplied vendor file."""

    namespace: str
    filename: str
    path: str
    kind: str  # "L" | "C" | "unknown"
    value: float | None
    srf_hz: float | None
    file_type: str  # "s2p" | "lib" | "other"
    notes: str | None = None


@dataclass
class UserVendorIndex:
    """Indexed catalogue of user-supplied vendor models, keyed by namespace."""

    namespaces: dict[str, list[UserVendorPart]] = field(default_factory=dict)

    def all_for(self, namespace: str) -> list[UserVendorPart]:
        return self.namespaces.get(namespace, [])

    def values_for(self, namespace: str, kind: str) -> list[float]:
        return sorted(
            p.value for p in self.all_for(namespace) if p.kind == kind and p.value is not None
        )

    def find_nearest(self, namespace: str, value: float, kind: str) -> UserVendorPart | None:
        cands = [p for p in self.all_for(namespace) if p.kind == kind and p.value is not None]
        if not cands:
            return None
        return min(cands, key=lambda p: abs(p.value - value))  # type: ignore[arg-type]


# Module-singleton index. Updated by register_user_vendor_dir(). The
# example design.py calls register_user_vendor_dir(...) once at start;
# subsequent substitute_real_components calls can opt into the user
# namespace via inductor_vendor="user" / capacitor_vendor="user".
USER_VENDOR_INDEX = UserVendorIndex()


_VALUE_PATTERN_NH = re.compile(r"(?<![A-Za-z0-9])(\d+(?:p\d+)?)\s*[Nn][Hh]")
_VALUE_PATTERN_PF = re.compile(r"(?<![A-Za-z0-9])(\d+(?:p\d+)?)\s*[Pp][Ff]")
_VALUE_PATTERN_UH = re.compile(r"(?<![A-Za-z0-9])(\d+(?:p\d+)?)\s*[Uu][Hh]")


def _parse_value_from_filename(filename: str) -> tuple[float | None, str]:
    """Try to extract value and unit from a filename heuristic.

    Examples that work:
    - ``coilcraft_0402DF_3n3_L.s2p`` → 3.3e-9 H, "L"
    - ``murata_GJM_1p0pF_C.lib`` → 1.0e-12 F, "C"
    - ``WE_LIB_22nH.s2p`` → 22e-9 H

    Returns ``(value, kind)`` where kind is "L", "C", or "unknown".
    """
    name = filename.lower()

    # Look for nH, pF, µH/uH first
    for pat, mult, kind in [
        (_VALUE_PATTERN_NH, 1e-9, "L"),
        (_VALUE_PATTERN_UH, 1e-6, "L"),
        (_VALUE_PATTERN_PF, 1e-12, "C"),
    ]:
        m = pat.search(name)
        if m:
            raw = m.group(1).replace("p", ".")
            try:
                return float(raw) * mult, kind
            except ValueError:
                continue

    # Heuristic: explicit _L_ or _C_ tag
    if "_l_" in name or "_l." in name:
        return None, "L"
    if "_c_" in name or "_c." in name:
        return None, "C"
    return None, "unknown"


def register_user_vendor_dir(
    directory: str | Path,
    *,
    namespace: str = "user",
    extensions: tuple[str, ...] = (".s2p", ".s1p", ".lib", ".inc"),
) -> dict[str, Any]:
    """Scan ``directory`` for vendor model files and register them.

    Files are registered under ``namespace`` (default ``"user"``) so the
    same directory can be re-scanned to refresh, and multiple labelled
    directories can coexist. After registration:

    - ``USER_VENDOR_INDEX.values_for(namespace, "L")`` returns the
      indexed inductor values.
    - ``substitute_real_components(inductor_vendor="user", ...)`` (with
      ``namespace="user"``) uses these as candidates.

    Returns a result dict with ``{namespace, n_indexed, parts, errors}``.
    """
    directory = Path(directory).expanduser().resolve()
    if not directory.is_dir():
        raise NotADirectoryError(f"Not a directory: {directory}")

    parts: list[UserVendorPart] = []
    errors: list[dict[str, str]] = []
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in extensions:
            continue
        try:
            value, kind = _parse_value_from_filename(path.name)
            file_type: str
            if path.suffix.lower() in (".s1p", ".s2p"):
                file_type = "s2p"
                if kind == "unknown":
                    kind = _classify_kind_from_s2p(path)
                srf = _extract_srf_from_s2p(path)
            elif path.suffix.lower() in (".lib", ".inc"):
                file_type = "lib"
                srf = None
            else:
                file_type = "other"
                srf = None

            parts.append(
                UserVendorPart(
                    namespace=namespace,
                    filename=path.name,
                    path=str(path),
                    kind=kind,
                    value=value,
                    srf_hz=srf,
                    file_type=file_type,
                )
            )
        except Exception as e:  # noqa: BLE001 - we want per-file robustness
            errors.append({"file": str(path), "error": f"{type(e).__name__}: {e}"})

    USER_VENDOR_INDEX.namespaces[namespace] = parts

    return {
        "namespace": namespace,
        "directory": str(directory),
        "n_indexed": len(parts),
        "parts": [asdict(p) for p in parts],
        "errors": errors,
    }


def list_user_vendor_parts(namespace: str = "user", kind: str | None = None) -> list[dict[str, Any]]:
    """Return the registered user parts for inspection."""
    parts = USER_VENDOR_INDEX.all_for(namespace)
    if kind:
        parts = [p for p in parts if p.kind == kind]
    return [asdict(p) for p in parts]


# ----------------------------------------------------------------------
# Cache manifest helper (for inspection / cleanup)
# ----------------------------------------------------------------------


def cache_manifest(cache_dir: str | Path | None = None) -> dict[str, Any]:
    """Walk the vendor-fetch cache and return a manifest of cached files."""
    root = Path(cache_dir).expanduser() if cache_dir else DEFAULT_CACHE_DIR
    if not root.is_dir():
        return {"cache_dir": str(root), "exists": False, "vendors": {}}
    out: dict[str, Any] = {"cache_dir": str(root), "exists": True, "vendors": {}}
    for vendor_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        files = []
        for f in sorted(vendor_dir.iterdir()):
            if f.is_file():
                files.append(
                    {
                        "filename": f.name,
                        "path": str(f),
                        "size_bytes": f.stat().st_size,
                        "sha256": hashlib.sha256(f.read_bytes()).hexdigest()[:16],
                    }
                )
        out["vendors"][vendor_dir.name] = {"n_files": len(files), "files": files}
    return out


__all__ = [
    "DEFAULT_CACHE_DIR",
    "FetchError",
    "USER_VENDOR_INDEX",
    "UserVendorIndex",
    "UserVendorPart",
    "cache_manifest",
    "fetch_coilcraft_s2p",
    "fetch_murata_spice",
    "list_user_vendor_parts",
    "register_user_vendor_dir",
]


# Avoid unused import warning - json is used for future manifest writes.
_ = json
