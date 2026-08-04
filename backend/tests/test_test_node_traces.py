"""TEST nodes must carry traces, or every RESULT node is orphaned.

Found by the live end-to-end build. ``_sync_code_nodes`` copies its DESIGN's
``trace_to`` onto the CODE node it creates; ``_sync_test_nodes`` created TEST
nodes with no ``trace_to`` at all. That asymmetry looked cosmetic and was not:
``result_recorder._find_trace_targets`` locates the owning TEST by scanning
``node.trace_to`` for the case id, so with an empty trace it never matched, and
every RESULT node ended up parented on the CASE instead of the TEST.

The observable damage in a real build: 195 RESULT nodes, ~197 unresolved
``ORPHAN_NODE`` gaps, and a broken CASE → TEST → RESULT evidence chain — while
every phase still reported complete, because ORPHAN_NODE is a quality gap that
appears in no phase's completion criteria.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.crew.workspace_sync import _sync_test_nodes
from backend.graph.engine import ProjectGraph
from backend.graph.models import GraphNode, LifecycleState, NodeType


@pytest.fixture
async def graph(tmp_path: Path) -> ProjectGraph:
    g = ProjectGraph(tmp_path / "g.db")
    await g.initialise()
    return g


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "tests").mkdir(parents=True)
    (ws / "tests" / "test_core.py").write_text(
        "from tracing import traces\n\n\n"
        '@traces("LLR-0001", case="CASE_LLR-0001")\n'
        "def test_happy_path():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    return ws


async def _seed_case(graph: ProjectGraph) -> GraphNode:
    case = GraphNode(
        node_id="CASE_LLR-0001",
        node_type=NodeType.CASE_LLR.value,
        title="Sorts ascending",
        content="Given a list, when sorted, then it is ascending.",
        parent_id="LLR-0001",
        trace_to=["LLR-0001"],
        properties={"file_path": "tests/test_core.py"},
        lifecycle=LifecycleState.ACTIVE,
    )
    await graph.add_node(
        GraphNode(
            node_id="LLR-0001",
            node_type=NodeType.LLR.value,
            title="Sort ascending",
            content="The system shall sort ascending.",
            lifecycle=LifecycleState.ACTIVE,
        )
    )
    await graph.add_node(case)
    return case


async def test_test_node_traces_back_to_its_case(
    graph: ProjectGraph, workspace: Path
) -> None:
    """The link result_recorder actually searches on."""
    await _seed_case(graph)

    await _sync_test_nodes(graph, workspace, [])

    tests = [n for n in graph.all_nodes() if n.node_type == NodeType.TEST.value]
    assert len(tests) == 1, f"expected 1 TEST node, got {len(tests)}"

    test_node = tests[0]
    assert test_node.trace_to, (
        "TEST node has an empty trace_to — result_recorder cannot find it, so "
        "every RESULT will be orphaned"
    )
    assert "CASE_LLR-0001" in test_node.trace_to


async def test_test_node_inherits_the_case_requirement_traces(
    graph: ProjectGraph, workspace: Path
) -> None:
    """Carrying the LLR through keeps the requirement reachable from the test."""
    await _seed_case(graph)

    await _sync_test_nodes(graph, workspace, [])

    test_node = next(n for n in graph.all_nodes() if n.node_type == NodeType.TEST.value)
    assert "LLR-0001" in test_node.trace_to, (
        "the requirement the case verifies did not propagate to the TEST node"
    )


async def test_result_recorder_can_locate_the_test_node(
    graph: ProjectGraph, workspace: Path
) -> None:
    """The end of the chain: CASE → TEST → RESULT.

    Asserts the actual lookup rather than the field, so the test fails if either
    side of the contract drifts.
    """
    case = await _seed_case(graph)
    await _sync_test_nodes(graph, workspace, [])

    case_ids = {case.node_id}
    matches = [
        n
        for n in graph.all_nodes()
        if n.node_type == NodeType.TEST.value
        and any(cid in (n.trace_to or []) for cid in case_ids)
    ]
    assert matches, (
        "result_recorder's TEST lookup (any(case_id in node.trace_to)) finds "
        "nothing — RESULT nodes would be parented on the CASE and orphaned"
    )


async def test_no_node_is_left_orphaned_after_sync(
    graph: ProjectGraph, workspace: Path
) -> None:
    await _seed_case(graph)

    await _sync_test_nodes(graph, workspace, [])

    ids = {n.node_id for n in graph.all_nodes()}
    dangling = [
        (n.node_id, t)
        for n in graph.all_nodes()
        for t in (n.trace_to or [])
        if t not in ids
    ]
    assert dangling == [], f"traces point at missing nodes: {dangling}"


async def test_code_and_test_sync_are_symmetric() -> None:
    """Both sync paths must set trace_to — the asymmetry was the bug.

    Pinned by inspection because the divergence is what regressed: one function
    was updated and its sibling was not.
    """
    import inspect

    from backend.crew import workspace_sync

    code_src = inspect.getsource(workspace_sync._sync_code_nodes)
    test_src = inspect.getsource(workspace_sync._sync_test_nodes)

    assert "trace_to=" in code_src
    assert "trace_to=" in test_src, (
        "_sync_test_nodes no longer sets trace_to while _sync_code_nodes does — "
        "this asymmetry orphaned every RESULT node once already"
    )
