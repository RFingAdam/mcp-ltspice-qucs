"""FastMCP server entry point for mcp-rf-analysis."""

from __future__ import annotations

from typing import Annotated, Any, TypeVar

from fastmcp import FastMCP
from pydantic import Field

from mcp_rf_analysis import __version__
from mcp_rf_analysis.bands import (
    is_in_restricted_band,
    list_5gnr_bands,
    list_fcc_restricted_bands,
    list_gnss_bands,
    list_halow_channels,
    list_ism_bands,
    list_lte_bands,
    lookup_band_by_freq,
)
from mcp_rf_analysis.coex import (
    check_coex_matrix as _check_coex_matrix,
)
from mcp_rf_analysis.coex import (
    lookup_harmonic_victims as _lookup_harmonic_victims,
)

# Phase 7
from mcp_rf_analysis.emc import (
    cispr_limit_at as _cispr_limit_at,
)
from mcp_rf_analysis.emc import (
    fcc_part15_radiated_limit_at as _fcc_radiated_limit,
)
from mcp_rf_analysis.emc import (
    predict_conducted_emissions as _predict_conducted,
)
from mcp_rf_analysis.emc import (
    predict_radiated_emissions_loop as _predict_radiated,
)
from mcp_rf_analysis.link import (
    compute_antenna_isolation_estimate as _compute_antenna_isolation_estimate,
)
from mcp_rf_analysis.link import (
    compute_desense as _compute_desense,
)
from mcp_rf_analysis.link import (
    compute_path_loss as _compute_path_loss,
)
from mcp_rf_analysis.network_ops import (
    cascade_networks as _cascade_networks,
)
from mcp_rf_analysis.network_ops import (
    compute_stability as _compute_stability,
)
from mcp_rf_analysis.network_ops import (
    deembed_network as _deembed_network,
)
from mcp_rf_analysis.network_ops import (
    renormalize_impedance as _renormalize_impedance,
)
from mcp_rf_analysis.network_ops import (
    smith_chart_data as _smith_chart_data,
)
from mcp_rf_analysis.si import (
    estimate_fext_db as _estimate_fext,
)
from mcp_rf_analysis.si import (
    estimate_next_db as _estimate_next,
)
from mcp_rf_analysis.si import (
    eye_diagram_from_s2p as _eye_diagram,
)
from mcp_rf_analysis.si import (
    tdr_from_s11 as _tdr,
)
from mcp_rf_analysis.spec_eval import (
    check_passband_compliance as _check_passband_compliance,
)
from mcp_rf_analysis.spec_eval import (
    check_rejection_at as _check_rejection_at,
)
from mcp_rf_analysis.spec_eval import (
    evaluate_against_spec_template as _evaluate_against_spec_template,
)
from mcp_rf_analysis.spec_eval import (
    list_spec_templates as _list_spec_templates,
)
from mcp_rf_analysis.touchstone_utils import (
    compare_sparameters as _compare_sparameters,
)
from mcp_rf_analysis.touchstone_utils import (
    extract_delay as _extract_delay,
)
from mcp_rf_analysis.touchstone_utils import (
    fit_equivalent_circuit as _fit_equivalent_circuit,
)
from rf_mcp_common.envelope import Envelope, Timer, error, ok
from rf_mcp_common.logging import get_logger

T = TypeVar("T")

mcp = FastMCP(name="mcp-rf-analysis", version=__version__)
log = get_logger("mcp_rf_analysis.server")


def _wrap(func: Any, *args: Any, **kwargs: Any) -> Envelope[T]:
    """Run a callable inside the standard envelope contract."""
    timer = Timer()
    try:
        return ok(
            func(*args, **kwargs),
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"{func.__name__} failed: {e}", tool_version=__version__)


# -------- Network operations --------


