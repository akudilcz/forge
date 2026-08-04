"""Every gap type with an agent must also have tools.

``ToolRegistry.get_tools_for_gap`` returns ``frozenset()`` for a gap type absent
from ``_GAP_TOOL_WHITELIST``. An agent built from that returns a model with **no
tools bound** — it can read the gap and has no means to act on it, so the gap
never closes, the structural loop records no progress, and the run burns retries
on work that was impossible from the start.

Six gap types were in this state — ``EMPTY_TRACE``, ``CIRCULAR_TRACE``,
``STALE_ARCHITECTURE``, ``STALE_SUITE``, ``MISSING_CODE`` and ``STALE_CODE``.
Nothing failed loudly; the agent simply produced nothing.

The two tables live in different modules (``agents/definitions.py`` and
``tools/registry.py``), so adding an agent mapping without a tool set is a
one-line change that looks complete. These tests are the cross-check.
"""

from __future__ import annotations

import pytest

from backend.agents.definitions import GAP_AGENT_MAPPING
from backend.analysis.gaps import GapType
from backend.tools.registry import ToolRegistry

_AGENT_GAPS: list[GapType] = sorted(GAP_AGENT_MAPPING, key=lambda g: g.value)

WHITELIST = ToolRegistry._GAP_TOOL_WHITELIST


def test_every_agent_mapped_gap_type_has_a_tool_set() -> None:
    missing = sorted(g.value for g in set(GAP_AGENT_MAPPING) - set(WHITELIST))
    assert not missing, (
        f"{len(missing)} gap type(s) are dispatched to an agent with no tools, so "
        f"they can never be resolved: {missing}"
    )


def test_no_whitelist_entry_is_empty() -> None:
    """An empty frozenset is the same failure, spelled differently."""
    empty = sorted(g.value for g, names in WHITELIST.items() if not names)
    assert not empty, f"gap types whitelisted with zero tools: {empty}"


@pytest.mark.parametrize("gap_type", _AGENT_GAPS, ids=lambda g: g.value)
def test_each_agent_mapped_gap_can_read_the_graph(gap_type: GapType) -> None:
    """Every remedy starts by inspecting the node the gap is about."""
    tools = WHITELIST.get(gap_type, frozenset())
    assert "graph_read" in tools, (
        f"{gap_type.value} has no graph_read, so its agent cannot inspect the "
        f"node it was asked to fix (tools: {sorted(tools)})"
    )


@pytest.mark.parametrize("gap_type", _AGENT_GAPS, ids=lambda g: g.value)
def test_each_agent_mapped_gap_can_mutate_something(gap_type: GapType) -> None:
    """Read-only tools cannot close a gap.

    Without at least one mutating tool the agent can look at the problem and is
    structurally incapable of fixing it.
    """
    mutating = {
        "graph_add_node",
        "graph_update_node",
        "graph_delete_node",
        "graph_reparent_node",
        "graph_add_traces",
        "graph_remove_traces",
        "graph_update_trace",
        "graph_regex_replace",
        "graph_bulk_delete",
        "multi_graph_write",
        "file_write",
        "file_patch",
        "derive_requirement",
    }
    tools = WHITELIST.get(gap_type, frozenset())
    assert tools & mutating, (
        f"{gap_type.value} has only read-only tools {sorted(tools)} — its agent "
        "cannot close the gap it is dispatched for"
    )


def test_whitelisted_tool_names_are_real() -> None:
    """A typo'd name is silently dropped by get_tools_for_gap's filter.

    ``[instance_map[n] for n in allowed if n in instance_map]`` skips unknown
    names, so a misspelling removes a tool with no error anywhere.
    """
    import backend.tools.registry as registry_module

    source = (registry_module.__file__ or "")
    assert source
    text = open(source, encoding="utf-8").read()

    known: set[str] = set()
    for names in WHITELIST.values():
        known |= set(names)

    # Every whitelisted name must appear somewhere else in the registry too —
    # in a role permission set or the tool construction list.
    for name in sorted(known):
        assert text.count(f'"{name}"') > 1, (
            f"tool name {name!r} appears only inside _GAP_TOOL_WHITELIST — it is "
            "probably a typo, and get_tools_for_gap would silently drop it"
        )


def test_workspace_gaps_get_file_tools() -> None:
    """MISSING_CODE and STALE_CODE are fixed on disk, not only in the graph."""
    for gap_type in (GapType.MISSING_CODE, GapType.STALE_CODE):
        tools = WHITELIST.get(gap_type, frozenset())
        assert "file_write" in tools, (
            f"{gap_type.value} concerns a workspace file but its agent cannot "
            f"write one (tools: {sorted(tools)})"
        )
