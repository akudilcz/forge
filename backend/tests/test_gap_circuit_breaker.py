"""The structural loop must abandon a gap it cannot resolve.

``gap_fail_counts`` was tracked and logged on every no-progress dispatch but
never consulted, and ``max_iter_reached`` — despite a comment promising
"iteration >= budget" — was only ever set on API quota exhaustion. So a gap the
agent could not close was re-dispatched until the quota ran out. One offline test
run logged **3336 consecutive failures against a single gap**; against a live
model every one of those is a paid call.

Abandoning the gap is the loud outcome, not a silent one: the gap stays open, so
``PhaseAuditor`` fails the phase and the operator sees exactly what could not be
closed — bounded spend instead of an unbounded retry.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from backend.analysis.gaps import GapType
from backend.config.models import ForgeConfig, ProjectConfig
from backend.core.phase_store import PhaseStore
from backend.graph.engine import ProjectGraph
from backend.graph.models import GraphNode, LifecycleState, NodeType
from backend.pipeline.flow import ForgeFlow
from backend.pipeline.structural_loop import _MAX_GAP_ATTEMPTS


class NoProgressAgent:
    """An agent that always claims success but never changes the graph.

    This is the realistic failure the breaker exists for: a model that returns a
    confident "OK: created the node" while writing nothing. The loop detects it
    via the unchanged node count, which is exactly the "possible hallucination"
    path the code already logs.
    """

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, *args: Any, **kwargs: Any) -> str:
        self.calls += 1
        return "OK: created the requested node"

    async def astream_events(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return
        yield  # pragma: no cover — makes this an async generator


@pytest.fixture
async def graph(tmp_path: Path) -> ProjectGraph:
    g = ProjectGraph(tmp_path / "graph.db")
    await g.initialise()
    return g


@pytest.fixture
def flow(graph: ProjectGraph, tmp_path: Path) -> ForgeFlow:
    ws = tmp_path / "workspace"
    (ws / "src").mkdir(parents=True)
    (ws / "tests").mkdir(parents=True)
    (ws / "forge.md").write_text("# Spec\n\nThe system shall do a thing.\n", encoding="utf-8")

    pool = MagicMock()
    return ForgeFlow(
        pool=pool,
        graph=graph,
        config=ForgeConfig(
            project=ProjectConfig(
                name="breaker-test", forgemd="forge.md", workspace_dir=str(ws)
            )
        ),
        broadcaster=MagicMock(),
        phase_store=PhaseStore(str(tmp_path / "phases.db")),
        workspace=ws,
    )


async def _seed_document(graph: ProjectGraph) -> None:
    """PROJECT + DOCUMENT, which makes UNCHUNKED_DOCUMENT the open gap."""
    project = GraphNode(
        node_id="PROJECT-0001",
        node_type=NodeType.PROJECT.value,
        layer=0,
        title="breaker-test",
        lifecycle=LifecycleState.ACTIVE,
    )
    await graph.add_node(project)
    await graph.add_node(
        GraphNode(
            node_id="DOCUMENT-0001",
            node_type=NodeType.DOCUMENT.value,
            layer=1,
            title="Forge.md",
            content="The system shall do a thing.",
            parent_id="PROJECT-0001",
            lifecycle=LifecycleState.ACTIVE,
        )
    )


async def test_unresolvable_gap_does_not_retry_without_limit(
    flow: ForgeFlow, graph: ProjectGraph
) -> None:
    """The headline guarantee: bounded dispatches, not thousands."""
    await _seed_document(graph)
    agent = NoProgressAgent()
    flow.pool.get_agent_for_gap.return_value = agent

    with patch("backend.pipeline.dispatch.run_agent_task", new=agent):
        await flow.run_phase(2)

    # The outer pipeline may retry the step across cycles, so the bound is not
    # exactly _MAX_GAP_ATTEMPTS — but it must be a small multiple, nowhere near
    # the thousands of calls the unbounded loop produced.
    assert agent.calls > 0, "the agent was never dispatched at all"
    assert agent.calls < 100, (
        f"agent dispatched {agent.calls} times for one unresolvable gap — "
        "the circuit breaker is not bounding the loop"
    )


async def test_the_gap_stays_open_so_the_phase_fails_loudly(
    flow: ForgeFlow, graph: ProjectGraph
) -> None:
    """Abandoning must not be mistaken for resolving.

    If abandonment marked the phase complete, an unbuildable project would sail
    through to code generation with a missing requirement.
    """
    from backend.analysis.gap_analyser import GapAnalyser

    await _seed_document(graph)
    agent = NoProgressAgent()
    flow.pool.get_agent_for_gap.return_value = agent

    with patch("backend.pipeline.dispatch.run_agent_task", new=agent):
        await flow.run_phase(2)

    remaining = {g.type for g in GapAnalyser().analyse(graph)}
    assert GapType.UNCHUNKED_DOCUMENT in remaining, (
        "the gap was reported closed despite nothing being created"
    )

    row = flow.phase_store.get(2)
    assert row is not None
    assert row["status"] != "complete", (
        "phase 2 reported complete while its own gap is still open"
    )


async def test_threshold_is_small_enough_to_bound_cost() -> None:
    """A high threshold would defeat the point on a paid API."""
    assert 1 <= _MAX_GAP_ATTEMPTS <= 5, (
        f"_MAX_GAP_ATTEMPTS={_MAX_GAP_ATTEMPTS} is too permissive to bound spend"
    )


async def test_a_gap_that_resolves_is_not_abandoned(
    flow: ForgeFlow, graph: ProjectGraph
) -> None:
    """The breaker must not fire on agents that do make progress."""
    from backend.analysis.gap_analyser import GapAnalyser

    await _seed_document(graph)

    class WorkingAgent(NoProgressAgent):
        async def __call__(self, *args: Any, **kwargs: Any) -> str:
            self.calls += 1
            await self._write()
            return "OK"

        async def astream_events(self, *args: Any, **kwargs: Any) -> Any:
            self.calls += 1
            await self._write()
            return
            yield  # pragma: no cover

        async def _write(self) -> None:
            existing = [n for n in graph.all_nodes() if n.node_type == NodeType.PARA.value]
            if existing:
                return
            for i in range(2):
                nid = await graph.allocate_node_id(NodeType.PARA.value)
                await graph.add_node(
                    GraphNode(
                        node_id=nid,
                        node_type=NodeType.PARA.value,
                        layer=1,
                        title=f"Paragraph {i + 1}",
                        content=f"The system shall do documented thing {i + 1}.",
                        parent_id="DOCUMENT-0001",
                        lifecycle=LifecycleState.ACTIVE,
                    )
                )

    agent = WorkingAgent()
    flow.pool.get_agent_for_gap.return_value = agent

    # The created PARAs make the semantic step run its duplicate checker; mock
    # the LLM boundary (step failures now propagate instead of failing open).
    async def _no_dup_checker(node_id: str, node_content: str, peers_text: str) -> bool:
        return False

    with (
        patch("backend.pipeline.dispatch.run_agent_task", new=agent),
        patch.object(flow, "_build_semantic_checker", return_value=_no_dup_checker),
    ):
        await flow.run_phase(2)

    remaining = {g.type for g in GapAnalyser().analyse(graph)}
    assert GapType.UNCHUNKED_DOCUMENT not in remaining


async def test_dispatch_outcomes_are_recorded_in_the_work_queue_history(
    flow: ForgeFlow, graph: ProjectGraph
) -> None:
    """The Control Station History panel must receive real data.

    ``ActionHistory`` had a full API and a UI panel, but nothing ever called
    ``record_action`` — so the history was permanently empty and the panel
    permanently blank. Every dispatch now records its outcome, derived from the
    same node-count delta the loop already uses to judge progress.
    """
    from backend.work_queue import work_queue

    # work_queue is a module singleton and history is deliberately append-only,
    # so measure the delta rather than the absolute contents.
    before = len(work_queue.all_history)

    await _seed_document(graph)
    agent = NoProgressAgent()
    flow.pool.get_agent_for_gap.return_value = agent

    with patch("backend.pipeline.dispatch.run_agent_task", new=agent):
        await flow.run_phase(2)

    recorded = work_queue.all_history[before:]
    assert recorded, "no action was recorded — the History panel would be blank"

    mine = [e for e in recorded if e["category"] == "UNCHUNKED_DOCUMENT"]
    assert mine, f"expected an UNCHUNKED_DOCUMENT entry, got {recorded}"
    assert all(e["outcome"] == "no_change" for e in mine), (
        "an agent that changed nothing was recorded as an improvement"
    )
