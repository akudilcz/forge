"""Tests for graph_write tool — remove_edge and remove_traces message accuracy."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.graph.models import GraphNode, NodeType
from backend.tools.graph_write import GraphWriteTool


@pytest.fixture
def mock_graph() -> MagicMock:
    graph = MagicMock()
    graph.remove_edge = AsyncMock()
    graph.update_node = AsyncMock()
    graph.node_sync = MagicMock(return_value=None)
    return graph


@pytest.fixture
def tool(mock_graph: MagicMock) -> GraphWriteTool:
    t = GraphWriteTool(graph=mock_graph)
    return t


# ── remove_edge ────────────────────────────────────────────────────────────────


def test_remove_edge_success(tool: GraphWriteTool, mock_graph: MagicMock) -> None:
    """remove_edge dispatches to graph.remove_edge and returns OK."""
    result = tool._execute(operation="remove_edge", edge_id="edge-123", reason="cleanup")
    assert "OK: removed edge edge-123" in result
    mock_graph.remove_edge.assert_called_once_with("edge-123", "cleanup")


def test_remove_edge_missing_edge_id(tool: GraphWriteTool, mock_graph: MagicMock) -> None:
    """remove_edge without edge_id returns an error."""
    result = tool._execute(operation="remove_edge")
    assert "ERROR" in result
    assert "edge_id" in result
    mock_graph.remove_edge.assert_not_called()


# ── remove_traces partial match message ────────────────────────────────────────


def test_remove_traces_partial_match_message(tool: GraphWriteTool, mock_graph: MagicMock) -> None:
    """When requested IDs don't exist in trace_to, message reports them."""
    node = GraphNode(
        node_id="HLR-001", node_type=NodeType.HLR.value,
        title="Test", content="content", trace_to=["REF-001"],
    )
    mock_graph.node_sync.return_value = node
    result = tool._execute(
        operation="remove_traces",
        node_id="HLR-001",
        trace_to='["REF-999"]',
    )
    assert "no matching traces to remove" in result
    assert "REF-999" in result
    assert "not present" in result


def test_remove_traces_success(tool: GraphWriteTool, mock_graph: MagicMock) -> None:
    """remove_traces removes matching IDs and returns them."""
    node = GraphNode(
        node_id="HLR-001", node_type=NodeType.HLR.value,
        title="Test", content="content", trace_to=["REF-001", "REF-002"],
    )
    mock_graph.node_sync.return_value = node
    result = tool._execute(
        operation="remove_traces",
        node_id="HLR-001",
        trace_to='["REF-001"]',
    )
    assert "OK: removed" in result
    assert "REF-001" in result
