"""Behavioural tests for AlgorithmMixin (backend/graph/_algorithms.py).

Covers traversal edge cases: missing nodes, dangling parents, depth
limits, self-parent cycles, and edge-based (NetworkX) relatives.
"""

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


async def _add(
    graph: ProjectGraph,
    node_id: str,
    node_type: str = NodeType.PARA.value,
    parent_id: str | None = None,
) -> GraphNode:
    node = GraphNode(
        node_id=node_id, node_type=node_type, title=node_id,
        content=f"content of {node_id}", parent_id=parent_id,
    )
    return await graph.add_node(node)


# ── ancestors ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ancestors_of_missing_node_returns_empty(graph: ProjectGraph) -> None:
    assert await graph.ancestors("no-such-node") == []


@pytest.mark.asyncio
async def test_ancestors_walks_parent_chain(graph: ProjectGraph) -> None:
    await _add(graph, "root")
    await _add(graph, "mid", parent_id="root")
    await _add(graph, "leaf", parent_id="mid")
    ids = [n.node_id for n in await graph.ancestors("leaf")]
    assert ids == ["mid", "root"]


@pytest.mark.asyncio
async def test_ancestors_depth_zero_skips_structural_walk(graph: ProjectGraph) -> None:
    await _add(graph, "root")
    await _add(graph, "leaf", parent_id="root")
    assert await graph.ancestors("leaf", depth=0) == []


@pytest.mark.asyncio
async def test_ancestors_stops_at_dangling_parent(graph: ProjectGraph) -> None:
    # parent_id points at a row that does not exist — walk stops cleanly.
    await _add(graph, "orphan", parent_id="ghost-parent")
    assert await graph.ancestors("orphan") == []


@pytest.mark.asyncio
async def test_ancestors_includes_edge_based_ancestors(graph: ProjectGraph) -> None:
    await _add(graph, "target")
    await _add(graph, "source")
    await graph.add_edge(GraphEdge(
        edge_type=EdgeType.IMPLEMENTS.value, source_id="source", target_id="target",
    ))
    ids = {n.node_id for n in await graph.ancestors("target")}
    assert ids == {"source"}


@pytest.mark.asyncio
async def test_ancestors_edge_and_parent_deduplicated(graph: ProjectGraph) -> None:
    # "parent" is both the structural parent and an edge ancestor: appears once.
    await _add(graph, "parent")
    await _add(graph, "child", parent_id="parent")
    await graph.add_edge(GraphEdge(
        edge_type=EdgeType.DERIVES_FROM.value, source_id="parent", target_id="child",
    ))
    ids = [n.node_id for n in await graph.ancestors("child")]
    assert ids == ["parent"]


@pytest.mark.asyncio
async def test_ancestors_skips_edge_ancestor_missing_from_db(graph: ProjectGraph) -> None:
    # An edge whose source was never persisted as a node: present in NX only.
    await _add(graph, "target")
    await graph.add_edge(GraphEdge(
        edge_type=EdgeType.IMPLEMENTS.value, source_id="ghost-src", target_id="target",
    ))
    assert await graph.ancestors("target") == []


# ── descendants ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_descendants_of_missing_node_returns_empty(graph: ProjectGraph) -> None:
    assert await graph.descendants("no-such-node") == []


@pytest.mark.asyncio
async def test_descendants_depth_limits_bfs(graph: ProjectGraph) -> None:
    await _add(graph, "a")
    await _add(graph, "b", parent_id="a")
    await _add(graph, "c", parent_id="b")
    ids = {n.node_id for n in await graph.descendants("a", depth=1)}
    assert ids == {"b"}


@pytest.mark.asyncio
async def test_descendants_self_parent_cycle_terminates(graph: ProjectGraph) -> None:
    await _add(graph, "loop")
    # Force a self-parent cycle directly in the in-memory graph.
    graph._g.nodes["loop"]["parent_id"] = "loop"
    assert await graph.descendants("loop") == []


@pytest.mark.asyncio
async def test_descendants_includes_edge_based_descendants(graph: ProjectGraph) -> None:
    await _add(graph, "src")
    await _add(graph, "dst")
    await graph.add_edge(GraphEdge(
        edge_type=EdgeType.IMPLEMENTS.value, source_id="src", target_id="dst",
    ))
    ids = {n.node_id for n in await graph.descendants("src")}
    assert ids == {"dst"}


@pytest.mark.asyncio
async def test_descendants_edge_target_already_structural_child(graph: ProjectGraph) -> None:
    await _add(graph, "src")
    await _add(graph, "dst", parent_id="src")
    await graph.add_edge(GraphEdge(
        edge_type=EdgeType.IMPLEMENTS.value, source_id="src", target_id="dst",
    ))
    ids = [n.node_id for n in await graph.descendants("src")]
    assert ids == ["dst"]


@pytest.mark.asyncio
async def test_descendants_skips_edge_target_missing_from_db(graph: ProjectGraph) -> None:
    await _add(graph, "src")
    await graph.add_edge(GraphEdge(
        edge_type=EdgeType.IMPLEMENTS.value, source_id="src", target_id="ghost-dst",
    ))
    assert await graph.descendants("src") == []


# ── impact_set ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_impact_set_missing_node_is_empty(graph: ProjectGraph) -> None:
    impact = await graph.impact_set("no-such-node")
    assert impact.root_node_id == "no-such-node"
    assert impact.stale_nodes == []
    assert impact.stale_count == 0


@pytest.mark.asyncio
async def test_impact_set_counts_descendants(graph: ProjectGraph) -> None:
    await _add(graph, "root")
    await _add(graph, "kid", parent_id="root")
    impact = await graph.impact_set("root")
    assert impact.stale_nodes == ["kid"]
    assert impact.stale_count == 1


# ── traceability ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_traceability_chain_lists_ancestors(graph: ProjectGraph) -> None:
    await _add(graph, "doc", node_type=NodeType.DOCUMENT.value)
    await _add(graph, "para", parent_id="doc")
    chain = await graph.traceability_chain("para")
    assert chain.node_id == "para"
    assert [a["node_id"] for a in chain.ancestors] == ["doc"]


@pytest.mark.asyncio
async def test_traceability_gaps_empty_graph(graph: ProjectGraph) -> None:
    gaps = await graph.traceability_gaps()
    assert gaps.unimplemented_requirements == []
    assert gaps.uncovered_requirements == []
    assert gaps.untested_code == []


@pytest.mark.asyncio
async def test_traceability_gaps_reports_missing_links(graph: ProjectGraph) -> None:
    await _add(graph, "HLR-1", node_type=NodeType.HLR.value)
    await _add(graph, "HLR-2", node_type=NodeType.HLR.value)
    await _add(graph, "CODE-1", node_type=NodeType.CODE.value)
    await _add(graph, "CODE-2", node_type=NodeType.CODE.value)
    await graph.add_edge(GraphEdge(
        edge_type=EdgeType.IMPLEMENTS.value, source_id="CODE-1", target_id="HLR-1",
    ))
    await graph.add_edge(GraphEdge(
        edge_type=EdgeType.VERIFIES.value, source_id="CODE-1", target_id="HLR-1",
    ))
    await graph.add_edge(GraphEdge(
        edge_type=EdgeType.EXERCISES.value, source_id="HLR-1", target_id="CODE-1",
    ))
    gaps = await graph.traceability_gaps()
    assert gaps.unimplemented_requirements == ["HLR-2"]
    assert gaps.uncovered_requirements == ["HLR-2"]
    assert gaps.untested_code == ["CODE-2"]
