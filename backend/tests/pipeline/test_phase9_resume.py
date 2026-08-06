"""U9 — phase 9 (SUITE) merged into phase 10 at the registry level.

Nothing downstream ever executes SUITE prose: the SUITE is *structured input*
to case authoring (its content sits in phase 10's static batch prefix), so
phase 9 keeps only the single UNSUITED per-gap dispatch — its quality/semantic
boundary is phase 10's (specs/03 Phases 9-10). Phase 10 opens with a
``suite_authoring`` guard so a resumed graph missing its SUITE still authors
it before any CASE is written, then validates every CASE's oracle
independently (``oracle_check``).

Resume regressions (real ProjectGraph, mocked LLM seams — same pattern as
``test_phase5_resume.py`` / ``test_phase8_resume.py``):
  (a) old graph with SUITE done + CASEs done passes 9 and 10 — the oracle
      check runs over every CASE, zero authoring dispatches when all PASS;
  (b) graph missing its SUITE completes 9-then-10 under the new registries,
      and the batch prompt carries the SUITE content in its static prefix;
  (c) graph missing its SUITE resumed directly at phase 10 authors the SUITE
      first via the suite_authoring guard, then the CASEs.
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
from backend.pipeline.runner import get_steps


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

    config = ForgeConfig(
        project=ProjectConfig(
            name="phase9-resume", forgemd="forge.md", workspace_dir=str(ws)
        )
    )
    # Offline test: LLM-backed checkers are patched, but build_llm still
    # constructs a client — keyless and non-routable so a leaked call fails
    # fast locally instead of dialing a provider.
    config.llm.keyless = True
    config.llm.base_url = "http://localhost:1/v1"
    pool = MagicMock()
    pool.get_agent_for_gap.return_value = MagicMock()
    return ForgeFlow(
        pool=pool,
        graph=graph,
        config=config,
        broadcaster=MagicMock(),
        phase_store=PhaseStore(str(tmp_path / "phases.db")),
        workspace=ws,
    )


_PUBLIC_API = [
    {
        "module": "engine",
        "symbol": "run",
        "kind": "function",
        "signature": "def run(path: str) -> Report",
        "raises": [
            {"cls": "ParseError", "base": "ValueError", "when": "input is malformed"}
        ],
        "postconditions": ["returns a Report covering every input row"],
    }
]

_SUITE_CONTENT = (
    "Risk-based behavioural strategy: every requirement is verified by at "
    "least one pytest case covering its happy path and each documented "
    "error path, with contract exceptions asserted by base class."
)


async def _seed_graph(
    graph: ProjectGraph, *, with_suite: bool, with_cases: bool
) -> None:
    """Skeleton through phase 8 plus an optional SUITE and CASE population."""
    nodes = [
        GraphNode(
            node_id="PROJECT-0001", node_type=NodeType.PROJECT.value, layer=0,
            title="phase9-resume", lifecycle=LifecycleState.ACTIVE,
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
            parent_id="ARCHITECTURE-0001", trace_to=["HLR-0001", "HLR-0002"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CONTRACT-0001", node_type=NodeType.CONTRACT.value, layer=3,
            title="Engine public interface",
            content="def run(path: str) -> Report — parses input and reports.",
            parent_id="MODULE-0001", lifecycle=LifecycleState.ACTIVE,
            properties={"public_api": _PUBLIC_API},
        ),
        GraphNode(
            node_id="LLR-0001", node_type=NodeType.LLR.value, layer=4,
            title="Parse input rows",
            content="The system shall parse each input row into a record.",
            parent_id="HLR-0001", trace_to=["HLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="LLR-0002", node_type=NodeType.LLR.value, layer=4,
            title="Report parsed records",
            content="The system shall report the count of parsed records.",
            parent_id="HLR-0002", trace_to=["HLR-0002"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="DESIGN-0001", node_type=NodeType.DESIGN.value, layer=3,
            title="Engine class design",
            content="Class Engine: parse(path) -> list[Record]; report() -> Report.",
            parent_id="MODULE-0001", trace_to=["LLR-0001", "LLR-0002"],
            lifecycle=LifecycleState.ACTIVE,
        ),
    ]
    if with_suite:
        nodes.append(
            GraphNode(
                node_id="SUITE-0001", node_type=NodeType.SUITE.value, layer=1,
                title="Test strategy", content=_SUITE_CONTENT,
                parent_id="PROJECT-0001", lifecycle=LifecycleState.ACTIVE,
            )
        )
    if with_cases:
        for i, req in enumerate(["HLR-0001", "HLR-0002"], start=1):
            nodes.append(
                GraphNode(
                    node_id=f"CASE_HLR-000{i}", node_type=NodeType.CASE_HLR.value,
                    layer=2, title=f"Verify behaviour {i}",
                    content=(
                        f"Given a valid input file, when the system runs, then "
                        f"the behaviour of {req} is observed end-to-end with "
                        f"exact expected output asserted."
                    ),
                    parent_id="SUITE-0001", trace_to=[req],
                    lifecycle=LifecycleState.ACTIVE,
                )
            )
        for i, req in enumerate(["LLR-0001", "LLR-0002"], start=1):
            nodes.append(
                GraphNode(
                    node_id=f"CASE_LLR-000{i}", node_type=NodeType.CASE_LLR.value,
                    layer=2, title=f"Verify detail {i}",
                    content=(
                        f"Arrange one crafted row, act by invoking the unit "
                        f"under {req}, assert the exact record produced — a "
                        f"wrong implementation returns a different record."
                    ),
                    parent_id="SUITE-0001", trace_to=[req],
                    lifecycle=LifecycleState.ACTIVE,
                )
            )
    for node in nodes:
        await graph.add_node(node)


def _patch_quality_noops(flow: ForgeFlow) -> tuple[Any, Any, Any]:
    """Mock the LLM-backed quality steps — external I/O, not under test here."""
    return (
        patch.object(flow, "run_qual_check", AsyncMock(return_value=0)),
        patch.object(flow, "run_combined_quality_check", AsyncMock(return_value=[])),
        patch.object(flow, "run_semantic_check", AsyncMock(return_value=0)),
    )


class RecordingOracle:
    """Stub oracle judge factory: records judged item ids, returns all-PASS."""

    def __init__(self) -> None:
        self.calls = 0
        self.judged_ids: list[str] = []

    def __call__(self, llm: Any) -> Any:
        async def check(items: list[Any]) -> list[Any]:
            self.calls += 1
            self.judged_ids.extend(i.node_id for i in items)
            return []

        return check


def _stub_case_trace_checker(llm: Any, graph: Any) -> Any:
    async def check(only_ids: Any) -> int:
        return 0

    return check


class SuiteAuthoringAgent:
    """Per-gap stub for UNSUITED dispatch: writes the SUITE under PROJECT."""

    def __init__(self, graph: ProjectGraph) -> None:
        self.graph = graph
        self.calls = 0
        self.gap_types: list[GapType] = []

    async def __call__(self, flow: Any, agent: Any, gap: Any, **kwargs: Any) -> str:
        self.calls += 1
        self.gap_types.append(gap.type)
        if gap.type is GapType.UNSUITED and await self.graph.node("SUITE-0001") is None:
            await self.graph.add_node(
                GraphNode(
                    node_id="SUITE-0001", node_type=NodeType.SUITE.value, layer=1,
                    title="Test strategy", content=_SUITE_CONTENT,
                    parent_id=gap.node_id, lifecycle=LifecycleState.ACTIVE,
                )
            )
            return "OK: SUITE-0001 created"
        return "OK"


class CaseBatchAgent:
    """Batch stub: writes a CASE for every untested requirement, recording
    each prompt so tests can pin the SUITE content in the static prefix."""

    def __init__(self, graph: ProjectGraph) -> None:
        self.graph = graph
        self.calls = 0
        self.prompts: list[str] = []

    async def __call__(
        self, flow: Any, gap_type: Any, prompt: str, phase: int, **kwargs: Any
    ) -> int:
        self.calls += 1
        self.prompts.append(prompt)
        specs = [
            ("CASE_HLR-0001", NodeType.CASE_HLR, "HLR-0001", "Verify parse behaviour"),
            ("CASE_HLR-0002", NodeType.CASE_HLR, "HLR-0002", "Verify report behaviour"),
            ("CASE_LLR-0001", NodeType.CASE_LLR, "LLR-0001", "Verify row parsing"),
            ("CASE_LLR-0002", NodeType.CASE_LLR, "LLR-0002", "Verify record count"),
        ]
        for node_id, node_type, req, title in specs:
            if await self.graph.node(node_id) is None:
                await self.graph.add_node(
                    GraphNode(
                        node_id=node_id, node_type=node_type.value, layer=2,
                        title=title,
                        content=(
                            f"Given crafted input, when the system runs, then the "
                            f"exact outcome required by {req} is asserted so a "
                            f"wrong implementation fails."
                        ),
                        parent_id="SUITE-0001", trace_to=[req],
                        lifecycle=LifecycleState.ACTIVE,
                    )
                )
        return len(specs)


class NoProgressAgent:
    """Claims success, writes nothing — proves zero-dispatch runs."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, *args: Any, **kwargs: Any) -> str:
        self.calls += 1
        return "OK"


