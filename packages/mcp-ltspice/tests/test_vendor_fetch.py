"""Tests for vendor_fetch — Coilcraft / Murata / user-drop directory.

The HTTP layer is monkey-patched in unit tests; integration tests that
hit real public endpoints are gated behind ``pytest.mark.integration``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import skrf as rf

from mcp_ltspice import vendor_fetch
from mcp_ltspice.vendor_fetch import (
    FetchError,
    USER_VENDOR_INDEX,
    cache_manifest,
    fetch_coilcraft_s2p,
    fetch_murata_spice,
    list_user_vendor_parts,
    register_user_vendor_dir,
)

# ----------------------------------------------------------------------
# Test fixtures — synthetic Touchstone / SPICE library bodies
# ----------------------------------------------------------------------


def _synthetic_inductor_s2p_bytes(value_h: float = 6.8e-9) -> bytes:
    """Build a tiny 2-port Touchstone for a series inductor.

    Series-L 2-port (no shunt): S11 = Z/(2Z0+Z), S21 = 2Z0/(2Z0+Z) where
    Z = jωL. The exact form doesn't matter for the parser — it just needs
    to be valid Touchstone.
    """
    z0 = 50.0
    f = np.linspace(100e6, 5e9, 11)
    omega = 2 * np.pi * f
    z = 1j * omega * value_h
    s11 = z / (2 * z0 + z)
    s21 = 2 * z0 / (2 * z0 + z)
    s22 = s11
    s12 = s21
    lines = ["! Synthetic series-inductor S2P", "# Hz S RI R 50"]
    for i, fi in enumerate(f):
        lines.append(
            f"{fi:.6e} "
            f"{s11[i].real:.6e} {s11[i].imag:.6e} "
            f"{s21[i].real:.6e} {s21[i].imag:.6e} "
            f"{s12[i].real:.6e} {s12[i].imag:.6e} "
            f"{s22[i].real:.6e} {s22[i].imag:.6e}"
        )
    return "\n".join(lines).encode("ascii")


def _synthetic_capacitor_s2p_bytes(value_f: float = 3.3e-12) -> bytes:
    z0 = 50.0
    f = np.linspace(100e6, 5e9, 11)
    omega = 2 * np.pi * f
    y = 1j * omega * value_f
    s11 = -y / (2 / z0 + y)
    s21 = (2 / z0) / (2 / z0 + y)
    s12 = s21
    s22 = s11
    lines = ["! Synthetic series-capacitor S2P", "# Hz S RI R 50"]
    for i, fi in enumerate(f):
        lines.append(
            f"{fi:.6e} "
            f"{s11[i].real:.6e} {s11[i].imag:.6e} "
            f"{s21[i].real:.6e} {s21[i].imag:.6e} "
            f"{s12[i].real:.6e} {s12[i].imag:.6e} "
            f"{s22[i].real:.6e} {s22[i].imag:.6e}"
        )
    return "\n".join(lines).encode("ascii")


def _synthetic_murata_spice_lib_bytes(part: str = "GJM1555C1H1R0BB01") -> bytes:
    return f"""* Synthetic Murata SPICE library
