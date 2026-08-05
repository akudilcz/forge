"""Behavioural tests for GraphReadTool traversal ops (backend/tools/graph_read.py)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

from backend.graph.models import GraphNode, NodeType
from backend.tools.graph_read import GraphReadTool


def _node(node_id: str) -> GraphNode:
    return GraphNode(
        node_id=node_id, node_type=NodeType.PARA.value, title=node_id, content="c",
    )


def test_children_operation_serialises_nodes() -> None:
    graph = MagicMock()
    graph.children_sync = MagicMock(return_value=[_node("kid-1"), _node("kid-2")])
    tool = GraphReadTool(graph=graph)
    out = json.loads(tool._execute(operation="children", node_id="parent"))
    assert [n["node_id"] for n in out] == ["kid-1", "kid-2"]
    graph.children_sync.assert_called_once_with("parent")


def test_ancestors_operation_serialises_nodes() -> None:
    graph = MagicMock()
    graph.ancestors = AsyncMock(return_value=[_node("anc-1")])
    tool = GraphReadTool(graph=graph)
    out = json.loads(tool._execute(operation="ancestors", node_id="n"))
    assert [n["node_id"] for n in out] == ["anc-1"]


def test_descendants_operation_serialises_nodes() -> None:
    graph = MagicMock()
    graph.descendants = AsyncMock(return_value=[_node("desc-1")])
    tool = GraphReadTool(graph=graph)
    out = json.loads(tool._execute(operation="descendants", node_id="n"))
    assert [n["node_id"] for n in out] == ["desc-1"]


def test_query_failure_returns_error_string() -> None:
    graph = MagicMock()
    graph.node = AsyncMock(side_effect=RuntimeError("db exploded"))
    tool = GraphReadTool(graph=graph)
    assert tool._execute(operation="node", node_id="n") == "ERROR: db exploded"


def test_nodes_combined_prefix_and_type_filters() -> None:
    llr = GraphNode(node_id="LLR-1", node_type="LLR", title="a", content="c")
    hlr = GraphNode(node_id="HLR-1", node_type="HLR", title="b", content="c")
    graph = MagicMock()
    graph.all_nodes = MagicMock(return_value=[llr, hlr])
    tool = GraphReadTool(graph=graph)
    out = json.loads(tool._execute(operation="nodes", type_prefix="LLR-", node_type="llr"))
    assert [n["node_id"] for n in out] == ["LLR-1"]
