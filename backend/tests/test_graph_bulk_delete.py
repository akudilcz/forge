"""Tests for graph_bulk_delete tool."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.tools.graph_bulk_delete import GraphBulkDeleteTool


def _node(nid: str, ntype: str, title: str = "", content: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        node_id=nid, node_type=ntype, title=title, content=content,
        parent_id=None, trace_to=[], properties={},
    )


@pytest.fixture
def mock_graph() -> MagicMock:
    g = MagicMock()
    g.delete_node = AsyncMock()
    g.all_nodes.return_value = [
        _node("PARA-001", "PARA", "Intro", "Introduction paragraph about the system."),
        _node("PARA-002", "PARA", "Scope", "Scope of the software project."),
        _node("HLR-001", "HLR", "Auth", "The system shall authenticate users."),
        _node("LLR-001", "LLR", "Hash", "The system shall hash passwords."),
    ]
    return g


def test_dry_run_lists_candidates(mock_graph: MagicMock) -> None:
    tool = GraphBulkDeleteTool(mock_graph)
    result = tool._execute(node_type="PARA", dry_run="true")
    assert "DRY RUN" in result
    assert "2 node(s) would be deleted" in result
    data = json.loads(result.split("\n", 1)[1])
    ids = {d["node_id"] for d in data}
    assert ids == {"PARA-001", "PARA-002"}
    mock_graph.delete_node.assert_not_called()


def test_delete_by_ids(mock_graph: MagicMock) -> None:
    tool = GraphBulkDeleteTool(mock_graph)
    ids = json.dumps(["PARA-001", "HLR-001"])
    result = tool._execute(node_ids=ids, dry_run="false")
    assert "2 node(s) deleted" in result
    assert mock_graph.delete_node.call_count == 2


def test_delete_by_type(mock_graph: MagicMock) -> None:
    tool = GraphBulkDeleteTool(mock_graph)
    result = tool._execute(node_type="PARA", dry_run="false")
    assert "2 node(s) deleted" in result


def test_delete_by_pattern(mock_graph: MagicMock) -> None:
    tool = GraphBulkDeleteTool(mock_graph)
    result = tool._execute(pattern="shall.*password", dry_run="true")
    assert "1 node(s)" in result
    data = json.loads(result.split("\n", 1)[1])
    assert data[0]["node_id"] == "LLR-001"


def test_no_filter_returns_error(mock_graph: MagicMock) -> None:
    tool = GraphBulkDeleteTool(mock_graph)
    result = tool._execute()
    assert "ERROR: At least one filter" in result


def test_combined_filters(mock_graph: MagicMock) -> None:
    tool = GraphBulkDeleteTool(mock_graph)
    result = tool._execute(node_type="PARA", pattern="scope", dry_run="true")
    assert "1 node(s)" in result
    data = json.loads(result.split("\n", 1)[1])
    assert data[0]["node_id"] == "PARA-002"


def test_no_matches(mock_graph: MagicMock) -> None:
    tool = GraphBulkDeleteTool(mock_graph)
    result = tool._execute(node_type="SUITE")
    assert "No nodes match" in result


def test_invalid_regex(mock_graph: MagicMock) -> None:
    tool = GraphBulkDeleteTool(mock_graph)
    result = tool._execute(pattern="[invalid")
    assert "ERROR: Invalid regex" in result


def test_graph_unavailable() -> None:
    tool = GraphBulkDeleteTool(None)
    result = tool._execute(node_type="HLR")
    assert "ERROR: Graph not available" in result