@mcp.tool(description="Cascade two or more 2-port networks (left-to-right).")
def cascade_networks(s2p_paths: list[str], output_path: str) -> Envelope[dict[str, Any]]:
    return _wrap(lambda: {"output_path": str(_cascade_networks(s2p_paths, output_path))})


@mcp.tool(description="De-embed left/right fixtures from a measured 2-port network.")
def deembed_network(
    measured_s2p: str,
    fixture_left_s2p: str,
    output_path: str,
    fixture_right_s2p: str | None = None,
) -> Envelope[dict[str, Any]]:
    return _wrap(
        lambda: {
            "output_path": str(
                _deembed_network(measured_s2p, fixture_left_s2p, output_path, fixture_right_s2p)
            )
        }
    )


@mcp.tool(description="Renormalize an S-parameter file to a new reference impedance.")
def renormalize_impedance(
    s2p_path: str, new_z0: Annotated[float, Field(gt=0)], output_path: str
) -> Envelope[dict[str, Any]]:
    return _wrap(
        lambda: {"output_path": str(_renormalize_impedance(s2p_path, new_z0, output_path))}
    )


@mcp.tool(description="Compute Rollett K-factor + |Δ| + μ-factor across frequency.")
def compute_stability(s2p_path: str) -> Envelope[dict[str, Any]]:
    return _wrap(_compute_stability, s2p_path)


@mcp.tool(
    description="Return Smith chart data (S_ii real/imag + normalized impedance) for plotting."
)
def smith_chart_data(
    s2p_path: str, port: Annotated[int, Field(ge=1)] = 1
) -> Envelope[dict[str, Any]]:
    return _wrap(_smith_chart_data, s2p_path, port=port)


# -------- Spec evaluation --------


@mcp.tool(description="Check |S21| at a single frequency against a min-rejection target.")
def check_rejection_at(
    s2p_path: str,
    freq_hz: Annotated[float, Field(gt=0)],
    min_rejection_db: Annotated[float, Field(gt=0)],
) -> Envelope[dict[str, Any]]:
    return _wrap(_check_rejection_at, s2p_path, freq_hz, min_rejection_db)


@mcp.tool(description="Check passband insertion loss + return loss across [f_start, f_stop].")
def check_passband_compliance(
    s2p_path: str,
    f_start: Annotated[float, Field(ge=0)],
    f_stop: Annotated[float, Field(gt=0)],
    il_max_db: Annotated[float, Field(gt=0)],
    rl_min_db: Annotated[float, Field(gt=0)],
) -> Envelope[dict[str, Any]]:
    return _wrap(
        _check_passband_compliance,
        s2p_path,
        f_start,
        f_stop,
        il_max_db=il_max_db,
        rl_min_db=rl_min_db,
    )


@mcp.tool(description="Evaluate a .s2p against a bundled spec template (FCC / ETSI / 3GPP).")
def evaluate_against_spec_template(s2p_path: str, template_name: str) -> Envelope[dict[str, Any]]:
    return _wrap(_evaluate_against_spec_template, s2p_path, template_name)


@mcp.tool(description="List names of bundled spec templates.")
def list_spec_templates_tool() -> Envelope[list[str]]:
    return _wrap(_list_spec_templates)


# -------- Regulatory / coex DB --------


@mcp.tool(description="List LTE bands. Optional ``region`` substring filter.")
def list_lte_bands_tool(
    region: str | None = None,
) -> Envelope[list[dict[str, Any]]]:
    return _wrap(list_lte_bands, region)


@mcp.tool(description="List 5G NR bands. ``family`` is 'fr1' or 'fr2'.")
def list_5gnr_bands_tool(family: str = "fr1") -> Envelope[list[dict[str, Any]]]:
    return _wrap(list_5gnr_bands, family)


@mcp.tool(description="List GNSS signals (GPS / GLONASS / Galileo / BeiDou).")
def list_gnss_bands_tool(
    system: str | None = None,
) -> Envelope[list[dict[str, Any]]]:
    return _wrap(list_gnss_bands, system)


