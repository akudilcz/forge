"""Tests for graph_regex_replace tool."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.tools.graph_regex_replace import GraphRegexReplaceTool


def _node(nid: str, ntype: str, title: str, content: str) -> SimpleNamespace:
    return SimpleNamespace(
        node_id=nid, node_type=ntype, title=title, content=content,
        parent_id=None, trace_to=[], properties={},
    )


@pytest.fixture
def mock_graph() -> MagicMock:
    g = MagicMock()
    g.update_node = AsyncMock()
    g.all_nodes.return_value = [
        _node("HLR-001", "HLR", "Auth Login", "The system shall authenticate users via password."),
        _node("HLR-002", "HLR", "Auth Logout", "The system shall log out users on timeout."),
        _node("LLR-001", "LLR", "Hash Password", "The system shall hash passwords with bcrypt."),
    ]
    return g


def test_dry_run_returns_preview(mock_graph: MagicMock) -> None:
    tool = GraphRegexReplaceTool(mock_graph)
    result = tool._execute(pattern="shall", replacement="must", field="content", dry_run="true")
    assert "DRY RUN" in result
    assert "3 node(s) would change" in result
    data = json.loads(result.split("\n", 1)[1])
    assert len(data) == 3
    assert "must" in data[0]["new_content_preview"]
    mock_graph.update_node.assert_not_called()


def test_apply_calls_update(mock_graph: MagicMock) -> None:
    tool = GraphRegexReplaceTool(mock_graph)
    result = tool._execute(pattern="shall", replacement="must", field="content", dry_run="false")
    assert "3 node(s) updated" in result
    assert mock_graph.update_node.call_count == 3


def test_invalid_regex(mock_graph: MagicMock) -> None:
    tool = GraphRegexReplaceTool(mock_graph)
    result = tool._execute(pattern="[invalid", replacement="x")
    assert "ERROR: Invalid regex" in result


def test_no_matches(mock_graph: MagicMock) -> None:
    tool = GraphRegexReplaceTool(mock_graph)
    result = tool._execute(pattern="zzz_no_match", replacement="x")
    assert "No matches" in result


def test_title_field_only(mock_graph: MagicMock) -> None:
    tool = GraphRegexReplaceTool(mock_graph)
    result = tool._execute(pattern="Auth", replacement="Authentication", field="title", dry_run="true")
    assert "DRY RUN" in result
    assert "2 node(s)" in result
    data = json.loads(result.split("\n", 1)[1])
    ids = {d["node_id"] for d in data}
    assert ids == {"HLR-001", "HLR-002"}


def test_node_type_filter(mock_graph: MagicMock) -> None:
    tool = GraphRegexReplaceTool(mock_graph)
    result = tool._execute(pattern="shall", replacement="must", node_type="LLR", dry_run="true")
    assert "1 node(s)" in result
    data = json.loads(result.split("\n", 1)[1])
    assert data[0]["node_id"] == "LLR-001"


def test_graph_unavailable() -> None:
    tool = GraphRegexReplaceTool(None)
    result = tool._execute(pattern="x", replacement="y")
    assert "ERROR: Graph not available" in result


def test_both_fields(mock_graph: MagicMock) -> None:
    tool = GraphRegexReplaceTool(mock_graph)
    result = tool._execute(pattern="Auth", replacement="X", field="both", dry_run="true")
    data = json.loads(result.split("\n", 1)[1])
    # HLR-001 and HLR-002 have "Auth" in title; content has "authenticate"/"auth" variants
    titled = [d for d in data if "new_title" in d]
    assert len(titled) == 2
