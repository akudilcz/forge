"""Offline contract tests for the LLM-driven phases, using a scripted agent.

Phases 2-10 are the nine that close every structural gap and build the whole
node/parent/trace contract — and until now their postconditions were asserted
only inside ``test_full_pipeline.py``, which is ``@pytest.mark.integration``,
needs live API keys, and takes hours. In practice CI asserted nothing about them.

The trick that makes them testable offline is that **only one function actually
talks to an LLM**: ``backend.crew.dispatch.run_agent_task``. Patch it and
everything else stays real — the gap analyser decides what work exists, the
quality and semantic steps run, ``PhaseAuditor`` gates completion, and a genuine
``ProjectGraph`` on SQLite records the result. The fake simply plays the part of
the agent, writing the nodes a competent agent would write for the gap it is
handed.

That makes these tests meaningful in a specific way: they do not check that the
LLM is smart. They check that **given correct agent output, the pipeline
machinery produces the documented postcondition** — gaps close, parents are
right, layers are right, traces land, and the phase reports complete. A
regression in the pipeline shows up here in milliseconds and for free.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from backend.analysis.gap_analyser import GapAnalyser
from backend.analysis.gaps import Gap, GapType
from backend.config.models import ForgeConfig, ProjectConfig
from backend.core.phase_store import PhaseStore
from backend.crew.flow import ForgeFlow
from backend.graph.engine import ProjectGraph
from backend.graph.models import GraphNode, LifecycleState, NodeType

# ── Scripted agent ───────────────────────────────────────────────────────────


class ScriptedAgent:
    """Stands in for the LLM, resolving each gap by writing real graph nodes.

    FORGE reaches its agents through two distinct seams, and a harness has to
    cover both:

    * **Per-gap dispatch** (phases 2, 4, 6, 9) calls
      ``dispatch.run_agent_task(flow, agent, gap, ...)`` once per gap. That is
      patched with this object's ``__call__``.
    * **Batch steps** (phases 3, 5, 7, 8, 10) bypass that entirely and drive
      ``agent.astream_events(...)`` on whatever ``pool.get_agent_for_gap``
      returned, resolving many gaps in one LLM turn. That is covered by making
      the pool return this same object and implementing ``astream_events``.

    Missing the second seam is easy — the tests simply report the phase making
    no progress — so both are exercised here.

    Every gap handled is recorded, letting a test assert *which* work the
    pipeline chose to dispatch. That is often more informative than the end
    state, because it shows whether gap analysis identified the right units.
    """

    def __init__(self, graph: ProjectGraph) -> None:
        self.graph = graph
        self.seen: list[Gap] = []
        self.calls = 0

    # ── Seam 1: per-gap dispatch ─────────────────────────────────────────────

    async def __call__(self, flow: Any, agent: Any, gap: Gap, **kwargs: Any) -> str:
        self.calls += 1
        return await self._resolve(gap)

    # ── Seam 2: batch steps ──────────────────────────────────────────────────

    async def astream_events(
        self, _payload: Any, **_kwargs: Any
    ) -> Any:  # AsyncIterator, kept loose to match LangGraph's surface
        """Resolve every currently-open gap this agent knows how to close.

        A real batch agent reads the prompt; this one reads the graph, which
        yields the same observable effect without needing to parse a prompt.
        """
        self.calls += 1
        for gap in GapAnalyser().analyse(self.graph):
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

    # Phase 2 — chunk the DOCUMENT into PARA nodes.
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

    # Phase 3 — derive an HLR from a requirement-bearing PARA.
    async def _resolve_uncovered_para(self, gap: Gap) -> str:
        para = await self.graph.node(gap.node_id)
        await self._add(
            NodeType.HLR,
            f"HLR for {gap.node_id}",
            "The system shall satisfy the behaviour described by "
            f"{gap.node_id} under all documented preconditions.",
            parent_id=gap.node_id,
            trace_to=[gap.node_id] if para else [],
        )
        return f"created HLR for {gap.node_id}"

    # Phase 4 — one ARCHITECTURE under the PROJECT.
    async def _resolve_unarchitected(self, gap: Gap) -> str:
        await self._add(
            NodeType.ARCHITECTURE,
            "System architecture",
            "The system decomposes into a single cohesive module that owns all "
            "documented behaviour and exposes one public interface.",
            parent_id=gap.node_id,
        )
        return "created ARCHITECTURE"

    # Phase 5 — a MODULE covering the uncovered HLR (coverage is via trace_to).
    async def _resolve_unmodularised(self, gap: Gap) -> str:
        arch = self._first(NodeType.ARCHITECTURE)
        if arch is None:
            return "no ARCHITECTURE to hang a MODULE from"
        module = self._first(NodeType.MODULE)
        if module is None:
            module = await self._add(
                NodeType.MODULE,
                "Core module",
                "Owns the documented behaviour of the system.",
                parent_id=arch.node_id,
                trace_to=[gap.node_id],
            )
            return f"created MODULE covering {gap.node_id}"
        # One module covers every HLR — extend its trace rather than adding more.
        await self.graph.update_node(
            module.node_id,
            None,
            None,
            "scripted-agent",
            f"cover {gap.node_id}",
            trace_to=[*module.trace_to, gap.node_id],
        )
        return f"extended MODULE trace to {gap.node_id}"

    # Phase 6 — a CONTRACT child per MODULE.
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

    # Phase 7 — LLR children decomposing an HLR.
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

    # Phase 8 — a DESIGN covering the LLR (coverage is via trace_to).
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

    # Phase 10 — a CASE per HLR and per LLR.
    async def _resolve_untested_hlr(self, gap: Gap) -> str:
        await self._add(
            NodeType.CASE_HLR,
            f"Case for {gap.node_id}",
            f"Given a valid input, when the system runs, then {gap.node_id} holds.",
            parent_id=gap.node_id,
            trace_to=[gap.node_id],
        )
        return f"created CASE_HLR for {gap.node_id}"

    async def _resolve_untested_llr(self, gap: Gap) -> str:
        await self._add(
            NodeType.CASE_LLR,
            f"Case for {gap.node_id}",
            f"Given a valid input, when the method runs, then {gap.node_id} holds.",
            parent_id=gap.node_id,
            trace_to=[gap.node_id],
        )
        return f"created CASE_LLR for {gap.node_id}"

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
            name="llm-contract-test", forgemd="forge.md", workspace_dir=str(workspace)
        )
    )
    # Offline scripted test: every real checker seam is mocked, but build_llm
    # still constructs a client — declare the endpoint keyless explicitly.
    config.llm.keyless = True
    pool = MagicMock()
    pool.get_agent_for_gap.return_value = MagicMock()  # a non-None "agent"
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
    """Close every LLM seam: per-gap dispatch, batch ``astream_events``, and
    the direct-LLM quality checkers (semantic, combined-quality, case-trace,
    design-consolidation).

    Checker failures now propagate instead of failing open, so the fake must
    stand in for those checkers explicitly — a clean verdict from each, which
    is what "given correct agent output" means for the quality steps.
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
            "backend.quality.semantic_duplicate_check.create_semantic_checker",
            return_value=_not_a_duplicate,
        ),
        patch(
            "backend.quality.combined_check.create_combined_quality_checker",
            return_value=_clean_combined_verdict,
        ),
        patch(
            "backend.quality.case_trace_check.create_case_trace_checker",
            return_value=_all_traces_valid,
        ),
        patch(
            "backend.quality.design_consolidation.create_design_consolidator",
            return_value=_no_consolidation,
        ),
    ):
        yield agent


