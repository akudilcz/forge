"""Tests for GraphNode identity semantics (backend/graph/models.py)."""

from __future__ import annotations

from backend.graph.models import GraphNode, NodeType


def _node(node_id: str, content: str = "c") -> GraphNode:
    return GraphNode(
        node_id=node_id, node_type=NodeType.PARA.value, title="t", content=content,
    )


def test_nodes_with_same_id_are_equal_and_hash_alike() -> None:
    a = _node("N-1", content="one")
    b = _node("N-1", content="two")
    assert a == b
    assert hash(a) == hash(b)
    assert len({a, b}) == 1


def test_nodes_with_different_ids_are_unequal() -> None:
    assert _node("N-1") != _node("N-2")


def test_comparison_with_non_node_returns_notimplemented() -> None:
    node = _node("N-1")
    assert node.__eq__("N-1") is NotImplemented
    assert node != "N-1"
