"""Shared fixtures and simulator-availability markers for mcp-qucs-s.

This package had no conftest at all, so the ``qucs`` marker registered in
the root pyproject.toml was applied to exactly zero tests and nothing was
gated on the binary being present.
"""

from __future__ import annotations

import pytest

from mcp_qucs_s.runner import find_qucs_s, find_xyce

HAS_QUCS = find_qucs_s() is not None
HAS_XYCE = find_xyce() is not None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    skip_qucs = pytest.mark.skip(reason="qucs-s / qucsator not installed")
    skip_xyce = pytest.mark.skip(reason="Xyce not installed")
    for item in items:
        # get_closest_marker rather than `"qucs" in item.keywords`: keywords
        # also holds test names and parametrize ids, so anything merely named
        # after the simulator would be skipped as though it required it.
        if item.get_closest_marker("qucs") and not HAS_QUCS:
            item.add_marker(skip_qucs)
        if item.get_closest_marker("xyce") and not HAS_XYCE:
            item.add_marker(skip_xyce)


@pytest.fixture
def qucs_dat(tmp_path):
    """A minimal well-formed Qucs-S .dat for a 3-point 2-port sweep.

    Hand-written rather than captured from a run so the tests exercise
    the parser without needing Qucs-S installed — which is the whole
    point, since this is the only code that runs once a user *does*
    install it.
    """

    def _block(name: str, values: list[float], kind: str = "dep") -> str:
        header = (
            f"<indep {name} {len(values)}>" if kind == "indep" else f"<dep {name} dep frequency>"
        )
        body = "\n".join(f"  {v!r}" for v in values)
        return f"{header}\n{body}\n</{kind}>\n"

    text = (
        _block("frequency", [1.0e9, 2.0e9, 3.0e9], kind="indep")
        + _block("S[1,1].r", [0.1, 0.2, 0.3])
        + _block("S[1,1].i", [-0.01, -0.02, -0.03])
        + _block("S[1,2].r", [0.9, 0.8, 0.7])
        + _block("S[1,2].i", [0.05, 0.06, 0.07])
        + _block("S[2,1].r", [0.9, 0.8, 0.7])
        + _block("S[2,1].i", [0.05, 0.06, 0.07])
        + _block("S[2,2].r", [0.15, 0.25, 0.35])
        + _block("S[2,2].i", [-0.02, -0.03, -0.04])
    )
    path = tmp_path / "sim.dat"
    path.write_text(text, encoding="utf-8")
    return path
