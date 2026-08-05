"""Behavioural tests for the single-operation graph tools (backend/tools/graph_ops.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from backend.graph.models import GraphEdge, GraphNode, NodeType
from backend.tools.graph_ops import (
    GraphAddEdgeTool,
    GraphAddTracesTool,
    GraphRemoveEdgeTool,
    GraphRemoveTracesTool,
    GraphUpdateTraceTool,
    _GraphMutationTool,
)


def _node(node_id: str, trace_to: list[str] | None = None) -> GraphNode:
    return GraphNode(
        node_id=node_id, node_type=NodeType.LLR.value, title=node_id,
        content="c", trace_to=trace_to or [],
    )


def _graph_with_node(node: GraphNode) -> MagicMock:
    graph = MagicMock()
    graph.node_sync = MagicMock(return_value=node)
    graph.update_node = AsyncMock(return_value=(node, None))
    return graph


def test_base_tool_without_graph_errors() -> None:
    assert _GraphMutationTool()._execute() == "ERROR: Graph not available"


def test_base_tool_run_op_not_implemented_surfaces_error() -> None:
    tool = _GraphMutationTool(graph=MagicMock())
    assert tool._execute().startswith("ERROR:")


def test_update_trace_tool_replaces_list() -> None:
    graph = _graph_with_node(_node("LLR-1", trace_to=["OLD"]))
    out = GraphUpdateTraceTool(graph=graph)._execute(
        node_id="LLR-1", trace_to='["HLR-1"]', reason="r",
    )
    assert out == "OK: updated trace_to on LLR-1"
    assert graph.update_node.await_args.kwargs["trace_to"] == ["HLR-1"]


def test_add_traces_tool_appends() -> None:
    graph = _graph_with_node(_node("LLR-1", trace_to=["HLR-1"]))
    out = GraphAddTracesTool(graph=graph)._execute(
        node_id="LLR-1", trace_to='["HLR-2"]', reason="r",
    )
    assert "added ['HLR-2']" in out
    assert graph.update_node.await_args.kwargs["trace_to"] == ["HLR-1", "HLR-2"]


def test_remove_traces_tool_removes() -> None:
    graph = _graph_with_node(_node("LLR-1", trace_to=["HLR-1", "HLR-2"]))
    out = GraphRemoveTracesTool(graph=graph)._execute(
        node_id="LLR-1", trace_to='["HLR-1"]', reason="r",
    )
    assert "removed ['HLR-1']" in out
    assert graph.update_node.await_args.kwargs["trace_to"] == ["HLR-2"]


def test_add_edge_tool_creates_edge() -> None:
    graph = MagicMock()

    async def _echo_edge(edge: GraphEdge) -> GraphEdge:
        return edge

    graph.add_edge = AsyncMock(side_effect=_echo_edge)
    out = GraphAddEdgeTool(graph=graph)._execute(
        edge_type="IMPLEMENTS", source_id="CODE-1", target_id="LLR-1", reason="r",
    )
    assert out.startswith("OK: added edge ")
    sent = graph.add_edge.await_args.args[0]
    assert sent.source_id == "CODE-1"
    assert sent.target_id == "LLR-1"


def test_remove_edge_tool_removes_edge() -> None:
    graph = MagicMock()
    graph.remove_edge = AsyncMock()
    out = GraphRemoveEdgeTool(graph=graph)._execute(edge_id="E-1", reason="r")
    assert out == "OK: removed edge E-1"
    graph.remove_edge.assert_awaited_once_with("E-1", "r")


def test_remove_edge_tool_requires_edge_id() -> None:
    out = GraphRemoveEdgeTool(graph=MagicMock())._execute(edge_id="")
    assert out == "ERROR: edge_id is required for remove_edge"
