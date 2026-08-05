"""Additional behavioural tests for ProjectGraph engine + QueryMixin.

Covers change callbacks, DB rehydration, hash/layer derivation edge
cases, NX/DB divergence tolerance, and residual query filters.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from backend.graph.engine import ProjectGraph
from backend.graph.models import EdgeType, GraphEdge, GraphNode, NodeType


@pytest.fixture
async def db_path() -> AsyncIterator[Path]:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = Path(f.name)
    yield path
    os.unlink(path)


@pytest.fixture
async def graph(db_path: Path) -> ProjectGraph:
    g = ProjectGraph(db_path)
    await g.initialise()
    return g


async def _add(
    graph: ProjectGraph,
    node_id: str,
    node_type: str = NodeType.PARA.value,
    parent_id: str | None = None,
    **extra: Any,
) -> GraphNode:
    node = GraphNode(
        node_id=node_id, node_type=node_type, title=node_id,
        content=f"content {node_id}", parent_id=parent_id, **extra,
    )
    return await graph.add_node(node)


# ── on_change callbacks ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_on_change_fires_for_add_update_delete(graph: ProjectGraph) -> None:
    events: list[tuple[str, str]] = []
    graph.set_on_change(lambda action, d: events.append((action, d.get("node_id", ""))))

    await _add(graph, "n1")
    await graph.update_node("n1", "new content", None, "tester", "why")
    await graph.delete_node("n1")

    assert [action for action, _ in events] == ["added", "updated", "deleted"]
    assert events[0] == ("added", "n1")
    assert events[2] == ("deleted", "n1")


# ── initialise / rehydration ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_initialise_rehydrates_nodes_and_edges(db_path: Path, graph: ProjectGraph) -> None:
    await _add(graph, "a")
    await _add(graph, "b", parent_id="a")
    await graph.add_edge(GraphEdge(
        edge_type=EdgeType.IMPLEMENTS.value, source_id="b", target_id="a",
    ))

    fresh = ProjectGraph(db_path)
    await fresh.initialise()
    assert fresh.node_sync("a") is not None
    assert [c.node_id for c in fresh.children_sync("a")] == ["b"]
    assert fresh._g.has_edge("b", "a")


# ── add_node edge cases ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_node_computes_hash_when_blank(graph: ProjectGraph) -> None:
    node = GraphNode(node_id="h1", node_type=NodeType.PARA.value, title="t", content="body")
    node.content_hash = ""
    saved = await graph.add_node(node)
    assert saved.content_hash != ""


@pytest.mark.asyncio
async def test_add_node_unknown_type_keeps_layer_zero(graph: ProjectGraph) -> None:
    saved = await _add(graph, "weird", node_type="NOT_A_REAL_TYPE")
    assert saved.layer == 0


# ── update_node edge cases ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_node_promotes_trace_to_from_properties(graph: ProjectGraph) -> None:
    await _add(graph, "n1")
    updated, _ = await graph.update_node(
        "n1", None, {"trace_to": ["HLR-9"], "k": "v"}, "tester", "promote",
    )
    assert updated.trace_to == ["HLR-9"]
    assert "trace_to" not in updated.properties
    assert updated.properties["k"] == "v"


@pytest.mark.asyncio
async def test_update_node_explicit_trace_wins_over_properties(graph: ProjectGraph) -> None:
    await _add(graph, "n1")
    updated, _ = await graph.update_node(
        "n1", None, {"trace_to": ["stale"]}, "tester", "explicit",
        trace_to=["fresh"],
    )
    assert updated.trace_to == ["fresh"]
    assert "trace_to" not in updated.properties


@pytest.mark.asyncio
async def test_update_node_title_change_logged(graph: ProjectGraph) -> None:
    await _add(graph, "n1")
    updated, _ = await graph.update_node(
        "n1", None, None, "tester", "rename", title="Better Title",
    )
    assert updated.title == "Better Title"


@pytest.mark.asyncio
async def test_update_node_survives_missing_nx_entry(graph: ProjectGraph) -> None:
    await _add(graph, "n1")
    graph._g.remove_node("n1")
    updated, _ = await graph.update_node("n1", "fresh", None, "tester", "resync")
    assert updated.content == "fresh"
    assert updated.version == 2


# ── remove_edge / reparent divergence tolerance ──────────────────────────────


@pytest.mark.asyncio
async def test_remove_edge_keeps_nx_edge_with_different_id(graph: ProjectGraph) -> None:
    await _add(graph, "s")
    await _add(graph, "t")
    edge = await graph.add_edge(GraphEdge(
        edge_type=EdgeType.IMPLEMENTS.value, source_id="s", target_id="t",
    ))
    graph._g["s"]["t"]["edge_id"] = "some-other-edge"
    await graph.remove_edge(edge.edge_id)
    assert graph._g.has_edge("s", "t")
    assert await graph.all_edges() == []


@pytest.mark.asyncio
async def test_remove_edge_tolerates_missing_nx_edge(graph: ProjectGraph) -> None:
    await _add(graph, "s")
    await _add(graph, "t")
    edge = await graph.add_edge(GraphEdge(
        edge_type=EdgeType.IMPLEMENTS.value, source_id="s", target_id="t",
    ))
    graph._g.remove_edge("s", "t")
    await graph.remove_edge(edge.edge_id)
    assert await graph.all_edges() == []


@pytest.mark.asyncio
async def test_reparent_survives_missing_nx_entry(graph: ProjectGraph) -> None:
    await _add(graph, "p1")
    await _add(graph, "p2")
    await _add(graph, "kid", parent_id="p1")
    graph._g.remove_node("kid")
    updated = await graph.reparent_node("kid", "p2", "tester", "move")
    assert updated.parent_id == "p2"


# ── QueryMixin residual filters ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_nodes_review_required_filter(graph: ProjectGraph) -> None:
    await _add(graph, "r1", properties={"review_required": True})
    await _add(graph, "r2")
    flagged = await graph.nodes(review_required=True)
    assert [n.node_id for n in flagged] == ["r1"]
    clear = await graph.nodes(review_required=False)
    assert [n.node_id for n in clear] == ["r2"]


@pytest.mark.asyncio
async def test_all_edges_edge_type_filter(graph: ProjectGraph) -> None:
    await _add(graph, "a")
    await _add(graph, "b")
    await graph.add_edge(GraphEdge(
        edge_type=EdgeType.IMPLEMENTS.value, source_id="a", target_id="b",
    ))
    await graph.add_edge(GraphEdge(
        edge_type=EdgeType.VERIFIES.value, source_id="b", target_id="a",
    ))
    edges = await graph.all_edges(edge_type=EdgeType.VERIFIES.value)
    assert [e.edge_type for e in edges] == [EdgeType.VERIFIES.value]


@pytest.mark.asyncio
async def test_find_node_by_slug_matches_and_misses(graph: ProjectGraph) -> None:
    await _add(
        graph, "doc-other", node_type=NodeType.DOCUMENT.value,
        properties={"slug": "other"},
    )
    await _add(
        graph, "doc-hit", node_type=NodeType.DOCUMENT.value,
        properties={"slug": "wanted"},
    )
    hit = await graph.find_node_by_slug("wanted")
    assert hit is not None
    assert hit.node_id == "doc-hit"
    assert await graph.find_node_by_slug("nope") is None


@pytest.mark.asyncio
async def test_predecessors_sync_missing_node(graph: ProjectGraph) -> None:
    assert graph.predecessors_sync("ghost") == []


@pytest.mark.asyncio
async def test_predecessors_sync_with_and_without_edge_type(graph: ProjectGraph) -> None:
    await _add(graph, "tgt")
    await _add(graph, "src1")
    await _add(graph, "src2")
    await graph.add_edge(GraphEdge(
        edge_type=EdgeType.IMPLEMENTS.value, source_id="src1", target_id="tgt",
    ))
    await graph.add_edge(GraphEdge(
        edge_type=EdgeType.VERIFIES.value, source_id="src2", target_id="tgt",
    ))
    all_preds = {n.node_id for n in graph.predecessors_sync("tgt")}
    assert all_preds == {"src1", "src2"}
    verifies = [n.node_id for n in graph.predecessors_sync("tgt", edge_type=EdgeType.VERIFIES.value)]
    assert verifies == ["src2"]


@pytest.mark.asyncio
async def test_predecessors_sync_skips_unresolvable_predecessor(graph: ProjectGraph) -> None:
    await _add(graph, "tgt")
    await _add(graph, "src")
    await graph.add_edge(GraphEdge(
        edge_type=EdgeType.IMPLEMENTS.value, source_id="src", target_id="tgt",
    ))
    original = graph.node_sync
    graph.node_sync = lambda node_id: None if node_id == "src" else original(node_id)  # type: ignore[method-assign]
    assert graph.predecessors_sync("tgt") == []
