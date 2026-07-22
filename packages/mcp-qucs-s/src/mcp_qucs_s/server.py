"""FastMCP server entry point for mcp-qucs-s."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from mcp_qucs_s import __version__
from mcp_qucs_s.couplers import synthesize_coupler as _synthesize_coupler
from mcp_qucs_s.harmonic_balance import analyze as _hb_analyze
from mcp_qucs_s.harmonic_balance import sweep_compression as _hb_sweep_compression
from mcp_qucs_s.microstrip import (
    Substrate,
    analyze_microstrip,
)
from mcp_qucs_s.microstrip import (
    synthesize_microstrip_line as _synthesize_microstrip_line,
)
from mcp_qucs_s.netlist import generate_ladder_netlist as _generate_ladder_netlist
from mcp_qucs_s.richards import lumped_to_distributed as _lumped_to_distributed
from mcp_qucs_s.runner import (
    is_qucs_available,
    is_xyce_available,
    run_qucs,
)
from mcp_qucs_s.sparams import dat_to_touchstone
from mcp_qucs_s.substrates import (
    get_substrate as _get_substrate_preset,
)
from mcp_qucs_s.substrates import (
    list_substrate_presets as _list_substrate_presets,
)
from rf_mcp_common.envelope import Envelope, Timer, error, ok
from rf_mcp_common.logging import get_logger

mcp = FastMCP(name="mcp-qucs-s", version=__version__)
log = get_logger("mcp_qucs_s.server")


def _substrate(d: dict[str, float] | str) -> Substrate:
    """Coerce either a preset-name string or a parameter dict into a Substrate.

    String inputs look up `mcp_qucs_s.substrates.SUBSTRATE_PRESETS`. Dict
    inputs require `er` and `h_mm` keys; `t_um` and `tan_d` default to
    35 µm and 0.02 if absent.
    """
    if isinstance(d, str):
        return _get_substrate_preset(d)
    return Substrate(
        er=d["er"],
        h_mm=d["h_mm"],
        t_um=d.get("t_um", 35.0),
        tan_d=d.get("tan_d", 0.02),
    )


# ---------------------------------------------------------------------------
# Status / capability discovery
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "List curated substrate presets (FR4, Rogers RO4350B / RO4003C, "
        "Duroid 5880 / 6002, PTFE, Isola FR408HR, Taconic TLY5) with their "
        "{er, h_mm, t_um, tan_d} values. Pass a preset name as the `substrate` "
        "argument to `synthesize_microstrip_line` and `analyze_microstrip_tool` "
        "instead of the full dict."
    )
)
def list_substrate_presets_tool() -> Envelope[list[dict[str, Any]]]:
    timer = Timer()
    try:
        return ok(
            _list_substrate_presets(),
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"list_substrate_presets failed: {e}", tool_version=__version__)


@mcp.tool(description="Report whether Qucs-S and Xyce are installed and discoverable.")
def status() -> Envelope[dict[str, Any]]:
    return ok(
        {
            "version": __version__,
            "qucs_s_available": is_qucs_available(),
            "xyce_available": is_xyce_available(),
            "synthesis_tools": [
                "synthesize_microstrip_line",
                "analyze_microstrip",
                "synthesize_coupler",
                "lumped_to_distributed",
            ],
            "sim_tools_requiring_qucs_s": [
                "run_sp_analysis",
                "extract_noise_parameters",
                "export_touchstone",
            ],
            "sim_tools_requiring_xyce": ["run_harmonic_balance", "sweep_compression_point"],
        },
        tool_version=__version__,
    )


# ---------------------------------------------------------------------------
# Tools 3-5: Microstrip + coupler synthesis (no simulator needed)
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Synthesize microstrip line dimensions for a target characteristic "
        "impedance and electrical length. Hammerstad-Jensen closed form. "
        "NOTE: this is the **synthesis** direction (Z₀, length, freq → W, L). "
        "For impedance **analysis** of an existing trace from PCB geometry, "
        "prefer a PCB-layout-aware EMC MCP if one is available."
    ),
)
def synthesize_microstrip_line(
    z0_ohm: Annotated[float, Field(gt=0)],
    electrical_length_deg: Annotated[float, Field(ge=0, le=720)],
    freq_hz: Annotated[float, Field(gt=0)],
    substrate: Annotated[
        dict[str, float] | str,
        Field(
            description=(
                "Either a preset name (e.g. 'FR4_0254', 'Rogers4350B_0508', "
                "'Duroid5880_0508' — see `list_substrate_presets_tool`) OR a "
                "parameter dict {er, h_mm, t_um (default 35), tan_d (default 0.02)}."
            )
        ),
    ],
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        sub = _substrate(substrate)
        line = _synthesize_microstrip_line(z0_ohm, electrical_length_deg, freq_hz, sub)
        return ok(
            {
                "z0_ohm": line.z0,
                "width_mm": line.width_mm,
                "length_mm": line.length_mm,
                "eff_permittivity": line.eff_permittivity,
                "wavelength_eff_mm": line.wavelength_eff_mm,
                "metadata": line.metadata,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"synthesize_microstrip_line failed: {e}", tool_version=__version__)


@mcp.tool(
    description=(
        "Analyze an existing microstrip line: Z0, eps_eff, wavelength. "
        "Hammerstad-Jensen closed form. NOTE: for PCB impedance analysis from "
        "stackup + trace data, prefer a PCB-layout-aware EMC MCP if one is "
        "available — those tools integrate with the wider PCB analysis "
        "workflow (CPW, stripline, differential, eye-diagram)."
    )
)
def analyze_microstrip_tool(
    width_mm: Annotated[float, Field(gt=0)],
    substrate: dict[str, float] | str,
    freq_hz: Annotated[float, Field(gt=0)] = 1e9,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        return ok(
            analyze_microstrip(width_mm, _substrate(substrate), freq_hz),
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"analyze_microstrip failed: {e}", tool_version=__version__)


@mcp.tool(
    description=(
        "Synthesize a directional coupler: branch_line / rat_race / "
        "coupled_line / lange. Returns per-section dimensions."
    ),
)
def synthesize_coupler(
    kind: Annotated[str, Field(description="branch_line | rat_race | coupled_line | lange")],
    coupling_db: Annotated[float, Field(gt=0, le=30)],
    freq_hz: Annotated[float, Field(gt=0)],
    z0_ohm: Annotated[float, Field(gt=0)],
    substrate: dict[str, float],
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        sub = _substrate(substrate)
        result = _synthesize_coupler(kind, coupling_db, freq_hz, z0_ohm, sub)  # type: ignore[arg-type]
        return ok(
            {
                "kind": result.kind,
                "coupling_db": result.coupling_db,
                "freq_hz": result.freq_hz,
                "z0_ohm": result.z0,
                "sections": result.sections,
                "notes": result.notes,
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"synthesize_coupler failed: {e}", tool_version=__version__)


@mcp.tool(
    description=(
        "Convert a lumped LC ladder to its distributed-element microstrip "
        "equivalent via Richards transformation + Kuroda identities."
    ),
)
def lumped_to_distributed(
    components: dict[str, float],
    cutoff_hz: Annotated[float, Field(gt=0)],
    substrate: dict[str, float],
    z0_ohm: Annotated[float, Field(gt=0)] = 50.0,
    apply_kuroda: bool = True,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        sub = _substrate(substrate)
        result = _lumped_to_distributed(
            components,
            cutoff_hz,
            z0=z0_ohm,
            substrate=sub,
            apply_kuroda=apply_kuroda,
        )
        return ok(result, runtime_sec=timer.elapsed(), tool_version=__version__)
    except Exception as e:
        return error(f"lumped_to_distributed failed: {e}", tool_version=__version__)


# ---------------------------------------------------------------------------
# Tools 1, 6, 8: Simulator-driven (need Qucs-S installed)
# ---------------------------------------------------------------------------


@mcp.tool(
    description=(
        "Run native Qucs-S S-parameter analysis on a qucsator netlist "
        "(generate one with simulate_lc_ladder, or hand-write it; this is "
        "the netlist format, not the GUI .sch file). "
        "Requires Qucs-S installed (see docs/installation.md). Output is "
        "a Touchstone .s2p."
    ),
)
def run_sp_analysis(
    netlist_path: str,
    output_s2p: str,
    timeout_sec: Annotated[float, Field(gt=0, le=600)] = 300.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        if not is_qucs_available():
            return error(
                "Qucs-S not installed. See docs/installation.md to build "
                "from source: github.com/ra3xdh/qucs_s",
                tool_version=__version__,
            )
        result = run_qucs(netlist_path, timeout_sec=timeout_sec)
        s2p = dat_to_touchstone(result.output_path, output_s2p)
        return ok(
            {"s2p_path": str(s2p), "dat_path": str(result.output_path)},
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"run_sp_analysis failed: {e}", tool_version=__version__)


@mcp.tool(
    description=(
        "Design-to-Touchstone in one call: build a Qucs netlist for a lumped "
        "LC ladder, simulate it with qucsator, and write S-parameters as a "
        ".s2p file. Each element states its position explicitly, e.g. "
        "{'kind': 'series_l', 'L': 7.96e-9} or {'kind': 'shunt_c', 'C': 6.37e-12}. "
        "Kinds: series_l, series_c, shunt_l, shunt_c, shunt_lc_trap, "
        "shunt_lc_parallel, series_lc_series, series_lc_parallel. LC kinds take "
        "both L and C. Elements are ordered source to load."
    ),
)
def simulate_lc_ladder(
    elements: Annotated[
        list[dict[str, Any]],
        Field(description="Ordered source-to-load elements, each with 'kind' plus L and/or C."),
    ],
    output_s2p: Annotated[str, Field(description="Path for the output .s2p file.")],
    z0: Annotated[float, Field(gt=0)] = 50.0,
    f_start_hz: Annotated[float, Field(gt=0)] = 1e6,
    f_stop_hz: Annotated[float, Field(gt=0)] = 5e9,
    points: Annotated[int, Field(ge=2, le=100_000)] = 200,
    netlist_path: Annotated[
        str | None,
        Field(description="Where to keep the generated netlist. Default: beside the .s2p."),
    ] = None,
    timeout_sec: Annotated[float, Field(gt=0, le=600)] = 300.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        if not is_qucs_available():
            return error(
                "Qucs-S not installed. See docs/installation.md to build "
                "from source: github.com/ra3xdh/qucs_s",
                tool_version=__version__,
            )

        parsed: list[tuple[str, dict[str, float]]] = []
        for i, raw in enumerate(elements, start=1):
            if "kind" not in raw:
                return error(
                    f"Element {i} has no 'kind'. Each element needs a kind plus "
                    "its L and/or C value, e.g. {'kind': 'shunt_c', 'C': 6.37e-12}.",
                    tool_version=__version__,
                )
            params = {k: float(v) for k, v in raw.items() if k in ("L", "C")}
            parsed.append((str(raw["kind"]), params))

        s2p_path = Path(output_s2p).expanduser().resolve()
        net_path = (
            Path(netlist_path).expanduser().resolve()
            if netlist_path
            else s2p_path.with_suffix(".net")
        )
        net = _generate_ladder_netlist(
            parsed,
            net_path,
            z0=z0,
            f_start_hz=f_start_hz,
            f_stop_hz=f_stop_hz,
            points=points,
        )
        result = run_qucs(net, timeout_sec=timeout_sec)
        s2p = dat_to_touchstone(result.output_path, s2p_path, z0=z0)
        return ok(
            {
                "s2p_path": str(s2p),
                "netlist_path": str(net),
                "dat_path": str(result.output_path),
                "n_elements": len(parsed),
            },
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"simulate_lc_ladder failed: {e}", tool_version=__version__)


@mcp.tool(
    description=(
        "Run harmonic-balance analysis via the Xyce backend. Returns the "
        "spectral content at all mixing products. Requires Xyce installed."
    ),
)
def run_harmonic_balance(
    dut_netlist: Annotated[
        list[str],
        Field(
            description=(
                "Raw SPICE lines for the circuit under test — devices, .SUBCKT "
                "and .MODEL cards — referring to the in_node and out_node names. "
                "Do not include sources, termination or analysis directives; "
                "those are added here. Use explicit multiplication in B-source "
                "expressions (V(in)*V(in)*V(in)); the '^' operator makes Xyce's "
                "HB startup transient diverge."
            )
        ),
    ],
    fundamentals_hz: Annotated[
        list[float],
        Field(description="One tone for harmonic distortion, two for intermod."),
    ],
    harmonics: Annotated[int, Field(ge=1, le=32)] = 5,
    input_power_dbm: float = -20.0,
    in_node: str = "in",
    out_node: str = "out",
    z0: Annotated[float, Field(gt=0)] = 50.0,
    timeout_sec: Annotated[float, Field(gt=0, le=600)] = 300.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        if not is_xyce_available():
            return error(
                "Xyce not installed. Build it from source — see "
                "docs/installation.md. Sandia's Linux binaries are RHEL RPMs "
                "that do not run on Debian/Ubuntu, and they no longer ship "
                "open-source builds.",
                tool_version=__version__,
            )

        result = _hb_analyze(
            dut_netlist,
            fundamentals_hz=fundamentals_hz,
            harmonics=harmonics,
            input_power_dbm=input_power_dbm,
            in_node=in_node,
            out_node=out_node,
            z0=z0,
            timeout_sec=timeout_sec,
        )

        payload: dict[str, Any] = {
            "fundamentals_hz": result.fundamentals_hz,
            "fundamental_dbm": result.fundamental_dbm,
            "input_power_dbm": result.input_power_dbm,
            "gain_db": result.gain_db,
            "spectrum": result.spectrum.top(20),
            "netlist_path": str(result.netlist_path),
            "output_path": str(result.output_path),
        }
        env_warnings: list[str] = []
        if result.im3_dbm is not None:
            payload |= {
                "im3_dbm": result.im3_dbm,
                "im3_freqs_hz": result.im3_freqs_hz,
                "oip3_dbm": result.oip3_dbm,
                "iip3_dbm": result.iip3_dbm,
            }
            # The single-point extrapolation assumes the products still sit on
            # their 3:1 slope. Near compression they do not, and IIP3 reads low.
            if result.gain_db is not None and result.fundamental_dbm:
                env_warnings.append(
                    "IIP3 is extrapolated from one drive level, which assumes the "
                    "third-order products are still on their 3:1 slope. Confirm by "
                    "re-running a few dB lower and checking IIP3 does not move."
                )

        env: Envelope[dict[str, Any]] = ok(
            payload, runtime_sec=timer.elapsed(), tool_version=__version__
        )
        env.warnings.extend(env_warnings)
        return env
    except Exception as e:
        return error(f"run_harmonic_balance failed: {e}", tool_version=__version__)


@mcp.tool(
    description=(
        "Sweep drive level through harmonic balance and locate the 1 dB "
        "gain-compression point (P1dB). Requires Xyce. The lowest power swept "
        "sets the small-signal gain reference, so keep it well below compression."
    ),
)
def sweep_compression_point(
    dut_netlist: list[str],
    fundamental_hz: float,
    input_powers_dbm: list[float],
    harmonics: Annotated[int, Field(ge=1, le=32)] = 5,
    in_node: str = "in",
    out_node: str = "out",
    z0: Annotated[float, Field(gt=0)] = 50.0,
    timeout_sec: Annotated[float, Field(gt=0, le=600)] = 300.0,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        if not is_xyce_available():
            return error(
                "Xyce not installed. See docs/installation.md for the source build.",
                tool_version=__version__,
            )
        data = _hb_sweep_compression(
            dut_netlist,
            fundamental_hz=fundamental_hz,
            input_powers_dbm=input_powers_dbm,
            harmonics=harmonics,
            in_node=in_node,
            out_node=out_node,
            z0=z0,
            timeout_sec=timeout_sec,
        )
        env: Envelope[dict[str, Any]] = ok(
            data, runtime_sec=timer.elapsed(), tool_version=__version__
        )
        if data.get("p1db_in_dbm") is None:
            env.warnings.append(
                "Gain never compressed by 1 dB across the swept range, so P1dB is "
                "not bracketed. Extend the sweep to higher drive levels."
            )
        return env
    except Exception as e:
        return error(f"sweep_compression_point failed: {e}", tool_version=__version__)


@mcp.tool(
    description="Run Qucs-S sim and export S-parameters to Touchstone in one call.",
)
def export_touchstone(
    netlist_path: str,
    output_s2p: str,
) -> Envelope[dict[str, Any]]:
    timer = Timer()
    try:
        if not is_qucs_available():
            return error(
                "Qucs-S not installed. See docs/installation.md.",
                tool_version=__version__,
            )
        result = run_qucs(netlist_path)
        s2p = dat_to_touchstone(result.output_path, output_s2p)
        return ok(
            {"s2p_path": str(Path(s2p).resolve())},
            runtime_sec=timer.elapsed(),
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"export_touchstone failed: {e}", tool_version=__version__)


@mcp.tool(
    description=(
        "Extract noise parameters (Fmin, Gamma_opt, Rn) from a Qucs-S "
        "noise analysis. Scaffolded — requires Qucs-S installed."
    ),
)
def extract_noise_parameters(
    netlist_path: str,
    f_start_hz: Annotated[float, Field(gt=0)],
    f_stop_hz: Annotated[float, Field(gt=0)],
) -> Envelope[dict[str, Any]]:
    try:
        if not is_qucs_available():
            return error("Qucs-S not installed.", tool_version=__version__)
        # Qucs-S noise-analysis output parsing (Fmin / Γopt / Rn / NF50)
        # is not yet implemented. Tracked as a Tier-6 roadmap item; see CHANGELOG.
        return error(
            "extract_noise_parameters is not yet implemented. Qucs-S is "
            "installed but the noise-analysis dataset parser (Fmin, "
            "Gamma_opt, Rn, NF50) is pending. Tracked as a Tier-6 roadmap "
            "item; see CHANGELOG. Use SPICE .NOISE in mcp-ltspice as an "
            "interim workaround for input-referred noise spectral density.",
            tool_version=__version__,
        )
    except Exception as e:
        return error(f"extract_noise_parameters failed: {e}", tool_version=__version__)


def main() -> None:
    log.info("starting mcp-qucs-s", extra={"version": __version__})
    mcp.run()


if __name__ == "__main__":
    main()