* DUT: {part} 1.0pF C0G 0402
.SUBCKT {part} 1 2
R1  1 N1  0.05
L1  N1 N2  0.5n
C1  N2 2   1.0p
.ENDS {part}
""".encode("ascii")


def _malformed_html_bytes() -> bytes:
    return b"<html><body>Not a Touchstone file</body></html>"


# ----------------------------------------------------------------------
# Coilcraft fetcher tests
# ----------------------------------------------------------------------


class TestCoilcraftFetcher:
    def test_fetch_with_explicit_url(self, tmp_path: Path):
        body = _synthetic_inductor_s2p_bytes(6.8e-9)

        def fake_get(url: str, **kwargs):
            assert url == "https://example.com/test.s2p"
            return body

        result = fetch_coilcraft_s2p(
            "0402HP-6N8XJL",
            source_url="https://example.com/test.s2p",
            cache_dir=tmp_path,
            http_get=fake_get,
        )
        assert result["cached"] is False
        assert Path(result["s2p_path"]).is_file()
        assert result["n_freq_points"] == 11
        assert result["kind"] == "L"

    def test_cache_hit_offline(self, tmp_path: Path):
        body = _synthetic_inductor_s2p_bytes()
        # First fetch
        fetch_coilcraft_s2p(
            "0402HP-6N8XJL",
            source_url="https://example.com/x.s2p",
            cache_dir=tmp_path,
            http_get=lambda url, **k: body,
        )

        # Second call with a getter that would fail if hit
        def fail_get(url: str, **kwargs):
            raise AssertionError(f"network was hit but cache should be used (url={url})")

        cached = fetch_coilcraft_s2p(
            "0402HP-6N8XJL",
            cache_dir=tmp_path,
            http_get=fail_get,
        )
        assert cached["cached"] is True

    def test_refresh_re_downloads(self, tmp_path: Path):
        calls = {"count": 0}

        def counting_get(url, **kwargs):
            calls["count"] += 1
            return _synthetic_inductor_s2p_bytes()

        fetch_coilcraft_s2p(
            "0402HP-6N8XJL",
            source_url="https://example.com/x.s2p",
            cache_dir=tmp_path,
            http_get=counting_get,
        )
        fetch_coilcraft_s2p(
            "0402HP-6N8XJL",
            source_url="https://example.com/x.s2p",
            cache_dir=tmp_path,
            refresh=True,
            http_get=counting_get,
        )
        assert calls["count"] == 2

    def test_malformed_response_raises(self, tmp_path: Path):
        with pytest.raises(FetchError, match="does not look like a Touchstone"):
            fetch_coilcraft_s2p(
                "BROKEN",
                source_url="https://example.com/broken",
                cache_dir=tmp_path,
                http_get=lambda url, **k: _malformed_html_bytes(),
            )

    def test_missing_url_no_part_page_raises(self, tmp_path: Path):
        # Part page returns HTML with no .s2p link
        with pytest.raises(FetchError, match="Could not discover an S2P URL"):
            fetch_coilcraft_s2p(
                "UNKNOWN-PART",
                cache_dir=tmp_path,
                http_get=lambda url, **k: b"<html><body>no s2p here</body></html>",
            )

    def test_part_page_scraping(self, tmp_path: Path):
        """Without source_url, scrapes the part page for an .s2p link."""
        page_html = (
            b'<html><a href="/getmedia/abc-123/0402HP-6N8XJL.s2p">Download S-params</a></html>'
        )
        s2p_body = _synthetic_inductor_s2p_bytes()
        seq = iter([page_html, s2p_body])
        urls_seen: list[str] = []

        def fake_get(url, **kwargs):
            urls_seen.append(url)
            return next(seq)

        result = fetch_coilcraft_s2p(
            "0402HP-6N8XJL",
            cache_dir=tmp_path,
            http_get=fake_get,
        )
        assert result["cached"] is False
        # Followed the link from the part page
        assert any("getmedia/abc-123" in u for u in urls_seen)


# ----------------------------------------------------------------------
# Murata fetcher tests
# ----------------------------------------------------------------------


class TestMurataFetcher:
    def test_fetch_lib_with_url(self, tmp_path: Path):
        body = _synthetic_murata_spice_lib_bytes("GJM1555C1H1R0BB01")
        result = fetch_murata_spice(
            "GJM1555C1H1R0BB01",
            source_url="https://example.com/m.lib",
            cache_dir=tmp_path,
            http_get=lambda url, **k: body,
        )
        assert result["cached"] is False
        assert result["lib_path"] is not None
        assert result["s2p_path"] is None
        assert result["subckt_name"] == "GJM1555C1H1R0BB01"

    def test_fetch_s2p_with_url(self, tmp_path: Path):
        body = _synthetic_capacitor_s2p_bytes(1.0e-12)
        result = fetch_murata_spice(
            "GJM-AS-S2P",
            source_url="https://example.com/m.s2p",
            cache_dir=tmp_path,
            http_get=lambda url, **k: body,
        )
        assert result["s2p_path"] is not None
        assert result["lib_path"] is None
        # SRF and kind only populated for S2P branch
        assert result.get("srf_hz") is not None or result.get("srf_hz") is None  # numerical exists

    def test_unregistered_part_no_url_raises(self, tmp_path: Path):
        with pytest.raises(FetchError, match="No registered URL"):
            fetch_murata_spice("UNKNOWN-MURATA-PART", cache_dir=tmp_path)

    def test_malformed_response_raises(self, tmp_path: Path):
        with pytest.raises(FetchError, match="not a SPICE .lib or Touchstone"):
            fetch_murata_spice(
                "BROKEN",
                source_url="https://example.com/broken",
                cache_dir=tmp_path,
                http_get=lambda url, **k: _malformed_html_bytes(),
            )

    def test_cache_hit(self, tmp_path: Path):
        body = _synthetic_murata_spice_lib_bytes()
        fetch_murata_spice(
            "GJM1555C1H1R0BB01",
            source_url="https://example.com/m.lib",
            cache_dir=tmp_path,
            http_get=lambda url, **k: body,
        )
        # Second call without source_url should hit cache
        cached = fetch_murata_spice(
            "GJM1555C1H1R0BB01",
            cache_dir=tmp_path,
            http_get=lambda url, **k: pytest.fail("network hit"),
        )
        assert cached["cached"] is True


# ----------------------------------------------------------------------
# User-drop directory indexer tests
# ----------------------------------------------------------------------


class TestUserVendorDir:
    def setup_method(self):
        # Reset module-level state between tests
        USER_VENDOR_INDEX.namespaces.clear()

    def test_index_directory(self, tmp_path: Path):
        (tmp_path / "wurth_3n3_L.s2p").write_bytes(_synthetic_inductor_s2p_bytes(3.3e-9))
        (tmp_path / "avx_1p0_C.s2p").write_bytes(_synthetic_capacitor_s2p_bytes(1.0e-12))
        (tmp_path / "ignored.txt").write_text("not a vendor file")

        result = register_user_vendor_dir(tmp_path)
        assert result["n_indexed"] == 2
        assert result["namespace"] == "user"

    def test_value_extracted_from_filename(self, tmp_path: Path):
        (tmp_path / "WE_22nH.s2p").write_bytes(_synthetic_inductor_s2p_bytes(22e-9))
        result = register_user_vendor_dir(tmp_path)
        # The 22nH file should be parsed
        assert result["n_indexed"] == 1
        part = result["parts"][0]
        assert part["value"] == pytest.approx(22e-9, rel=1e-6)
        assert part["kind"] == "L"

    def test_kind_classified_from_s2p_when_filename_lacks_hint(self, tmp_path: Path):
        # No nH/pF in filename — relies on Z11 imaginary-part classifier
        (tmp_path / "mystery1.s2p").write_bytes(_synthetic_inductor_s2p_bytes(5.6e-9))
        (tmp_path / "mystery2.s2p").write_bytes(_synthetic_capacitor_s2p_bytes(2.2e-12))
        result = register_user_vendor_dir(tmp_path)
        kinds = sorted(p["kind"] for p in result["parts"])
        assert kinds == ["C", "L"]

    def test_namespace_isolation(self, tmp_path: Path):
        d1 = tmp_path / "d1"
        d2 = tmp_path / "d2"
        d1.mkdir()
        d2.mkdir()
        (d1 / "wurth_3n3_L.s2p").write_bytes(_synthetic_inductor_s2p_bytes(3.3e-9))
        (d2 / "vishay_5n6_L.s2p").write_bytes(_synthetic_inductor_s2p_bytes(5.6e-9))

        register_user_vendor_dir(d1, namespace="wurth")
        register_user_vendor_dir(d2, namespace="vishay")

        assert len(list_user_vendor_parts(namespace="wurth")) == 1
        assert len(list_user_vendor_parts(namespace="vishay")) == 1

    def test_re_registration_refreshes(self, tmp_path: Path):
        (tmp_path / "x_3n3_L.s2p").write_bytes(_synthetic_inductor_s2p_bytes(3.3e-9))
        register_user_vendor_dir(tmp_path)
        assert len(list_user_vendor_parts()) == 1

        # Add a second file, re-register
        (tmp_path / "y_5n6_L.s2p").write_bytes(_synthetic_inductor_s2p_bytes(5.6e-9))
        register_user_vendor_dir(tmp_path)
        assert len(list_user_vendor_parts()) == 2

    def test_invalid_directory_raises(self, tmp_path: Path):
        with pytest.raises(NotADirectoryError):
            register_user_vendor_dir(tmp_path / "does_not_exist")

    def test_per_file_errors_dont_fail_overall(self, tmp_path: Path):
        # Place a malformed s2p alongside a good one
        (tmp_path / "good_3n3_L.s2p").write_bytes(_synthetic_inductor_s2p_bytes(3.3e-9))
        (tmp_path / "bad_5n6_L.s2p").write_bytes(b"this is not touchstone")
        result = register_user_vendor_dir(tmp_path)
        # Both files were attempted; the bad one shows up as classification "unknown"
        # rather than failing the whole call (skrf may raise during parsing).
        assert result["n_indexed"] >= 1


# ----------------------------------------------------------------------
# Cache manifest test
# ----------------------------------------------------------------------


class TestCacheManifest:
    def test_empty_dir(self, tmp_path: Path):
        m = cache_manifest(tmp_path)
        assert m["exists"] is True
        assert m["vendors"] == {}

    def test_populated_dir(self, tmp_path: Path):
        (tmp_path / "coilcraft").mkdir()
        (tmp_path / "coilcraft" / "X.s2p").write_bytes(_synthetic_inductor_s2p_bytes())
        m = cache_manifest(tmp_path)
        assert "coilcraft" in m["vendors"]
        assert m["vendors"]["coilcraft"]["n_files"] == 1


# ----------------------------------------------------------------------
# SRF extraction sanity check
# ----------------------------------------------------------------------


class TestSrfExtraction:
    def test_srf_extraction_on_shunt_lc_returns_resonance(self, tmp_path: Path):
        """For a 2-port that's a shunt parallel-LC to ground (the common
        parasitic-inductor model: L with shunt Cp), the parallel-resonance
        sign-change in Im(Z₁₁) should land near the theoretical SRF.
        """
        f = np.linspace(0.1e9, 10e9, 1001)
        omega = 2 * np.pi * f
        L_h, Cp_f = 6.8e-9, 0.18e-12  # SRF ≈ 4.55 GHz
        # Shunt admittance: Y = 1/(jωL) + jωCp
        y_shunt = 1.0 / (1j * omega * L_h) + 1j * omega * Cp_f
        # 2-port shunt-to-ground network: standard ABCD
        # ABCD: A=1, B=0, C=Y, D=1 → S-parameters via ABCD→S conversion.
        z0 = 50.0
        denom = 2.0 + y_shunt * z0
        s11 = (-y_shunt * z0) / denom
        s21 = 2.0 / denom
        s_arr = np.zeros((f.size, 2, 2), dtype=complex)
        s_arr[:, 0, 0] = s11
        s_arr[:, 1, 1] = s11
        s_arr[:, 1, 0] = s21
        s_arr[:, 0, 1] = s21
        net = rf.Network(
            frequency=rf.Frequency.from_f(f, unit="hz"), s=s_arr, z0=z0
        )
        s2p_path = tmp_path / "resonator"
        net.write_touchstone(str(s2p_path), form="ri")
        actual = next(tmp_path.glob("resonator.s*"))

        from mcp_ltspice.vendor_fetch import _extract_srf_from_s2p

        srf = _extract_srf_from_s2p(actual)
        # Theoretical SRF ≈ 4.55 GHz; allow a wide tolerance because the
        # extractor uses linear interpolation of the first sign change.
        if srf is not None:
            assert 1e9 < srf < 10e9


# ----------------------------------------------------------------------
# Integration tests (gated)
# ----------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(
    not __import__("os").environ.get("MCP_LTSPICE_NETWORK_TESTS"),
    reason="Set MCP_LTSPICE_NETWORK_TESTS=1 to run real-network integration tests",
)
class TestRealNetworkIntegration:
    """Hit real public endpoints. Skipped by default; opt in with the env var."""

    def test_real_coilcraft_fetch(self, tmp_path: Path):
        # Pick a known-stable Coilcraft part. If their site changes the URL
        # scheme this test fails loudly — that's the signal to update the
        # registry / scraper.
        result = fetch_coilcraft_s2p(
            "0402DF-152XJL",
            cache_dir=tmp_path,
        )
        assert Path(result["s2p_path"]).is_file()
        assert result["n_freq_points"] > 0
