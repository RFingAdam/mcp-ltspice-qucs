"""Plot S-parameter responses as PNG."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless backend; safe for CI / MCP
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from rf_mcp_common.touchstone import read_touchstone  # noqa: E402


def render_response(
    s2p_path: str | Path,
    output_png: str | Path,
    *,
    freq_range: tuple[float, float] | None = None,
    markers: list[tuple[float, str]] | None = None,
    show_s11: bool = True,
    title: str | None = None,
    figsize: tuple[float, float] = (10.0, 6.0),
    dpi: int = 120,
) -> Path:
    """Render an S₂ₚ Bode plot and save as PNG.

    - ``freq_range``: optional (f_min_hz, f_max_hz). Defaults to full sweep.
    - ``markers``: list of ``(freq_hz, label)`` for vertical guide lines.
    - ``show_s11``: overlay |S11| on a secondary y-axis.

    Returns the resolved PNG path.
    """
    net = read_touchstone(s2p_path)
    f = net.f
    if freq_range is not None:
        mask = (f >= freq_range[0]) & (f <= freq_range[1])
    else:
        mask = np.ones_like(f, dtype=bool)
    f_plot = f[mask]
    s21_db = 20 * np.log10(np.maximum(np.abs(net.s[mask, 1, 0]), 1e-12))

    fig, ax21 = plt.subplots(figsize=figsize, dpi=dpi)
    ax21.plot(f_plot / 1e6, s21_db, color="C0", lw=1.5, label="|S21|")
    ax21.set_xlabel("Frequency [MHz]")
    ax21.set_ylabel("|S21| [dB]", color="C0")
    ax21.tick_params(axis="y", labelcolor="C0")
    ax21.grid(True, which="both", alpha=0.3)
    ax21.set_xscale("log")

    if show_s11:
        s11_db = 20 * np.log10(np.maximum(np.abs(net.s[mask, 0, 0]), 1e-12))
        ax11 = ax21.twinx()
        ax11.plot(f_plot / 1e6, s11_db, color="C3", lw=1.0, ls="--", label="|S11|")
        ax11.set_ylabel("|S11| [dB]", color="C3")
        ax11.tick_params(axis="y", labelcolor="C3")
        ax11.set_ylim(-40, 5)

    if markers:
        ymin, ymax = ax21.get_ylim()
        for freq_hz, label in markers:
            ax21.axvline(freq_hz / 1e6, color="black", alpha=0.4, lw=0.8, ls=":")
            ax21.text(
                freq_hz / 1e6, ymax - 0.05 * (ymax - ymin), label,
                rotation=90, va="top", ha="right", fontsize=8, alpha=0.7,
            )

    ax21.set_title(title or Path(s2p_path).stem)
    fig.tight_layout()

    out = Path(output_png).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)
    plt.close(fig)
    return out