@mcp.tool(
    description="List ISM band allocations. ``region`` is the ITU region (1=EMEA, 2=Americas, 3=APAC)."
)
def list_ism_bands_tool(
    region: int | None = None,
) -> Envelope[list[dict[str, Any]]]:
    return _wrap(list_ism_bands, region)


@mcp.tool(
    description="List 802.11ah HaLow channels for a region (US, EU, JP, KR, CN, SG, AU_NZ, IN)."
)
def list_halow_channels_tool(region: str = "US") -> Envelope[dict[str, Any]]:
    return _wrap(list_halow_channels, region)


@mcp.tool(description="Find every band/system that contains a given frequency.")
def lookup_band_by_freq_tool(
    freq_hz: Annotated[float, Field(gt=0)],
) -> Envelope[dict[str, Any]]:
    return _wrap(lookup_band_by_freq, freq_hz)


@mcp.tool(description="List FCC §15.205 restricted bands.")
def list_fcc_restricted_bands_tool() -> Envelope[list[dict[str, Any]]]:
    return _wrap(list_fcc_restricted_bands)


@mcp.tool(description="Check whether a frequency falls into an FCC restricted band.")
def is_in_restricted_band_tool(
    freq_hz: Annotated[float, Field(gt=0)],
) -> Envelope[dict[str, Any]]:
    def _do() -> dict[str, Any]:
        is_in, info = is_in_restricted_band(freq_hz)
        return {"freq_hz": freq_hz, "in_restricted_band": is_in, "info": info}

    return _wrap(_do)


@mcp.tool(
    description="For a TX center frequency, find which RX bands its 2nd / 3rd / etc. harmonics land in."
)
def lookup_harmonic_victims(
    f_center_hz: Annotated[float, Field(gt=0)],
    harmonic_orders: list[int] | None = None,
) -> Envelope[list[dict[str, Any]]]:
    return _wrap(_lookup_harmonic_victims, f_center_hz, harmonic_orders)


@mcp.tool(
    description="Compute the multi-radio coex aggressor × victim matrix with predicted desense."
)
def check_coex_matrix(
    tx_list: list[dict[str, Any]],
    rx_list: list[dict[str, Any]],
    antenna_iso_db: Annotated[float, Field(ge=0)] = 20.0,
    default_filter_rejection_db: Annotated[float, Field(ge=0)] = 0.0,
) -> Envelope[dict[str, Any]]:
    return _wrap(
        _check_coex_matrix,
        tx_list,
        rx_list,
        antenna_iso_db=antenna_iso_db,
        default_filter_rejection_db=default_filter_rejection_db,
    )


# -------- Link budget --------


@mcp.tool(description="Compute Friis or log-distance path loss in dB.")
def compute_path_loss(
    freq_hz: Annotated[float, Field(gt=0)],
    distance_m: Annotated[float, Field(gt=0)],
    model: str = "friis",
    n: float = 2.0,
    extra_loss_db: float = 0.0,
) -> Envelope[dict[str, Any]]:
    return _wrap(
        _compute_path_loss,
        freq_hz,
        distance_m,
        model=model,
        n=n,
        extra_loss_db=extra_loss_db,
    )


@mcp.tool(description="Estimate antenna-to-antenna isolation in dB.")
def compute_antenna_isolation_estimate(
    antenna_separation_m: Annotated[float, Field(gt=0)],
    freq_hz: Annotated[float, Field(gt=0)],
    ground_plane_size_m: float | None = None,
) -> Envelope[dict[str, Any]]:
    return _wrap(
        _compute_antenna_isolation_estimate,
        antenna_separation_m,
        freq_hz,
        ground_plane_size_m=ground_plane_size_m,
    )


