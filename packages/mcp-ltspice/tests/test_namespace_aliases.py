"""Tests for tool namespacing — both flat and namespaced names work, both
point to the same implementation, and every category is represented.

Background: the package previously had 30+ tools in a flat namespace,
making domain-driven discovery hard for LLM agents. Each tool now also
registers under a `<category>.<name>` alias (e.g. ``filter.synthesize_lc``
for ``synthesize_lc_filter``). Both names continue to work; the
namespaced form is preferred and the flat form will be deprecated in a
future major release.
"""

from __future__ import annotations

import asyncio

import pytest

from mcp_ltspice import server


CATEGORIES = {"filter", "analog", "power", "digital", "vendor", "sim"}


def _registered_tool_names() -> set[str]:
    """Inspect the FastMCP tool registry synchronously by driving the
    async `list_tools` through ``asyncio.run``."""
    tools = asyncio.run(server.mcp.list_tools())
    return {t.name for t in tools}


def test_every_alias_resolves_to_a_real_tool():
    """Every namespaced alias must be registered alongside the flat name."""
    names = _registered_tool_names()
    missing_namespaced = [
        ns_name
        for ns_name in server.NAMESPACE_ALIASES.values()
        if ns_name not in names
    ]
    assert not missing_namespaced, (
        f"These namespaced aliases were not registered: {missing_namespaced}"
    )
    missing_flat = [
        flat_name
        for flat_name in server.NAMESPACE_ALIASES
        if flat_name not in names
    ]
    assert not missing_flat, (
        f"These flat tool names disappeared from the MCP registry: {missing_flat}"
    )


def test_flat_and_namespaced_point_to_same_function():
    """Calling either name should hit the same Python implementation."""
    for flat_name, _ in server.NAMESPACE_ALIASES.items():
        flat_fn = getattr(server, flat_name, None)
        assert flat_fn is not None, f"Flat name `{flat_name}` not on server module"
        assert callable(flat_fn), f"Flat name `{flat_name}` is not callable"


def test_all_categories_present():
    """Every documented category must have at least one alias."""
    seen_categories: set[str] = set()
    for namespaced_name in server.NAMESPACE_ALIASES.values():
        category = namespaced_name.split(".", 1)[0]
        seen_categories.add(category)
    missing = CATEGORIES - seen_categories
    assert not missing, (
        f"Documented categories with no registered aliases: {missing}. "
        f"Either add tools to that category or remove it from the "
        f"CATEGORIES contract in this test."
    )


def test_no_duplicate_namespaced_names():
    """Aliases must be unique — no two flat names map to the same namespaced one."""
    namespaced_values = list(server.NAMESPACE_ALIASES.values())
    duplicates = {n for n in namespaced_values if namespaced_values.count(n) > 1}
    assert not duplicates, (
        f"Duplicate namespaced names in NAMESPACE_ALIASES: {duplicates}. "
        f"Each flat name needs a unique namespaced alias."
    )


def test_alias_format_dotted():
    """Every namespaced name must follow the `category.name` format."""
    for ns_name in server.NAMESPACE_ALIASES.values():
        assert "." in ns_name, (
            f"Namespaced alias `{ns_name}` does not follow `category.name` format."
        )
        category, _, _ = ns_name.partition(".")
        assert category in CATEGORIES, (
            f"Alias `{ns_name}` uses unrecognised category `{category}`. "
            f"Allowed: {sorted(CATEGORIES)}"
        )


def test_alias_count_matches_flat_count_among_registered_pairs():
    """For every flat tool covered by NAMESPACE_ALIASES that is actually
    callable on the server module, both the flat and namespaced names
    must appear in the registry. (Skipped tools — e.g. those gated on
    optional vendor catalogues — would produce a registration mismatch.)
    """
    names = _registered_tool_names()
    for flat_name, ns_name in server.NAMESPACE_ALIASES.items():
        if not callable(getattr(server, flat_name, None)):
            continue
        assert flat_name in names, f"Flat tool `{flat_name}` not registered"
        assert ns_name in names, f"Namespaced alias `{ns_name}` not registered"
