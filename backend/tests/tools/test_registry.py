"""Tests for ToolRegistry — verifies permission names match actual tool .name values."""

from __future__ import annotations

from typing import cast
from unittest.mock import MagicMock

import pytest

from backend.agents.definitions import AgentRole
from backend.tools.base import ForgeTool
from backend.tools.registry import _GRAPH_WRITE_TOOLS, ToolRegistry


def _make_tool(name: str) -> ForgeTool:
    t = MagicMock()
    t.name = name
    return cast(ForgeTool, t)


@pytest.fixture
def registry() -> ToolRegistry:
    tools = [
        _make_tool("graph_read"),
        _make_tool("file_read"),
        _make_tool("file_write"),
        _make_tool("list_files"),
        _make_tool("run_tests"),
        _make_tool("derive_requirement"),
        _make_tool("check_consistency"),
    ] + [_make_tool(name) for name in _GRAPH_WRITE_TOOLS]
    return ToolRegistry(tools=tools)


@pytest.mark.parametrize(("role", "required_tools"), [
    (AgentRole.QUALITY_AUDITOR, {"graph_add_node", "graph_read", "check_consistency"}),
    (AgentRole.DOCUMENT_SPECIALIST, {"graph_add_node", "graph_read"}),
    (AgentRole.REQUIREMENTS_ENGINEER, {"derive_requirement", "graph_add_node"}),
    (AgentRole.TEST_ENGINEER, {"list_files", "file_read", "graph_add_node"}),
])
def test_role_gets_required_tools(
    registry: ToolRegistry, role: AgentRole, required_tools: set[str]
) -> None:
    names = {t.name for t in registry.get_tools_for_role(role)}
    assert required_tools <= names


def test_unknown_role_returns_empty(registry: ToolRegistry) -> None:
    assert registry.get_tools_for_role("nonexistent-role") == []  # type: ignore[arg-type]


def test_role_permissions_contain_graph_read() -> None:
    """Every role should have graph_read in its permissions."""
    r = ToolRegistry()
    for role in AgentRole:
        perms = r._role_permissions.get(role, set())
        assert "graph_read" in perms, f"{role.value} missing graph_read"


# ── update_llm_config tests ─────────────────────────────────────────────────


class _FakeTool:
    """Minimal stand-in for an analysis tool with _llm_config."""

    def __init__(self, name: str, llm_config: object = None):
        self.name = name
        self._llm_config = llm_config


def test_update_llm_config_pushes_to_tools_with_attribute() -> None:
    old_cfg = {"model": "old"}
    new_cfg = {"model": "new"}
    tools = [
        _FakeTool("derive_requirement", old_cfg),
        _FakeTool("check_consistency", old_cfg),
    ]
    registry = ToolRegistry(tools=cast(list[ForgeTool], tools))
    registry.update_llm_config(new_cfg)

    for tool in tools:
        assert tool._llm_config is new_cfg


def test_update_llm_config_skips_tools_without_attribute() -> None:
    """Tools that don't have _llm_config should not be affected."""

    class _PlainTool:
        def __init__(self) -> None:
            self.name = "graph_read"

    plain = _PlainTool()
    analysis = _FakeTool("derive_requirement", {"model": "old"})
    registry = ToolRegistry(tools=cast(list[ForgeTool], [plain, analysis]))
    registry.update_llm_config({"model": "new"})

    assert analysis._llm_config == {"model": "new"}
    assert not hasattr(plain, "_llm_config")


def test_add_tools_registers_new_instances() -> None:
    registry = ToolRegistry(tools=[_make_tool("graph_read")])
    registry.add_tools([_make_tool("file_read")])
    tools = registry.get_tools_for_role(AgentRole.QUALITY_AUDITOR)
    assert {t.name for t in tools} == {"graph_read", "file_read"}


def test_get_tools_for_gap_returns_whitelisted_instances() -> None:
    from backend.analysis.gaps import GapType

    registry = ToolRegistry(tools=[
        _make_tool("graph_read"),
        _make_tool("graph_update_node"),
        _make_tool("file_write"),  # not whitelisted for EMPTY_CONTENT
    ])
    tools = registry.get_tools_for_gap(GapType.EMPTY_CONTENT)
    assert {t.name for t in tools} == {"graph_read", "graph_update_node"}


def test_get_tools_for_gap_unlisted_gap_returns_empty() -> None:
    from backend.analysis.gaps import GapType

    registry = ToolRegistry(tools=[_make_tool("graph_read")])
    assert registry.get_tools_for_gap(GapType.UNSYNCED_DESIGN) == []
