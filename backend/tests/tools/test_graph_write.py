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

# ── reparent validation paths ─────────────────────────────────────────────────


class _ReparentStub:
    """Duck-typed graph capturing reparent calls, with controllable lookups."""

    def __init__(self, nodes: dict[str, GraphNode], children: dict[str, list[GraphNode]]):
        self._nodes = nodes
        self._children = children
        self.reparented: list[tuple[str, str | None]] = []

    async def node(self, node_id: str) -> GraphNode | None:
        return self._nodes.get(node_id)

    def children_sync(self, parent_id: str) -> list[GraphNode]:
        return self._children.get(parent_id, [])

    async def reparent_node(
        self, node_id: str, new_parent_id: str | None, changed_by: str, reason: str,
    ) -> None:
        self.reparented.append((node_id, new_parent_id))


def _n(node_id: str, node_type: str, parent_id: str | None = None) -> GraphNode:
    return GraphNode(
        node_id=node_id, node_type=node_type, title=node_id,
        content="c", parent_id=parent_id,
    )


def test_reparent_missing_child_skips_validation_and_reparents() -> None:
    graph = _ReparentStub(nodes={}, children={})
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(operation="reparent_node", node_id="ghost", parent_id="")
    assert result == "OK: moved ghost to parent None"
    assert graph.reparented == [("ghost", None)]


def test_reparent_detach_checks_orphan_guard_only() -> None:
    # parent_id empty → no type check; orphan guard passes (sibling remains).
    child = _n("HLR-1", NodeType.HLR.value, parent_id="PARA-1")
    sibling = _n("HLR-2", NodeType.HLR.value, parent_id="PARA-1")
    graph = _ReparentStub(
        nodes={"HLR-1": child},
        children={"PARA-1": [child, sibling]},
    )
    result = GraphWriteTool(graph=graph)._execute(
        operation="reparent_node", node_id="HLR-1", parent_id="",
    )
    assert result.startswith("OK: moved HLR-1")


def test_reparent_unconstrained_child_type_skips_type_check() -> None:
    # DOCUMENT has no VALID_PARENT_TYPES entry — any parent type accepted.
    child = _n("DOC-1", NodeType.DOCUMENT.value)
    parent = _n("PARA-9", NodeType.PARA.value)
    graph = _ReparentStub(nodes={"DOC-1": child, "PARA-9": parent}, children={})
    result = GraphWriteTool(graph=graph)._execute(
        operation="reparent_node", node_id="DOC-1", parent_id="PARA-9",
    )
    assert result.startswith("OK: moved DOC-1")


def test_reparent_orphan_guard_attribute_error_tolerated() -> None:
    class _NoChildrenGraph:
        async def node(self, node_id: str) -> GraphNode | None:
            return {
                "HLR-1": _n("HLR-1", NodeType.HLR.value, parent_id="PARA-1"),
                "PARA-2": _n("PARA-2", NodeType.PARA.value),
            }.get(node_id)

        async def reparent_node(self, *args: object, **kwargs: object) -> None:
            return None

    result = GraphWriteTool(graph=_NoChildrenGraph())._execute(
        operation="reparent_node", node_id="HLR-1", parent_id="PARA-2",
    )
    assert result == "OK: moved HLR-1 to parent PARA-2"


# ── dispatch / error handling ─────────────────────────────────────────────────


def test_execute_without_graph_errors() -> None:
    assert GraphWriteTool()._execute(operation="add_node") == "ERROR: Graph not available"


def test_execute_wraps_graph_exception(mock_graph: MagicMock) -> None:
    mock_graph.update_node = AsyncMock(side_effect=KeyError("Node not found: X"))
    result = GraphWriteTool(graph=mock_graph)._execute(
        operation="update_node", node_id="X", content="c",
    )
    assert result.startswith("ERROR:")
    assert "Node not found" in result


def test_unknown_operation_lists_valid_ops(tool: GraphWriteTool) -> None:
    result = tool._execute(operation="frobnicate")
    assert "Unknown operation 'frobnicate'" in result
    assert "add_node" in result


# ── add_node constraints ──────────────────────────────────────────────────────


def test_add_node_blocked_by_phase_constraint(mock_graph: MagicMock) -> None:
    from backend.analysis.gaps import GapType
    from backend.pipeline.phase_constraints import (
        reset_phase_constraints,
        set_phase_constraints,
    )

    token = set_phase_constraints(GapType.UNCOVERED_PARA)  # only HLR allowed
    try:
        result = GraphWriteTool(graph=mock_graph)._execute(
            operation="add_node", node_type="MODULE", node_id="MOD-1",
        )
    finally:
        reset_phase_constraints(token)
    assert "Phase constraint" in result
    assert "MODULE" in result