# ── Registry pins ────────────────────────────────────────────────────────────


def test_phase9_registry_is_the_unsuited_dispatch_only() -> None:
    """Registry pin (U9): phase 9 keeps ONLY the per-gap dispatch of its
    single UNSUITED gap — its quality/semantic boundary is phase 10's."""
    assert [s.__name__ for s in get_steps(9)] == ["structural"]


def test_phase10_registry_authors_suite_first_and_gates_on_oracles() -> None:
    """Registry pin (U9): phase 10 opens with the suite_authoring guard and
    closes with the independent oracle_check gate."""
    assert [s.__name__ for s in get_steps(10)] == [
        "suite_authoring",
        "batch_phase10",
        "quality_gaps",
        "combined_quality",
        "semantic",
        "case_trace_coverage",
        "oracle_check",
    ]


def test_suite_quality_boundary_belongs_to_phase10() -> None:
    """SUITE nodes are judged inside phase 10's merged quality boundary."""
    from backend.quality.checks import NODE_TYPE_TO_PHASE, PHASE_TO_NODE_TYPES

    assert NODE_TYPE_TO_PHASE["SUITE"] == 10
    assert 9 not in PHASE_TO_NODE_TYPES
    assert set(PHASE_TO_NODE_TYPES[10]) == {"SUITE", "CASE_HLR", "CASE_LLR"}