@mcp.tool(description="Predict RX desense from an aggressor TX with filter and antenna isolation.")
def compute_desense(
    aggressor_power_dbm: float,
    filter_rejection_db: Annotated[float, Field(ge=0)],
    antenna_iso_db: Annotated[float, Field(ge=0)],
    victim_noise_floor_dbm: float,
) -> Envelope[dict[str, Any]]:
    return _wrap(
        _compute_desense,
        aggressor_power_dbm,
        filter_rejection_db,
        antenna_iso_db,
        victim_noise_floor_dbm,
    )


# -------- Touchstone utilities --------


@mcp.tool(description="Element-wise diff between two .s2p files (S21 dB / S11 dB / mag / phase).")
def compare_sparameters(
    s2p_a: str,
    s2p_b: str,
    metric: str = "s21_db",
) -> Envelope[dict[str, Any]]:
    return _wrap(_compare_sparameters, s2p_a, s2p_b, metric=metric)


@mcp.tool(description="Compute group delay (or unwrapped phase) of S21.")
def extract_delay(
    s2p_path: str,
    method: str = "group_delay",
) -> Envelope[dict[str, Any]]:
    return _wrap(_extract_delay, s2p_path, method)


@mcp.tool(description="Fit a lumped equivalent circuit to a measured 2-port network.")
def fit_equivalent_circuit(
    s2p_path: str,
    topology: str = "series_l_shunt_c",
) -> Envelope[dict[str, Any]]:
    return _wrap(_fit_equivalent_circuit, s2p_path, topology=topology)


# ===========================================================================
# Phase 7: Signal Integrity + EMC pre-compliance
# ===========================================================================


# ----- Signal integrity ----------------------------------------------------


@mcp.tool(
    description=(
        "Time-Domain Reflectometry from S11. Inverse-FFT to time domain "
        "and convert to Z(distance) profile via the substrate's phase "
        "velocity. Useful for locating impedance discontinuities."
    ),
)
def tdr_from_s11(
    s2p_path: str,
    er_eff: Annotated[float, Field(gt=0)] = 4.0,
    window: str = "hann",
) -> Envelope[dict[str, Any]]:
    return _wrap(_tdr, s2p_path, er_eff=er_eff, window=window)