async def _gap_types(graph: ProjectGraph) -> set[str]:
    return {g.type.value for g in GapAnalyser().analyse(graph)}


def _nodes(graph: ProjectGraph, node_type: NodeType) -> list[GraphNode]:
    return [n for n in graph.all_nodes() if n.node_type == node_type.value]


# ── Phase 2 — Parse Document ─────────────────────────────────────────────────


class TestPhase02ParseDocument:
    """Postcondition: PARA nodes under the DOCUMENT; UNCHUNKED_DOCUMENT closed."""

    async def test_creates_paras_under_the_document_and_closes_the_gap(
        self, flow: ForgeFlow, graph: ProjectGraph, scripted: ScriptedAgent
    ) -> None:
        await flow.run_phase(0)
        await flow.run_phase(1)
        assert GapType.UNCHUNKED_DOCUMENT.value in await _gap_types(graph)

        await flow.run_phase(2)

        paras = _nodes(graph, NodeType.PARA)
        assert paras, "phase 2 produced no PARA nodes"

        document_id = _nodes(graph, NodeType.DOCUMENT)[0].node_id
        for para in paras:
            assert para.parent_id == document_id, (
                f"{para.node_id} must hang off the DOCUMENT, got {para.parent_id}"
            )
            assert para.layer == 1

        assert GapType.UNCHUNKED_DOCUMENT.value not in await _gap_types(graph)

    async def test_dispatches_the_document_chunking_gap(
        self, flow: ForgeFlow, scripted: ScriptedAgent
    ) -> None:
        """Gap analysis must ask for exactly the work the phase owns."""
        await flow.run_phase(0)
        await flow.run_phase(1)
        await flow.run_phase(2)

        assert GapType.UNCHUNKED_DOCUMENT.value in scripted.gap_types()

    async def test_does_not_run_without_a_document(
        self, flow: ForgeFlow, graph: ProjectGraph, scripted: ScriptedAgent
    ) -> None:
        """Phase 2's precondition is a DOCUMENT; with none, nothing is created."""
        await flow.run_phase(0)  # PROJECT only — phase 1 deliberately skipped

        await flow.run_phase(2)

        assert _nodes(graph, NodeType.PARA) == []


