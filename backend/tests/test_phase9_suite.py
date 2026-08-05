"""Phase 9 (Write Test Strategy) — offline pipeline tests with a scripted agent.

Phase 9 runs the default pipeline (structural loop → per-gap dispatch) driven
by the single UNSUITED gap on the PROJECT; the Test Engineer answers it by
writing the SUITE node. Following ``test_phase_contracts_llm.py``, the only
LLM seam — ``backend.crew.dispatch.run_agent_task`` — is patched with a
scripted agent while gap analysis, dispatch routing, the quality steps, and a
real ``ProjectGraph`` on SQLite all stay live. The tests assert the machinery,
not model intelligence: the gap dispatches to the Test Engineer role, a SUITE
child of PROJECT closes it, re-runs are idempotent, and the task prompt built
by ``build_context_for_gap`` carries architecture + modules + HLR context.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from backend.agents.definitions import GAP_AGENT_MAPPING, AgentRole
from backend.analysis.gap_analyser import GapAnalyser
from backend.analysis.gaps import Gap, GapType
from backend.config.models import ForgeConfig, ProjectConfig
from backend.core.phase_store import PhaseStore
from backend.crew.flow import ForgeFlow
from backend.crew.quality import PHASE_TO_NODE_TYPES
from backend.crew.task_builder import (
    build_context_for_gap,
    build_task_description,
    find_suite_id,
)
from backend.graph.engine import ProjectGraph
from backend.graph.models import GraphNode, LifecycleState, NodeType

# ── Scripted agent (pattern from test_phase_contracts_llm.py) ────────────────


class ScriptedAgent:
    """Stands in for the LLM on both seams: per-gap dispatch and batch steps.

    Implements just enough resolvers to walk phases 0-9 — every structural
    gap up to and including UNSUITED — recording each gap it is handed so
    tests can assert what the pipeline chose to dispatch.
    """

    def __init__(self, graph: ProjectGraph) -> None:
        self.graph = graph
        self.seen: list[Gap] = []

    async def __call__(self, flow: Any, agent: Any, gap: Gap, **kwargs: Any) -> str:
        return await self._resolve(gap)

    async def astream_events(self, _payload: Any, **_kwargs: Any) -> Any:
        # UNSUITED is excluded: only batch phases (3, 5, 7, 8, 10) reach this
        # seam and none of them owns the SUITE — resolving it here would create
        # the SUITE before phase 9 ever ran, defeating what these tests assert.
        for gap in GapAnalyser().analyse(self.graph):
            if gap.type is GapType.UNSUITED:
                continue
            if hasattr(self, f"_resolve_{gap.type.value.lower()}"):
                await self._resolve(gap)
        return
        yield  # pragma: no cover — makes this an async generator

    async def _resolve(self, gap: Gap) -> str:
        self.seen.append(gap)
        handler = getattr(self, f"_resolve_{gap.type.value.lower()}", None)
        if handler is None:
            return f"no scripted resolution for {gap.type.value}"
        result: str = await handler(gap)
        return result

    def gap_types(self) -> list[str]:
        return [g.type.value for g in self.seen]

    async def _add(
        self,
        node_type: NodeType,
        title: str,
        content: str,
        parent_id: str | None,
        *,
        trace_to: list[str] | None = None,
        properties: dict[str, Any] | None = None,
    ) -> GraphNode:
        node_id = await self.graph.allocate_node_id(node_type.value)
        node = GraphNode(
            node_id=node_id,
            node_type=node_type.value,
            title=title,
            content=content,
            parent_id=parent_id,
            trace_to=trace_to or [],
            properties=properties or {},
            lifecycle=LifecycleState.ACTIVE,
            created_by="scripted-agent",
        )
        await self.graph.add_node(node)
        return node

    async def _resolve_unchunked_document(self, gap: Gap) -> str:
        for i in range(3):
            await self._add(
                NodeType.PARA,
                f"Requirement paragraph {i + 1}",
                f"The system shall perform documented behaviour number {i + 1} "
                f"whenever the corresponding precondition holds.",
                parent_id=gap.node_id,
                properties={"para_type": "requirement"},
            )
        return "created 3 PARA nodes"

    async def _resolve_uncovered_para(self, gap: Gap) -> str:
        await self._add(
            NodeType.HLR,
            f"HLR for {gap.node_id}",
            "The system shall satisfy the behaviour described by "
            f"{gap.node_id} under all documented preconditions.",
            parent_id=gap.node_id,
            trace_to=[gap.node_id],
        )
        return f"created HLR for {gap.node_id}"

    async def _resolve_unarchitected(self, gap: Gap) -> str:
        await self._add(
            NodeType.ARCHITECTURE,
            "System architecture",
            "The system decomposes into a single cohesive module that owns all "
            "documented behaviour and exposes one public interface.",
            parent_id=gap.node_id,
        )
        return "created ARCHITECTURE"

    async def _resolve_unmodularised(self, gap: Gap) -> str:
        arch = self._first(NodeType.ARCHITECTURE)
        if arch is None:
            return "no ARCHITECTURE to hang a MODULE from"
        module = self._first(NodeType.MODULE)
        if module is None:
            await self._add(
                NodeType.MODULE,
                "Core module",
                "Owns the documented behaviour of the system.",
                parent_id=arch.node_id,
                trace_to=[gap.node_id],
            )
            return f"created MODULE covering {gap.node_id}"
        await self.graph.update_node(
            module.node_id,
            None,
            None,
            "scripted-agent",
            f"cover {gap.node_id}",
            trace_to=[*module.trace_to, gap.node_id],
        )
        return f"extended MODULE trace to {gap.node_id}"

    async def _resolve_uncontracted(self, gap: Gap) -> str:
        await self._add(
            NodeType.CONTRACT,
            "Module public interface",
            "The module exposes a single documented entry point returning the "
            "computed result for the given inputs.",
            parent_id=gap.node_id,
            trace_to=[gap.node_id],
        )
        return f"created CONTRACT under {gap.node_id}"

    async def _resolve_unrefined_hlr(self, gap: Gap) -> str:
        await self._add(
            NodeType.LLR,
            f"LLR for {gap.node_id}",
            "The system shall implement the specific documented behaviour "
            f"required by {gap.node_id} for every valid input.",
            parent_id=gap.node_id,
            trace_to=[gap.node_id],
        )
        return f"created LLR under {gap.node_id}"

    async def _resolve_undesigned(self, gap: Gap) -> str:
        module = self._first(NodeType.MODULE)
        if module is None:
            return "no MODULE to hang a DESIGN from"
        await self._add(
            NodeType.DESIGN,
            f"Design for {gap.node_id}",
            "Class exposing one public method implementing the traced "
            "requirement, with validation on entry.",
            parent_id=module.node_id,
            trace_to=[gap.node_id],
            properties={"file_path": "src/core.py"},
        )
        return f"created DESIGN covering {gap.node_id}"

    # Phase 9 — the single SUITE under the PROJECT.
    async def _resolve_unsuited(self, gap: Gap) -> str:
        await self._add(
            NodeType.SUITE,
            "Test strategy",
            "Every requirement is verified by at least one behavioural test "
            "exercising its happy path and its documented error paths.",
            parent_id=gap.node_id,
        )
        return "created SUITE"

    def _first(self, node_type: NodeType) -> GraphNode | None:
        nodes = [n for n in self.graph.all_nodes() if n.node_type == node_type.value]
        return nodes[0] if nodes else None


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def graph(tmp_path: Path) -> ProjectGraph:
    g = ProjectGraph(tmp_path / "graph.db")
    await g.initialise()
    return g


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    (ws / "src").mkdir(parents=True)
    (ws / "tests").mkdir(parents=True)
    (ws / "forge.md").write_text(
        "# Spec\n\n"
        "The system shall do the first thing.\n\n"
        "The system shall do the second thing.\n\n"
        "The system shall do the third thing.\n",
        encoding="utf-8",
    )
    return ws


@pytest.fixture
def flow(graph: ProjectGraph, workspace: Path, tmp_path: Path) -> ForgeFlow:
    config = ForgeConfig(
        project=ProjectConfig(
            name="phase9-suite-test", forgemd="forge.md", workspace_dir=str(workspace)
        )
    )
    pool = MagicMock()
    pool.get_agent_for_gap.return_value = MagicMock()
    return ForgeFlow(
        pool=pool,
        graph=graph,
        config=config,
        broadcaster=MagicMock(),
        phase_store=PhaseStore(str(tmp_path / "phases.db")),
        workspace=workspace,
    )


@pytest.fixture
def scripted(graph: ProjectGraph, flow: ForgeFlow) -> Iterator[ScriptedAgent]:
    """Close every LLM seam: per-gap dispatch plus the direct-LLM quality
    checkers (semantic, combined-quality, case-trace, design-consolidation).

    Checker failures propagate instead of failing open, so the fake must stand
    in for those checkers explicitly with a clean verdict from each.
    """
    agent = ScriptedAgent(graph)
    flow.pool.get_agent_for_gap.return_value = agent

    async def _not_a_duplicate(node_id: str, node_content: str, peers_text: str) -> bool:
        return False

    async def _clean_combined_verdict(items: Any) -> list[Any]:
        return []

    async def _all_traces_valid(only_ids: Any) -> int:
        return 0

    async def _no_consolidation(**kwargs: Any) -> int:
        return 0

    with (
        patch("backend.crew.dispatch.run_agent_task", new=agent),
        patch(
            "backend.crew.semantic_duplicate_check.create_semantic_checker",
            return_value=_not_a_duplicate,
        ),
        patch(
            "backend.crew.combined_quality_check.create_combined_quality_checker",
            return_value=_clean_combined_verdict,
        ),
        patch(
            "backend.crew.case_trace_check.create_case_trace_checker",
            return_value=_all_traces_valid,
        ),
        patch(
            "backend.crew.design_consolidation.create_design_consolidator",
            return_value=_no_consolidation,
        ),
    ):
        yield agent


async def _gap_types(graph: ProjectGraph) -> set[str]:
    return {g.type.value for g in GapAnalyser().analyse(graph)}


def _nodes(graph: ProjectGraph, node_type: NodeType) -> list[GraphNode]:
    return [n for n in graph.all_nodes() if n.node_type == node_type.value]


# ── Routing and pipeline wiring ──────────────────────────────────────────────


class TestPhase09Routing:
    """Phase 9 work routes to the Test Engineer and owns only SUITE nodes."""

    def test_suite_gaps_route_to_the_test_engineer(self) -> None:
        assert GAP_AGENT_MAPPING[GapType.UNSUITED] is AgentRole.TEST_ENGINEER
        assert GAP_AGENT_MAPPING[GapType.STALE_SUITE] is AgentRole.TEST_ENGINEER

    def test_phase_9_produces_only_suite_nodes(self) -> None:
        assert PHASE_TO_NODE_TYPES[9] == ["SUITE"]

    def test_phase_9_runs_the_default_pipeline(self) -> None:
        from backend.crew.phase_pipeline import get_steps

        assert [s.__name__ for s in get_steps(9)] == [
            "structural",
            "quality_gaps",
            "combined_quality",
            "semantic",
        ]


# ── Phase 9 postconditions under real pipeline machinery ─────────────────────


class TestPhase09WritesSuite:
    """Postcondition: exactly one SUITE under the PROJECT; UNSUITED closed."""

    async def test_unsuited_gap_dispatches_and_suite_closes_it(
        self, flow: ForgeFlow, graph: ProjectGraph, scripted: ScriptedAgent
    ) -> None:
        for phase in range(9):
            await flow.run_phase(phase)
        assert GapType.UNSUITED.value in await _gap_types(graph)

        await flow.run_phase(9)

        assert GapType.UNSUITED.value in scripted.gap_types(), (
            "phase 9 never dispatched the UNSUITED gap"
        )
        routed = [c.args[0] for c in flow.pool.get_agent_for_gap.call_args_list]
        assert GapType.UNSUITED in routed, (
            "dispatch never asked the pool for the UNSUITED agent"
        )

        suites = _nodes(graph, NodeType.SUITE)
        assert len(suites) == 1, f"expected 1 SUITE, got {len(suites)}"
        project_id = _nodes(graph, NodeType.PROJECT)[0].node_id
        assert suites[0].parent_id == project_id, (
            f"SUITE must hang off the PROJECT, got {suites[0].parent_id}"
        )
        assert GapType.UNSUITED.value not in await _gap_types(graph)
        assert find_suite_id(graph) == suites[0].node_id

    async def test_rerunning_does_not_create_a_second_suite(
        self, flow: ForgeFlow, graph: ProjectGraph, scripted: ScriptedAgent
    ) -> None:
        """Phase re-runs are documented as idempotent — one PROJECT, one SUITE."""
        for phase in range(10):
            await flow.run_phase(phase)
        assert len(_nodes(graph, NodeType.SUITE)) == 1

        await flow.run_phase(9)

        assert len(_nodes(graph, NodeType.SUITE)) == 1


# ── The SUITE task prompt carries the system-wide context ────────────────────


class TestPhase09SuitePrompt:
    """The _suite prompt is grounded in ARCHITECTURE + MODULEs + HLRs
    assembled by ``build_context_for_gap`` (design/19 'Context Provided')."""

    async def test_task_description_embeds_architecture_modules_and_hlrs(
        self, flow: ForgeFlow, graph: ProjectGraph, scripted: ScriptedAgent
    ) -> None:
        for phase in range(9):
            await flow.run_phase(phase)

        gap = next(
            g for g in GapAnalyser().analyse(graph) if g.type is GapType.UNSUITED
        )
        project_id = _nodes(graph, NodeType.PROJECT)[0].node_id
        assert gap.node_id == project_id

        ctx = build_context_for_gap(graph, gap)
        arch = _nodes(graph, NodeType.ARCHITECTURE)[0]
        modules = _nodes(graph, NodeType.MODULE)
        hlrs = _nodes(graph, NodeType.HLR)
        assert modules and hlrs, "pipeline did not produce MODULEs/HLRs to embed"

        assert f"[ARCHITECTURE {arch.node_id}]" in ctx
        assert "ALL MODULE NODES" in ctx
        assert all(m.node_id in ctx for m in modules)
        assert "ALL HLR REQUIREMENTS" in ctx
        assert all(h.node_id in ctx for h in hlrs)

        description, expected_output = build_task_description(
            gap, ctx, attempt=1, suite_id=find_suite_id(graph)
        )
        assert "SUITE" in description
        assert project_id in description
        assert arch.node_id in description
        assert all(m.node_id in description for m in modules)
        assert all(h.node_id in description for h in hlrs)
        assert "SUITE" in expected_output