@mcp.tool(
    description=(
        "Compute eye-diagram metrics for a channel given its S2P. "
        "Returns eye height, width (UI), worst-case ISI."
    ),
)
def eye_diagram_from_s2p(
    s2p_path: str,
    bitrate_gbps: Annotated[float, Field(gt=0)],
    n_bits: Annotated[int, Field(gt=0, le=10000)] = 1024,
    swing_v: Annotated[float, Field(gt=0)] = 1.0,
    rise_time_ps: float | None = None,
    seed: int = 0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        m = _eye_diagram(
            s2p_path,
            bitrate_gbps=bitrate_gbps,
            n_bits=n_bits,
            swing_v=swing_v,
            rise_time_ps=rise_time_ps,
            seed=seed,
        )
        return ok(
            {
                "bitrate_gbps": m.bitrate_gbps,
                "n_bits": m.n_bits,
                "eye_height_v": m.eye_height_v,
                "eye_width_ui": m.eye_width_ui,
                "isi_pp_v": m.isi_pp_v,
                "sample_point_v_top": m.sample_point_v_top,
                "sample_point_v_bot": m.sample_point_v_bot,
                "notes": m.notes,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"eye_diagram_from_s2p failed: {e}", tool_version=__version__)


@mcp.tool(description="Near-End Crosstalk (NEXT) estimate for two coupled traces.")
def estimate_next_db(
    coupling_length_mm: Annotated[float, Field(gt=0)],
    trace_separation_mm: Annotated[float, Field(gt=0)],
    substrate_height_mm: Annotated[float, Field(gt=0)],
    rise_time_ps: Annotated[float, Field(gt=0)],
    er: Annotated[float, Field(gt=0)] = 4.0,
) -> Envelope[dict[str, float]]:
    return _wrap(
        _estimate_next,
        coupling_length_mm=coupling_length_mm,
        trace_separation_mm=trace_separation_mm,
        substrate_height_mm=substrate_height_mm,
        rise_time_ps=rise_time_ps,
        er=er,
    )


@mcp.tool(description="Far-End Crosstalk (FEXT) estimate for two coupled traces.")
def estimate_fext_db(
    coupling_length_mm: Annotated[float, Field(gt=0)],
    trace_separation_mm: Annotated[float, Field(gt=0)],
    substrate_height_mm: Annotated[float, Field(gt=0)],
    rise_time_ps: Annotated[float, Field(gt=0)],
    er: Annotated[float, Field(gt=0)] = 4.0,
) -> Envelope[dict[str, float]]:
    return _wrap(
        _estimate_fext,
        coupling_length_mm=coupling_length_mm,
        trace_separation_mm=trace_separation_mm,
        substrate_height_mm=substrate_height_mm,
        rise_time_ps=rise_time_ps,
        er=er,
    )


# ----- EMC pre-compliance --------------------------------------------------


@mcp.tool(description="Conducted-emissions limit (dBuV) at a frequency for CISPR 22 / FCC 15B.")
def cispr_limit_at(
    freq_hz: Annotated[float, Field(gt=0)],
    standard: str = "cispr22_b",
) -> Envelope[dict[str, float]]:
    timer = Timer()
    try:
        limit = _cispr_limit_at(freq_hz, standard=standard)  # type: ignore[arg-type]
        return ok(
            {"freq_hz": freq_hz, "standard": standard, "limit_dbuv": limit},
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"cispr_limit_at failed: {e}", tool_version=__version__)


@mcp.tool(
    description=(
        "Convert an AC line-current spectrum to LISN voltage and check "
        "against CISPR 22 / FCC 15B conducted-emissions limits."
    ),
)
def predict_conducted_emissions(
    line_current_spectrum: list[list[float]],
    standard: str = "cispr22_b",
    margin_db: Annotated[float, Field(ge=0)] = 6.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        spectrum = [(float(f), float(i)) for f, i in line_current_spectrum]
        return ok(
            _predict_conducted(spectrum, standard=standard, margin_db=margin_db),  # type: ignore[arg-type]
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(
            f"predict_conducted_emissions failed: {e}",
            tool_version=__version__,
        )


@mcp.tool(description="FCC Part 15.109 Class B radiated-emissions limit (dBuV/m) at distance.")
def fcc_part15_radiated_limit_at(
    freq_hz: Annotated[float, Field(gt=0)],
    distance_m: Annotated[float, Field(gt=0)] = 3.0,
) -> Envelope[dict[str, float]]:
    timer = Timer()
    try:
        return ok(
            {
                "freq_hz": freq_hz,
                "distance_m": distance_m,
                "limit_dbuv_per_m": _fcc_radiated_limit(freq_hz, distance_m=distance_m),
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(
            f"fcc_part15_radiated_limit_at failed: {e}",
            tool_version=__version__,
        )


@mcp.tool(
    description=(
        "Estimate radiated E-field from a current-carrying loop (small-loop "
        "approximation). Useful first-order radiated-EMI check."
    ),
)
def predict_radiated_emissions_loop(
    current_a: Annotated[float, Field(gt=0)],
    loop_area_cm2: Annotated[float, Field(gt=0)],
    freq_hz: Annotated[float, Field(gt=0)],
    measurement_distance_m: Annotated[float, Field(gt=0)] = 3.0,
) -> Envelope[dict[str, float]]:
    return _wrap(
        _predict_radiated,
        current_a=current_a,
        loop_area_cm2=loop_area_cm2,
        freq_hz=freq_hz,
        measurement_distance_m=measurement_distance_m,
    )


def main() -> None:
    log.info("starting mcp-rf-analysis", extra={"version": __version__})
    mcp.run()


if __name__ == "__main__":
    main()
