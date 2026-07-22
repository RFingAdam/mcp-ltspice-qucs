"""Server-layer contract tests for mcp-rf-analysis.

Until now nothing in the suite imported ``mcp_rf_analysis.server`` at
all: all 33 tools were exercised only through their underlying module
functions, so the envelope wrapper, the ``Annotated``/``Field`` schemas,
and the tool registration itself were entirely untested.

These tests cover the contract every tool shares — an agent calling a
tool with bad input must get a usable ``status="error"`` envelope naming
the tool, not a traceback and not a message naming something it never
called.
"""

from __future__ import annotations

import inspect

import pytest

from mcp_rf_analysis import server


def _required_params(func: object) -> list[str]:
    return [
        name
        for name, p in inspect.signature(func).parameters.items()
        if p.default is inspect.Parameter.empty
    ]


def _tool_functions() -> dict[str, object]:
    """Every registered MCP tool on the server module.

    Discovered by introspection rather than hand-listed so a newly added
    tool is covered automatically instead of quietly escaping the sweep.
    """
    tools = {}
    for name, obj in vars(server).items():
        if name.startswith("_") or not inspect.isfunction(obj):
            continue
        if obj.__module__ != server.__name__:
            continue
        ann = inspect.signature(obj).return_annotation
        if "Envelope" in str(ann):
            tools[name] = obj
    return tools


def test_tool_surface_is_discoverable() -> None:
    """Guards the sweep below: if this drops to ~0 the sweep is vacuous."""
    assert len(_tool_functions()) >= 30


@pytest.mark.parametrize("tool_name", sorted(_tool_functions()))
def test_every_tool_returns_named_error_envelope(tool_name: str) -> None:
    """A bad call must produce an error envelope that names *this* tool.

    Regression guard for `_wrap`: it reported ``func.__name__``, but most
    tools pass a lambda, so failures surfaced to the agent as
    "<lambda> failed: ..." with no indication of which tool broke.
    """
    func = _tool_functions()[tool_name]
    required = _required_params(func)
    if not required:
        # Every parameter is optional, so calling it "wrongly" would just
        # be calling it correctly. Filter-value rejection for these is
        # covered by test_band_filters_reject_unknown_values below.
        pytest.skip("no required argument to make invalid")

    # Feed every required parameter a path that does not exist. Whatever
    # the tool does with it, it must come back as an envelope, not raise.
    kwargs = dict.fromkeys(required, "/nonexistent/definitely-not-a-real-file.s2p")

    env = func(**kwargs)
    assert env.status == "error", f"{tool_name} accepted garbage input"
    assert env.error is not None
    assert env.error.startswith(tool_name), (
        f"{tool_name} error envelope is attributed to {env.error.split(' ')[0]!r}"
    )


def test_error_envelope_carries_no_data() -> None:
    env = server.cascade_networks(["/nope1.s2p", "/nope2.s2p"], "/tmp/out.s2p")
    assert env.status == "error"
    assert env.data is None
    assert "tool_version" in env.metadata


def test_list_spec_templates_returns_ok_envelope() -> None:
    env = server.list_spec_templates_tool()
    assert env.status == "ok"
    assert isinstance(env.data, list)


@pytest.mark.parametrize(
    ("tool_name", "bad_filter"),
    [
        ("list_lte_bands_tool", "Atlantis"),
        ("list_gnss_bands_tool", "NAVSTAR-9000"),
        ("list_ism_bands_tool", 99),
    ],
)
def test_band_filters_reject_unknown_values(tool_name: str, bad_filter: object) -> None:
    """An unrecognized filter must not read as "there are none".

    These three previously returned ok with an empty list, so an agent
    that misspelled a region got a confident, wrong "no such bands"
    instead of a correctable error. Their siblings list_5gnr_bands and
    list_halow_channels always raised; this makes the set consistent.
    """
    env = getattr(server, tool_name)(bad_filter)
    assert env.status == "error"
    assert "available" in env.error