# ── Resume proofs ────────────────────────────────────────────────────────────


async def test_fully_authored_graph_passes_9_and_10_with_oracle_only(
    flow: ForgeFlow, graph: ProjectGraph
) -> None:
    """(a) Resume proof: SUITE done + CASEs done → phases 9 and 10 complete
    with ZERO authoring dispatches; the oracle check still runs over every
    CASE (independent validation is never skipped on resume)."""
    await _seed_graph(graph, with_suite=True, with_cases=True)
    open_types = {g.type for g in GapAnalyser().analyse(graph)}
    assert GapType.UNSUITED not in open_types
    assert GapType.UNTESTED_HLR not in open_types

    per_gap = NoProgressAgent()
    batch = CaseBatchAgent(graph)
    oracle = RecordingOracle()
    q1, q2, q3 = _patch_quality_noops(flow)
    with (
        patch("backend.pipeline.dispatch.run_agent_task", new=per_gap),
        patch("backend.pipeline.batch_steps._run_batch_agent", new=batch),
        patch(
            "backend.quality.case_trace_check.create_case_trace_checker",
            new=_stub_case_trace_checker,
        ),
        patch(
            "backend.quality.oracle_check.create_oracle_checker", new=oracle
        ),
        q1, q2, q3,
    ):
        await flow.run_phase(9)
        await flow.run_phase(10)

    assert per_gap.calls == 0, "resumed complete graph must not dispatch agents"
    assert batch.calls == 0, "no untested requirement — batch must not run"
    assert oracle.calls >= 1, "oracle validation must run on resumed CASEs"
    assert set(oracle.judged_ids) == {
        "CASE_HLR-0001", "CASE_HLR-0002", "CASE_LLR-0001", "CASE_LLR-0002",
    }
    for phase in (9, 10):
        row = flow.phase_store.get(phase)
        assert row is not None
        assert row["status"] == "complete"


