"""U7 — phase 5 is verification + residual-only; phase 4 authors allocations.

Allocation of HLRs to MODULEs is an output of phase-4 architecture authoring
(specs/03 Phase 4). Phase 5 keeps its number and completion criterion (no
``UNMODULARISED``) but authors nothing in the normal flow: it deterministically
verifies that every HLR lands in a MODULE's ``trace_to`` and dispatches
per-gap agents ONLY for residual unassigned HLRs.

Resume regression: a graph shaped like an old phase-5 entry state —
ARCHITECTURE + MODULEs present but HLRs unassigned — must still complete
phase 5 under the new registry via the per-gap residual route.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.analysis.gap_analyser import GapAnalyser
from backend.analysis.gaps import GapType
from backend.config.models import ForgeConfig, ProjectConfig
from backend.core.phase_store import PhaseStore
from backend.graph.engine import ProjectGraph
from backend.graph.models import GraphNode, LifecycleState, NodeType
from backend.pipeline.flow import ForgeFlow
from backend.pipeline.runner import _DEFAULT_STEPS, get_steps


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
    (ws / "forge.md").write_text("# Spec\n\nThe system shall parse.\n", encoding="utf-8")

    return ForgeFlow(
        pool=MagicMock(),
        graph=graph,
        config=ForgeConfig(
            project=ProjectConfig(
                name="phase5-resume", forgemd="forge.md", workspace_dir=str(ws)
            )
        ),
        broadcaster=MagicMock(),
        phase_store=PhaseStore(str(tmp_path / "phases.db")),
        workspace=ws,
    )


async def _seed_old_phase5_entry_graph(
    graph: ProjectGraph, *, assign_all: bool
) -> None:
    """ARCHITECTURE + MODULE exist; HLR-0002 is unassigned unless assign_all.

    This is exactly the state an old-pipeline build persisted when it was
    interrupted at phase-5 entry: phase 4 wrote ARCHITECTURE + MODULEs, and
    the retired batch-assignment step had not yet run.
    """
    module_trace = ["HLR-0001", "HLR-0002"] if assign_all else ["HLR-0001"]
    nodes = [
        GraphNode(
            node_id="PROJECT-0001", node_type=NodeType.PROJECT.value, layer=0,
            title="phase5-resume", lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="DOCUMENT-0001", node_type=NodeType.DOCUMENT.value, layer=1,
            title="Forge.md", content="The system shall parse and report.",
            parent_id="PROJECT-0001", lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="PARA-0001", node_type=NodeType.PARA.value, layer=2,
            title="Parsing and reporting",
            content="The system shall parse input. The system shall report results.",
            parent_id="DOCUMENT-0001", lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="HLR-0001", node_type=NodeType.HLR.value, layer=3,
            title="Parse input", content="The system shall parse input files.",
            parent_id="PARA-0001", lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="HLR-0002", node_type=NodeType.HLR.value, layer=3,
            title="Report results", content="The system shall report run results.",
            parent_id="PARA-0001", lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="ARCHITECTURE-0001", node_type=NodeType.ARCHITECTURE.value,
            layer=1, title="System architecture",
            content="## Executive Summary\nSingle-module pipeline design.",
            parent_id="PROJECT-0001", trace_to=["HLR-0001", "HLR-0002"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="MODULE-0001", node_type=NodeType.MODULE.value, layer=2,
            title="Pipeline engine",
            content="Responsibilities: parsing and reporting. Class plan: Engine.",
            parent_id="ARCHITECTURE-0001", trace_to=module_trace,
            lifecycle=LifecycleState.ACTIVE,
        ),
    ]
    for node in nodes:
        await graph.add_node(node)


def _patch_quality_noops(flow: ForgeFlow) -> Any:
    """Mock the LLM-backed quality steps — external I/O, not under test here."""
    return (
        patch.object(flow, "run_qual_check", AsyncMock(return_value=0)),
        patch.object(flow, "run_combined_quality_check", AsyncMock(return_value=[])),
        patch.object(flow, "run_semantic_check", AsyncMock(return_value=0)),
    )


class AssigningAgent:
    """Per-gap agent stub that appends the unassigned HLR to the MODULE."""

    def __init__(self, graph: ProjectGraph) -> None:
        self.graph = graph
        self.calls = 0

    async def __call__(self, *args: Any, **kwargs: Any) -> str:
        self.calls += 1
        module = await self.graph.node("MODULE-0001")
        assert module is not None
        if "HLR-0002" not in module.trace_to:
            await self.graph.update_node(
                "MODULE-0001", None, None, "test-agent",
                "assign residual HLR", trace_to=[*module.trace_to, "HLR-0002"],
            )
        return "OK: HLR-0002 appended to MODULE-0001 trace_to"


class NoProgressAgent:
    """Claims success, writes nothing — exercises the residual-dispatch bound."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, *args: Any, **kwargs: Any) -> str:
        self.calls += 1
        return "OK: assigned"