# ── Phase 3 — Derive HLRs ────────────────────────────────────────────────────


class TestPhase03DeriveHLRs:
    """Postcondition: an HLR per requirement PARA, tracing back to it."""

    async def test_creates_hlrs_that_trace_to_their_para(
        self, flow: ForgeFlow, graph: ProjectGraph, scripted: ScriptedAgent
    ) -> None:
        for phase in (0, 1, 2, 3):
            await flow.run_phase(phase)

        hlrs = _nodes(graph, NodeType.HLR)
        assert hlrs, "phase 3 produced no HLR nodes"

        para_ids = {n.node_id for n in _nodes(graph, NodeType.PARA)}
        for hlr in hlrs:
            assert hlr.trace_to, f"{hlr.node_id} traces to nothing"
            assert set(hlr.trace_to) <= para_ids, (
                f"{hlr.node_id} traces to unknown nodes: {hlr.trace_to}"
            )

    async def test_closes_the_uncovered_para_gap(
        self, flow: ForgeFlow, graph: ProjectGraph, scripted: ScriptedAgent
    ) -> None:
        for phase in (0, 1, 2):
            await flow.run_phase(phase)
        assert GapType.UNCOVERED_PARA.value in await _gap_types(graph)

        await flow.run_phase(3)

        assert GapType.UNCOVERED_PARA.value not in await _gap_types(graph)


# ── Phase 4 — Create Architecture ────────────────────────────────────────────


class TestPhase04Architecture:
    """Postcondition: exactly one ARCHITECTURE under the PROJECT."""

    async def test_creates_exactly_one_architecture(
        self, flow: ForgeFlow, graph: ProjectGraph, scripted: ScriptedAgent
    ) -> None:
        for phase in (0, 1, 2, 3, 4):
            await flow.run_phase(phase)

        architectures = _nodes(graph, NodeType.ARCHITECTURE)
        assert len(architectures) == 1, f"expected 1 ARCHITECTURE, got {len(architectures)}"

        project_id = _nodes(graph, NodeType.PROJECT)[0].node_id
        assert architectures[0].parent_id == project_id

    async def test_rerunning_does_not_create_a_second_architecture(
        self, flow: ForgeFlow, graph: ProjectGraph, scripted: ScriptedAgent
    ) -> None:
        """Phase re-runs are documented as idempotent."""
        for phase in (0, 1, 2, 3, 4):
            await flow.run_phase(phase)

        await flow.run_phase(4)

        assert len(_nodes(graph, NodeType.ARCHITECTURE)) == 1


# ── Cross-phase invariants under real pipeline machinery ─────────────────────


