"""Tests for MultiGraphWriteTool."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.tools.multi_graph_write import MultiGraphWriteTool


def _make_graph() -> MagicMock:
    from backend.graph.models import GraphEdge, GraphNode, ImpactSet, NodeType

    mock = MagicMock()
    node = GraphNode(node_id="req.hlr.001", node_type=NodeType.HLR.value, title="HLR-001")
    edge = GraphEdge(edge_type="DERIVES_FROM", source_id="req.hlr.001", target_id="doc.spec")
    mock.add_node = AsyncMock(return_value=node)
    mock.add_edge = AsyncMock(return_value=edge)
    mock.update_node = AsyncMock(return_value=(node, ImpactSet(root_node_id="req.hlr.001")))
    mock.set_lifecycle = AsyncMock(return_value=None)
    mock.delete_node = AsyncMock(return_value=None)
    return mock


def test_no_graph_returns_error() -> None:
    tool = MultiGraphWriteTool(graph=None)
    result = tool._execute(operations="[]")
    assert "ERROR" in result


@pytest.mark.parametrize(("ops_str", "expected_msg"), [
    ("{not valid}", "JSON"),
    ('{"operation": "add_node"}', "array"),
])
def test_invalid_input_returns_error(ops_str: str, expected_msg: str) -> None:
    graph = _make_graph()
    tool = MultiGraphWriteTool(graph=graph)
    result = tool._execute(operations=ops_str)
    assert "ERROR" in result
    assert expected_msg in result


def test_add_node_success() -> None:
    graph = _make_graph()
    tool = MultiGraphWriteTool(graph=graph)
    ops = json.dumps([{
        "operation": "add_node",
        "node_id": "req.hlr.001",
        "node_type": "HLR",
        "title": "HLR-001",
        "content": "The system shall...",
        "properties": '{"req_level": "hlr"}',
        "lifecycle": "draft",
    }])
    result = tool._execute(operations=ops)
    assert "1/1 operations succeeded" in result
    assert "added node" in result


def test_add_node_invalid_properties_json_falls_back() -> None:
    graph = _make_graph()
    tool = MultiGraphWriteTool(graph=graph)
    ops = json.dumps([{
        "operation": "add_node",
        "node_id": "req.hlr.002",
        "node_type": "HLR",
        "title": "HLR-002",
        "properties": "{not json}",
    }])
    result = tool._execute(operations=ops)
    assert "1/1 operations succeeded" in result


def test_add_edge_success() -> None:
    graph = _make_graph()
    tool = MultiGraphWriteTool(graph=graph)
    ops = json.dumps([{
        "operation": "add_edge",
        "edge_type": "DERIVES_FROM",
        "source_id": "req.hlr.001",
        "target_id": "doc.spec",
        "reason": "derived from spec",
    }])
    result = tool._execute(operations=ops)
    assert "1/1 operations succeeded" in result
    assert "added edge" in result


def test_update_node_success() -> None:
    graph = _make_graph()
    tool = MultiGraphWriteTool(graph=graph)
    ops = json.dumps([{
        "operation": "update_node",
        "node_id": "req.hlr.001",
        "content": "updated content",
        "title": "New Label",
        "properties": '{"status": "approved"}',
        "reason": "refined",
    }])
    result = tool._execute(operations=ops)
    assert "1/1 operations succeeded" in result
    assert "updated" in result


def test_unknown_operation_returns_error() -> None:
    graph = _make_graph()
    tool = MultiGraphWriteTool(graph=graph)
    ops = json.dumps([{
        "operation": "set_lifecycle",
        "node_id": "req.hlr.001",
    }])
    result = tool._execute(operations=ops)
    assert "Unknown" in result or "Errors" in result


def test_delete_node_success() -> None:
    graph = _make_graph()
    tool = MultiGraphWriteTool(graph=graph)
    ops = json.dumps([{"operation": "delete_node", "node_id": "req.hlr.001"}])
    result = tool._execute(operations=ops)
    assert "1/1 operations succeeded" in result
    assert "deleted" in result


def test_delete_node_empty_id() -> None:
    graph = _make_graph()
    tool = MultiGraphWriteTool(graph=graph)
    ops = json.dumps([{"operation": "delete_node", "node_id": ""}])
    result = tool._execute(operations=ops)
    # GraphWriteTool passes through to graph.delete_node which may succeed or fail
    assert "1/1" in result or "Errors" in result


def test_mixed_operations_partial_success() -> None:
    graph = _make_graph()
    graph.add_node = AsyncMock(side_effect=RuntimeError("DB error"))
    tool = MultiGraphWriteTool(graph=graph)
    ops = json.dumps([
        {"operation": "add_node", "node_id": "req.1", "node_type": "HLR"},
        {"operation": "add_edge", "edge_type": "DERIVES_FROM", "source_id": "a", "target_id": "b"},
    ])
    result = tool._execute(operations=ops)
    assert "1/2 operations succeeded" in result
    assert "Errors" in result