async def test_old_shape_graph_completes_phase5_via_residual_dispatch(
    flow: ForgeFlow, graph: ProjectGraph
) -> None:
    """Resume proof: an old half-assigned graph completes phase 5 under the
    new registry — the per-gap residual route closes UNMODULARISED."""
    # Registry-level fuse: phase 5 must run the verification pipeline, with
    # per-gap structural dispatch first and no batch authoring step.
    names = [s.__name__ for s in get_steps(5)]
    assert names[0] == "structural"
    assert names == [s.__name__ for s in _DEFAULT_STEPS]

    await _seed_old_phase5_entry_graph(graph, assign_all=False)
    open_before = {
        (g.type, g.node_id) for g in GapAnalyser().analyse(graph)
    }
    assert (GapType.UNMODULARISED, "HLR-0002") in open_before

    agent = AssigningAgent(graph)
    q1, q2, q3 = _patch_quality_noops(flow)
    with patch("backend.pipeline.dispatch.run_agent_task", new=agent), q1, q2, q3:
        await flow.run_phase(5)

    assert agent.calls >= 1, "residual UNMODULARISED gap was never dispatched"
    remaining = {g.type for g in GapAnalyser().analyse(graph)}
    assert GapType.UNMODULARISED not in remaining

    row = flow.phase_store.get(5)
    assert row is not None
    assert row["status"] == "complete"


async def test_fully_allocated_graph_completes_phase5_with_zero_dispatches(
    flow: ForgeFlow, graph: ProjectGraph
) -> None:
    """Normal flow: phase 4 already allocated every HLR, so phase 5 is pure
    verification — it completes without a single agent dispatch."""
    await _seed_old_phase5_entry_graph(graph, assign_all=True)

    agent = NoProgressAgent()
    q1, q2, q3 = _patch_quality_noops(flow)
    with patch("backend.pipeline.dispatch.run_agent_task", new=agent), q1, q2, q3:
        await flow.run_phase(5)

    assert agent.calls == 0, "verification-only phase 5 must not dispatch agents"
    row = flow.phase_store.get(5)
    assert row is not None
    assert row["status"] == "complete"


async def test_residual_dispatch_is_bounded_and_fails_loudly(
    flow: ForgeFlow, graph: ProjectGraph
) -> None:
    """An unresolvable residual HLR is dispatched a bounded number of times;
    the gap stays open and phase 5 does not report complete."""
    await _seed_old_phase5_entry_graph(graph, assign_all=False)

    agent = NoProgressAgent()
    q1, q2, q3 = _patch_quality_noops(flow)
    with patch("backend.pipeline.dispatch.run_agent_task", new=agent), q1, q2, q3:
        await flow.run_phase(5)

    assert agent.calls > 0, "the residual gap was never dispatched at all"
    assert agent.calls < 100, (
        f"agent dispatched {agent.calls} times for one unresolvable "
        f"UNMODULARISED gap — the circuit breaker is not bounding the loop"
    )
    remaining = {g.type for g in GapAnalyser().analyse(graph)}
    assert GapType.UNMODULARISED in remaining
    row = flow.phase_store.get(5)
    assert row is not None
    assert row["status"] != "complete"