async def test_graph_missing_suite_completes_9_then_10(
    flow: ForgeFlow, graph: ProjectGraph
) -> None:
    """(b) Resume proof: a graph with no SUITE completes phase 9 through the
    single UNSUITED dispatch, then phase 10 authors the CASEs with the SUITE
    content in the batch prompt's static prefix."""
    await _seed_graph(graph, with_suite=False, with_cases=False)
    assert GapType.UNSUITED in {g.type for g in GapAnalyser().analyse(graph)}

    per_gap = SuiteAuthoringAgent(graph)
    batch = CaseBatchAgent(graph)
    oracle = RecordingOracle()
    q1, q2, q3 = _patch_quality_noops(flow)
    with (
        patch("backend.pipeline.dispatch.run_agent_task", new=per_gap),
        patch("backend.pipeline.batch_steps._run_batch_agent", new=batch),
        patch(
            "backend.quality.case_trace_check.create_case_trace_checker",
            new=_stub_case_trace_checker,
        ),
        patch(
            "backend.quality.oracle_check.create_oracle_checker", new=oracle
        ),
        q1, q2, q3,
    ):
        await flow.run_phase(9)
        assert GapType.UNSUITED in per_gap.gap_types, (
            "phase 9 never dispatched the UNSUITED gap"
        )
        row = flow.phase_store.get(9)
        assert row is not None
        assert row["status"] == "complete"

        await flow.run_phase(10)

    assert batch.calls >= 1, "phase 10 never ran the CASE batch"
    # SUITE content is STRUCTURED input to case authoring — static prefix pin.
    assert all("SUITE-0001" in p for p in batch.prompts)
    assert all(_SUITE_CONTENT in p for p in batch.prompts)
    remaining = {g.type for g in GapAnalyser().analyse(graph)}
    assert GapType.UNTESTED_HLR not in remaining
    assert GapType.UNTESTED_LLR not in remaining
    row = flow.phase_store.get(10)
    assert row is not None
    assert row["status"] == "complete"


async def test_resume_directly_at_phase10_authors_suite_first(
    flow: ForgeFlow, graph: ProjectGraph
) -> None:
    """(c) Resume proof: entering phase 10 with no SUITE, the suite_authoring
    guard resolves UNSUITED before any CASE is authored, so the batch prompt
    is never built without its SUITE prefix."""
    await _seed_graph(graph, with_suite=False, with_cases=False)

    per_gap = SuiteAuthoringAgent(graph)
    batch = CaseBatchAgent(graph)
    oracle = RecordingOracle()
    q1, q2, q3 = _patch_quality_noops(flow)
    with (
        patch("backend.pipeline.dispatch.run_agent_task", new=per_gap),
        patch("backend.pipeline.batch_steps._run_batch_agent", new=batch),
        patch(
            "backend.quality.case_trace_check.create_case_trace_checker",
            new=_stub_case_trace_checker,
        ),
        patch(
            "backend.quality.oracle_check.create_oracle_checker", new=oracle
        ),
        q1, q2, q3,
    ):
        await flow.run_phase(10)

    assert GapType.UNSUITED in per_gap.gap_types, (
        "suite_authoring guard never dispatched the UNSUITED gap"
    )
    suites = [n for n in graph.all_nodes() if n.node_type == "SUITE"]
    assert len(suites) == 1
    assert batch.calls >= 1
    assert all(_SUITE_CONTENT in p for p in batch.prompts)
    row = flow.phase_store.get(10)
    assert row is not None
    assert row["status"] == "complete"