def test_add_node_design_capped_by_class_plan() -> None:
    module = GraphNode(
        node_id="MOD-1", node_type="MODULE", title="m",
        content="## Class plan\n- `Parser`\n",
    )
    design = _n("DESIGN-1", "DESIGN", parent_id="MOD-1")
    graph = MagicMock()
    graph.node_sync = MagicMock(return_value=module)
    graph.children_sync = MagicMock(return_value=[design])
    result = GraphWriteTool(graph=graph)._execute(
        operation="add_node", node_type="DESIGN", parent_id="MOD-1", node_id="DESIGN-2",
    )
    assert "Refusing to create another DESIGN" in result


# ── update_node option handling ───────────────────────────────────────────────


def test_update_node_para_type_merged_into_properties(mock_graph: MagicMock) -> None:
    result = GraphWriteTool(graph=mock_graph)._execute(
        operation="update_node", node_id="PARA-1", para_type="heading",
    )
    assert result == "OK: updated PARA-1"
    props = mock_graph.update_node.await_args.args[2]
    assert props == {"sub_type": "heading"}


def test_update_node_invalid_properties_json_ignored(mock_graph: MagicMock) -> None:
    result = GraphWriteTool(graph=mock_graph)._execute(
        operation="update_node", node_id="PARA-1", properties="{not json",
    )
    assert result == "OK: updated PARA-1"
    assert mock_graph.update_node.await_args.args[2] is None


# ── trace list handling ───────────────────────────────────────────────────────


def test_update_trace_missing_node(mock_graph: MagicMock) -> None:
    mock_graph.node_sync.return_value = None
    result = GraphWriteTool(graph=mock_graph)._execute(
        operation="update_trace", node_id="ghost", trace_to='["A"]',
    )
    assert result == "ERROR: Node not found: ghost"


def test_add_traces_missing_node(mock_graph: MagicMock) -> None:
    mock_graph.node_sync.return_value = None
    result = GraphWriteTool(graph=mock_graph)._execute(
        operation="add_traces", node_id="ghost", trace_to='["A"]',
    )
    assert result == "ERROR: Node not found: ghost"


def test_remove_traces_empty_request(mock_graph: MagicMock) -> None:
    node = GraphNode(
        node_id="HLR-1", node_type=NodeType.HLR.value,
        title="t", content="c", trace_to=["REF-1"],
    )
    mock_graph.node_sync.return_value = node
    result = GraphWriteTool(graph=mock_graph)._execute(
        operation="remove_traces", node_id="HLR-1", trace_to="[]",
    )
    assert result == "OK: no matching traces to remove on HLR-1"


def test_add_traces_rejects_json_object(mock_graph: MagicMock) -> None:
    node = GraphNode(
        node_id="HLR-1", node_type=NodeType.HLR.value, title="t", content="c",
    )
    mock_graph.node_sync.return_value = node
    result = GraphWriteTool(graph=mock_graph)._execute(
        operation="add_traces", node_id="HLR-1", trace_to='{"a": 1}',
    )
    assert result == "ERROR: trace_to must be a JSON array of node ID strings"


# ── module-level helpers ──────────────────────────────────────────────────────


def test_coerce_to_list_variants() -> None:
    from backend.tools.graph_write_parsing import _coerce_to_list, _TraceToCoerceError

    assert _coerce_to_list(None) == []
    assert _coerce_to_list("") == []
    assert _coerce_to_list("[]") == []
    assert _coerce_to_list(("A", "B")) == ["A", "B"]
    assert _coerce_to_list('["A"]') == ["A"]
    with pytest.raises(_TraceToCoerceError):
        _coerce_to_list("not json")
    with pytest.raises(_TraceToCoerceError):
        _coerce_to_list('{"a": 1}')
    with pytest.raises(_TraceToCoerceError):
        _coerce_to_list({"a": 1})


def test_parse_trace_to_fallbacks() -> None:
    from backend.tools.graph_write_parsing import _parse_trace_to

    # kwargs win when parseable
    assert _parse_trace_to({"trace_to": '["A"]'}, {}) == ["A"]
    # bad kwargs value falls back to props string (wrapped in a list)
    assert _parse_trace_to({"trace_to": "junk"}, {"trace_to": "HLR-1"}) == ["HLR-1"]
    # props list is coerced
    assert _parse_trace_to({}, {"trace_to": ["A", "B"]}) == ["A", "B"]
    # unusable props fallback yields empty list
    assert _parse_trace_to({}, {"trace_to": {"bad": True}}) == []
