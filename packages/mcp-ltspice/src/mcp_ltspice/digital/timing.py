"""Synchronous logic timing analysis.

Static-timing-style checks for a digital path: launch FF → combinational
delay → capture FF. Reports setup / hold margins given the standard
parameters from a digital library datasheet.

These are first-order analytical checks — they don't replace a real
STA tool but they catch the common failure modes early in design.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TimingPath:
    """One launch → comb → capture timing path."""

    name: str
    clk_period_ns: float
    t_clk_q_ns: float  # launch FF clk-to-Q delay
    t_comb_ns: float  # combinational logic delay (max)
    t_setup_ns: float  # capture FF setup time
    t_hold_ns: float  # capture FF hold time
    t_skew_ns: float = 0.0  # clock skew (capture - launch); positive = capture later
    t_jitter_ns: float = 0.0  # peak-to-peak clock jitter


@dataclass
class SetupHoldResult:
    setup_slack_ns: float  # positive = pass
    hold_slack_ns: float  # positive = pass
    setup_status: str  # "pass" | "fail"
    hold_status: str  # "pass" | "fail"
    max_safe_clock_mhz: float
    notes: list[str]


def check_setup_hold(path: TimingPath) -> SetupHoldResult:
    """Compute setup / hold slack for a single timing path.

    Setup slack = T_clk - T_clk_q_max - T_comb_max - T_setup - T_jitter + T_skew
    Hold slack  = T_clk_q_min + T_comb_min - T_hold - T_skew

    Positive slack = pass. Negative = timing violation.
    For simplicity we assume min-delay = max-delay (no specific min-times
    provided); report optimistic hold check.
    """
    setup_slack = (
        path.clk_period_ns
        - path.t_clk_q_ns
        - path.t_comb_ns
        - path.t_setup_ns
        - path.t_jitter_ns
        + path.t_skew_ns
    )
    hold_slack = path.t_clk_q_ns + path.t_comb_ns - path.t_hold_ns - path.t_skew_ns

    notes: list[str] = []
    if setup_slack < 0:
        notes.append(
            f"SETUP VIOLATION: need to either reduce comb delay by "
            f"{abs(setup_slack):.2f} ns, lower clock freq, or pipeline."
        )
    if hold_slack < 0:
        notes.append(
            f"HOLD VIOLATION: add buffer delay >= {abs(hold_slack):.2f} ns "
            f"in the data path, or reduce clock skew."
        )
    if setup_slack > 0 and setup_slack < path.clk_period_ns * 0.05:
        notes.append("Setup slack <5% of clock period — very tight, no margin.")

    # Maximum safe clock = current period - setup_slack (if positive)
    if setup_slack > 0:
        # Working period: T_clk - setup_slack (the "consumed" part of T_clk)
        consumed = (
            path.t_clk_q_ns + path.t_comb_ns + path.t_setup_ns + path.t_jitter_ns - path.t_skew_ns
        )
        max_safe_period_ns = consumed
        max_safe_freq_mhz = 1000.0 / max_safe_period_ns if max_safe_period_ns > 0 else 0.0
    else:
        max_safe_freq_mhz = 0.0  # current freq already fails

    return SetupHoldResult(
        setup_slack_ns=setup_slack,
        hold_slack_ns=hold_slack,
        setup_status="pass" if setup_slack >= 0 else "fail",
        hold_status="pass" if hold_slack >= 0 else "fail",
        max_safe_clock_mhz=max_safe_freq_mhz,
        notes=notes,
    )


def propagation_delay(
    *,
    n_gates: int,
    t_gate_avg_ns: float,
    t_wire_per_mm_ns: float = 0.005,  # ~5 ps/mm for FR-4 microstrip
    wire_length_mm: float = 0.0,
    fanout: int = 1,
    t_per_fanout_ns: float = 0.05,
) -> dict[str, float]:
    """Estimate combinational propagation delay through a logic chain.

    First-order: sum gate delays + wire delays + fanout penalty.

    - n_gates: number of gates in the longest path
    - t_gate_avg_ns: average gate delay (datasheet typ value)
    - wire_length_mm: total interconnect length on the chain
    - t_wire_per_mm_ns: wire-induced delay (~5 ps/mm for typical FR-4
      microstrip; doubles for stripline)
    - fanout: average gate fanout in the chain
    - t_per_fanout_ns: extra delay per unit fanout (~50 ps for std cells)
    """
    if n_gates <= 0:
        raise ValueError("n_gates must be positive")
    gate_delay = n_gates * t_gate_avg_ns
    wire_delay = wire_length_mm * t_wire_per_mm_ns
    fanout_penalty = max(0, fanout - 1) * t_per_fanout_ns * n_gates
    total = gate_delay + wire_delay + fanout_penalty
    return {
        "gate_delay_ns": gate_delay,
        "wire_delay_ns": wire_delay,
        "fanout_penalty_ns": fanout_penalty,
        "total_delay_ns": total,
        "max_freq_mhz": 1000.0 / total if total > 0 else float("inf"),
    }