class TestPipelineInvariants:
    async def test_no_dangling_parents_after_five_phases(
        self, flow: ForgeFlow, graph: ProjectGraph, scripted: ScriptedAgent
    ) -> None:
        for phase in range(5):
            await flow.run_phase(phase)

        ids = {n.node_id for n in graph.all_nodes()}
        dangling = [
            (n.node_id, n.parent_id)
            for n in graph.all_nodes()
            if n.parent_id is not None and n.parent_id not in ids
        ]
        assert dangling == [], f"dangling parents: {dangling}"

    async def test_trace_targets_all_resolve(
        self, flow: ForgeFlow, graph: ProjectGraph, scripted: ScriptedAgent
    ) -> None:
        for phase in range(5):
            await flow.run_phase(phase)

        ids = {n.node_id for n in graph.all_nodes()}
        broken = [
            (n.node_id, t) for n in graph.all_nodes() for t in n.trace_to if t not in ids
        ]
        assert broken == [], f"traces pointing at missing nodes: {broken}"

    async def test_node_ids_are_unique(
        self, flow: ForgeFlow, graph: ProjectGraph, scripted: ScriptedAgent
    ) -> None:
        for phase in range(5):
            await flow.run_phase(phase)

        ids = [n.node_id for n in graph.all_nodes()]
        assert len(ids) == len(set(ids)), "duplicate node ids allocated"

    async def test_all_nine_llm_phases_close_their_own_gap_types(
        self, flow: ForgeFlow, graph: ProjectGraph, scripted: ScriptedAgent
    ) -> None:
        """Walk 0→10 and assert each phase closes exactly what it owns.

        This is the offline equivalent of the multi-hour live pipeline test: it
        proves the *machinery* — gap analysis, dispatch, the batch steps, the
        quality passes and the auditor — turns correct agent output into the
        documented postcondition for every LLM phase.
        """
        from backend.analysis.phase_auditor import PHASE_COMPLETION_CRITERIA

        for phase in range(11):
            await flow.run_phase(phase)

            owned = PHASE_COMPLETION_CRITERIA.get(phase, frozenset())
            remaining = await _gap_types(graph)
            still_open = {g.value for g in owned} & remaining
            assert not still_open, (
                f"phase {phase} finished with its own gap types still open: "
                f"{sorted(still_open)}"
            )

    async def test_the_full_requirement_chain_is_traced(
        self, flow: ForgeFlow, graph: ProjectGraph, scripted: ScriptedAgent
    ) -> None:
        """PARA → HLR → LLR → DESIGN and LLR → CASE_LLR all resolve.

        Traceability is FORGE's headline promise, so assert the chain exists as
        links rather than merely as nodes.
        """
        for phase in range(11):
            await flow.run_phase(phase)

        by_id = {n.node_id: n for n in graph.all_nodes()}

        def traced_types(node: GraphNode) -> set[str]:
            return {by_id[t].node_type for t in node.trace_to if t in by_id}

        for hlr in _nodes(graph, NodeType.HLR):
            assert "PARA" in traced_types(hlr), f"{hlr.node_id} does not trace to a PARA"

        llrs = _nodes(graph, NodeType.LLR)
        assert llrs, "no LLRs were derived"
        for llr in llrs:
            assert "HLR" in traced_types(llr), f"{llr.node_id} does not trace to an HLR"

        designs = _nodes(graph, NodeType.DESIGN)
        assert designs, "no DESIGNs were created"
        for design in designs:
            assert "LLR" in traced_types(design), f"{design.node_id} does not trace to an LLR"

        covered_llrs = {t for d in designs for t in d.trace_to}
        uncovered = [llr.node_id for llr in llrs if llr.node_id not in covered_llrs]
        assert not uncovered, f"LLRs with no DESIGN coverage: {uncovered}"

    async def test_pipeline_terminates_rather_than_cycling_forever(
        self, flow: ForgeFlow, scripted: ScriptedAgent
    ) -> None:
        """The pipeline caps itself at 12 cycles; a runaway would blow past it.

        Each phase dispatches a bounded amount of work, so a call count in the
        thousands means the loop is not converging.
        """
        for phase in range(5):
            await flow.run_phase(phase)

        assert scripted.calls < 200, (
            f"pipeline dispatched {scripted.calls} agent calls for 5 phases — "
            "it is not converging"
        )
