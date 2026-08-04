"""Tests for graph engine: delete_node, update_node raises, remove_edge, reparent_node."""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from backend.graph.engine import ProjectGraph
from backend.graph.models import EdgeType, GraphEdge, GraphNode, NodeType


@pytest.fixture
async def graph() -> AsyncIterator[ProjectGraph]:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    g = ProjectGraph(db_path)
    await g.initialise()
    yield g
    os.unlink(db_path)


async def _add_node(g: ProjectGraph, node_id: str, node_type: str = NodeType.DOCUMENT.value) -> GraphNode:
    node = GraphNode(node_id=node_id, node_type=node_type, title=node_id, content=f"content of {node_id}")
    return await g.add_node(node)


# ── delete_node ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_node_removes_from_db(graph: ProjectGraph) -> None:
    await _add_node(graph, "doc.x")
    assert await graph.node("doc.x") is not None

    await graph.delete_node("doc.x")
    assert await graph.node("doc.x") is None


@pytest.mark.asyncio
async def test_delete_node_cascades_edges(graph: ProjectGraph) -> None:
    await _add_node(graph, "doc.x")
    await _add_node(graph, "doc.y")
    edge = GraphEdge(edge_id="e1", edge_type=EdgeType.DERIVES_FROM.value, source_id="doc.x", target_id="doc.y")
    await graph.add_edge(edge)
    assert graph._g.has_edge("doc.x", "doc.y")

    await graph.delete_node("doc.x")
    assert not graph._g.has_node("doc.x")


@pytest.mark.asyncio
async def test_delete_nonexistent_node_noop(graph: ProjectGraph) -> None:
    await graph.delete_node("does.not.exist")  # Should not raise


# ── update_node ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_node_raises_for_missing_node(graph: ProjectGraph) -> None:
    with pytest.raises(KeyError):
        await graph.update_node("nonexistent.node", "new content", None, "tester", "test update")


@pytest.mark.asyncio
async def test_update_node_changes_content_and_hash(graph: ProjectGraph) -> None:
    await _add_node(graph, "doc.a")
    original = await graph.node("doc.a")
    assert original is not None
    updated, _impact = await graph.update_node(
        "doc.a", "updated content", None, "tester", "test update"
    )
    assert updated.content == "updated content"
    assert updated.version == 2
    assert updated.content_hash != original.content_hash


@pytest.mark.asyncio
async def test_update_node_merges_properties(graph: ProjectGraph) -> None:
    node = GraphNode(
        node_id="doc.c", node_type=NodeType.DOCUMENT.value, title="Doc C",
        content="content", properties={"key1": "value1"},
    )
    await graph.add_node(node)
    updated, _ = await graph.update_node("doc.c", None, {"key2": "value2"}, "tester", "props update")
    assert updated.properties.get("key2") == "value2"


# ── remove_edge ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_remove_edge_deletes_from_db(graph: ProjectGraph) -> None:
    await _add_node(graph, "doc.src")
    await _add_node(graph, "doc.tgt")
    edge = GraphEdge(edge_id="edge-001", edge_type=EdgeType.DERIVES_FROM.value, source_id="doc.src", target_id="doc.tgt")
    await graph.add_edge(edge)
    assert graph._g.has_edge("doc.src", "doc.tgt")

    await graph.remove_edge("edge-001")
    assert not graph._g.has_edge("doc.src", "doc.tgt")
    assert await graph.node("doc.src") is not None


@pytest.mark.asyncio
async def test_remove_edge_nonexistent_noop(graph: ProjectGraph) -> None:
    await graph.remove_edge("nonexistent-edge-id")  # Should not raise


# ── reparent_node ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reparent_node_updates_parent_id_and_version(graph: ProjectGraph) -> None:
    parent_a = GraphNode(node_id="doc.a", node_type=NodeType.DOCUMENT.value, title="A")
    parent_b = GraphNode(node_id="doc.b", node_type=NodeType.DOCUMENT.value, title="B")
    child = GraphNode(node_id="doc.a.child", node_type=NodeType.DOCUMENT.value, title="Child", parent_id="doc.a")
    for n in (parent_a, parent_b, child):
        await graph.add_node(n)

    original = await graph.node("doc.a.child")
    assert original is not None
    updated = await graph.reparent_node("doc.a.child", "doc.b", "tester", "moving child")

    assert updated.parent_id == "doc.b"
    assert updated.version == (original.version + 1)
    assert graph._g.nodes["doc.a.child"]["parent_id"] == "doc.b"


@pytest.mark.asyncio
async def test_reparent_node_missing_node_raises(graph: ProjectGraph) -> None:
    with pytest.raises(KeyError, match="not found"):
        await graph.reparent_node("nonexistent.node", "doc.b", "tester", "move")


@pytest.mark.asyncio
async def test_reparent_node_missing_parent_raises(graph: ProjectGraph) -> None:
    child = GraphNode(node_id="doc.orphan", node_type=NodeType.DOCUMENT.value, title="X")
    await graph.add_node(child)
    with pytest.raises(KeyError, match="New parent not found"):
        await graph.reparent_node("doc.orphan", "nonexistent.parent", "tester", "move")


@pytest.mark.asyncio
async def test_reparent_node_to_none_detaches(graph: ProjectGraph) -> None:
    parent = GraphNode(node_id="doc.parent", node_type=NodeType.DOCUMENT.value, title="P")
    child = GraphNode(node_id="doc.parent.child", node_type=NodeType.DOCUMENT.value, title="C", parent_id="doc.parent")
    for n in (parent, child):
        await graph.add_node(n)

    updated = await graph.reparent_node("doc.parent.child", None, "tester", "detach")
    assert updated.parent_id is None
