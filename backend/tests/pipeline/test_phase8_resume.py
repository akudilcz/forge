"""U8 — phase 8 fused into phase 7's implementable-spec authoring pass.

Phase 7's fused batch pass authors, per MODULE, each uncovered HLR's LLR(s)
AND each LLR's DESIGN in the same response (specs/03 Phases 7-8). Phase 8
keeps its number and completion criterion (no ``UNDESIGNED``) but authors
nothing in the normal flow: ``design_consolidation`` merges DESIGN sprawl
and the default verification pipeline dispatches per-gap agents ONLY for
residual undesigned LLRs.

Resume regressions (real ProjectGraph, mocked LLM seams — same pattern as
``test_phase5_resume.py``):
  (a) old-shape graph mid-phase-7 (some HLRs refined, no DESIGNs) completes
      7 under the fused pass and 8 under verification-only;
  (b) old-shape graph mid-phase-8 (LLRs done, DESIGNs missing) completes 8
      via residual per-gap dispatch;
  (c) fully-authored graph passes 8 with zero dispatches.
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

    return ForgeFlow(
        pool=MagicMock(),
        graph=graph,
        config=ForgeConfig(
            project=ProjectConfig(
                name="phase8-resume", forgemd="forge.md", workspace_dir=str(ws)
            )
        ),
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


async def _seed_old_shape_graph(
    graph: ProjectGraph, *, refine_all: bool, design_all: bool
) -> None:
    """Skeleton through phase 6 plus a partially fused-authored tail.

    ``refine_all=False`` leaves HLR-0002 unrefined (mid-phase-7 old shape).
    ``design_all=False`` leaves every LLR undesigned (mid-phase-8 old shape).
    """
    nodes = [
        GraphNode(
            node_id="PROJECT-0001", node_type=NodeType.PROJECT.value, layer=0,
            title="phase8-resume", lifecycle=LifecycleState.ACTIVE,
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
    ]
    if refine_all:
        nodes.append(
            GraphNode(
                node_id="LLR-0002", node_type=NodeType.LLR.value, layer=4,
                title="Report parsed records",
                content="The system shall report the count of parsed records.",
                parent_id="HLR-0002", trace_to=["HLR-0002"],
                lifecycle=LifecycleState.ACTIVE,
            )
        )
    if design_all:
        llr_ids = ["LLR-0001", "LLR-0002"] if refine_all else ["LLR-0001"]
        nodes.append(
            GraphNode(
                node_id="DESIGN-0001", node_type=NodeType.DESIGN.value, layer=3,
                title="Engine class design",
                content="Class Engine: parse(path) -> list[Record]; report() -> Report.",
                parent_id="MODULE-0001", trace_to=llr_ids,
                lifecycle=LifecycleState.ACTIVE,
            )
        )
    for node in nodes:
        await graph.add_node(node)


def _patch_quality_noops(flow: ForgeFlow) -> tuple[Any, Any, Any, Any]:
    """Mock the LLM-backed quality steps — external I/O, not under test here."""
    return (
        patch.object(flow, "run_qual_check", AsyncMock(return_value=0)),
        patch.object(flow, "run_combined_quality_check", AsyncMock(return_value=[])),
        patch.object(flow, "run_semantic_check", AsyncMock(return_value=0)),
        patch.object(flow, "run_design_consolidation", AsyncMock(return_value=0)),
    )


class FusedBatchAgent:
    """Fused-pass stub: writes the missing LLR AND its DESIGN in one response,
    exactly as the U8 prompt instructs a real agent to."""

    def __init__(self, graph: ProjectGraph) -> None:
        self.graph = graph
        self.calls = 0
        self.prompts: list[str] = []

    async def __call__(
        self,
        flow: Any,
        gap_type: Any,
        prompt: str,
        phase: int,
        **kwargs: Any,
    ) -> int:
        self.calls += 1
        self.prompts.append(prompt)
        if await self.graph.node("LLR-0002") is None:
            await self.graph.add_node(
                GraphNode(
                    node_id="LLR-0002", node_type=NodeType.LLR.value, layer=4,
                    title="Report parsed records",
                    content="The system shall report the count of parsed records.",
                    parent_id="HLR-0002", trace_to=["HLR-0002"],
                    lifecycle=LifecycleState.ACTIVE,
                )
            )
        if await self.graph.node("DESIGN-0001") is None:
            await self.graph.add_node(
                GraphNode(
                    node_id="DESIGN-0001", node_type=NodeType.DESIGN.value, layer=3,
                    title="Engine class design",
                    content="Class Engine: parse(path) -> list[Record]; report() -> Report.",
                    parent_id="MODULE-0001", trace_to=["LLR-0001", "LLR-0002"],
                    lifecycle=LifecycleState.ACTIVE,
                )
            )
        return 2


class DesignAuthoringAgent:
    """Per-gap agent stub for residual UNDESIGNED dispatch: creates the
    module's missing DESIGN, tracing to every LLR."""

    def __init__(self, graph: ProjectGraph) -> None:
        self.graph = graph
        self.calls = 0

    async def __call__(self, *args: Any, **kwargs: Any) -> str:
        self.calls += 1
        if await self.graph.node("DESIGN-0001") is None:
            await self.graph.add_node(
                GraphNode(
                    node_id="DESIGN-0001", node_type=NodeType.DESIGN.value, layer=3,
                    title="Engine class design",
                    content="Class Engine: parse(path) -> list[Record]; report() -> Report.",
                    parent_id="MODULE-0001", trace_to=["LLR-0001", "LLR-0002"],
                    lifecycle=LifecycleState.ACTIVE,
                )
            )
        return "OK: DESIGN-0001 created covering LLR-0001 and LLR-0002"


class NoProgressAgent:
    """Claims success, writes nothing — proves zero-dispatch verification."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, *args: Any, **kwargs: Any) -> str:
        self.calls += 1
        return "OK"


def test_phase8_registry_is_verification_only() -> None:
    """Registry pin (U8): phase 8 has NO batch authoring step — it is
    design_consolidation plus the default verification pipeline, with
    ``structural`` as the residual per-gap dispatch route."""
    names = [s.__name__ for s in get_steps(8)]
    assert "batch_phase8" not in names
    assert names == [
        "design_consolidation",
        "structural",
        "quality_gaps",
        "combined_quality",
        "semantic",
    ]


async def test_old_shape_mid_phase7_completes_fused_then_phase8_verifies(
    flow: ForgeFlow, graph: ProjectGraph
) -> None:
    """(a) Resume proof: an old graph interrupted mid-phase-7 (HLR-0001
    refined, HLR-0002 not, zero DESIGNs) completes phase 7 under the fused
    LLR+DESIGN pass, then phase 8 completes as pure verification."""
    await _seed_old_shape_graph(graph, refine_all=False, design_all=False)
    open_before = {(g.type, g.node_id) for g in GapAnalyser().analyse(graph)}
    assert (GapType.UNREFINED_HLR, "HLR-0002") in open_before
    assert (GapType.UNDESIGNED, "LLR-0001") in open_before

    fused = FusedBatchAgent(graph)
    per_gap = NoProgressAgent()
    q1, q2, q3, q4 = _patch_quality_noops(flow)
    with (
        patch("backend.pipeline.batch_steps._run_batch_agent", new=fused),
        patch("backend.pipeline.dispatch.run_agent_task", new=per_gap),
        q1, q2, q3, q4,
    ):
        await flow.run_phase(7)

        # The fused pass authored BOTH artifact levels — no per-gap dispatch.
        assert fused.calls >= 1
        assert per_gap.calls == 0
        remaining = {g.type for g in GapAnalyser().analyse(graph)}
        assert GapType.UNREFINED_HLR not in remaining
        assert GapType.UNDESIGNED not in remaining
        row = flow.phase_store.get(7)
        assert row is not None
        assert row["status"] == "complete"

        # The fused prompt was grounded in the module's CONTRACT record.
        assert any("public_api" in p or "def run(path: str)" in p for p in fused.prompts)

        # Phase 8 is now pure verification: zero dispatches, completes.
        await flow.run_phase(8)

    assert per_gap.calls == 0
    row = flow.phase_store.get(8)
    assert row is not None
    assert row["status"] == "complete"


async def test_old_shape_mid_phase8_completes_via_residual_dispatch(
    flow: ForgeFlow, graph: ProjectGraph
) -> None:
    """(b) Resume proof: an old graph interrupted mid-phase-8 (all LLRs
    authored, DESIGNs missing) completes phase 8 under the new registry via
    the structural step's residual per-gap dispatch."""
    await _seed_old_shape_graph(graph, refine_all=True, design_all=False)
    open_before = {(g.type, g.node_id) for g in GapAnalyser().analyse(graph)}
    assert (GapType.UNDESIGNED, "LLR-0001") in open_before
    assert (GapType.UNDESIGNED, "LLR-0002") in open_before

    agent = DesignAuthoringAgent(graph)
    q1, q2, q3, q4 = _patch_quality_noops(flow)
    with patch("backend.pipeline.dispatch.run_agent_task", new=agent), q1, q2, q3, q4:
        await flow.run_phase(8)

    assert agent.calls >= 1, "residual UNDESIGNED gap was never dispatched"
    remaining = {g.type for g in GapAnalyser().analyse(graph)}
    assert GapType.UNDESIGNED not in remaining
    row = flow.phase_store.get(8)
    assert row is not None
    assert row["status"] == "complete"


async def test_fully_authored_graph_completes_phase8_with_zero_dispatches(
    flow: ForgeFlow, graph: ProjectGraph
) -> None:
    """(c) Normal flow: the fused pass already designed every LLR, so phase 8
    is pure verification — it completes without a single agent dispatch."""
    await _seed_old_shape_graph(graph, refine_all=True, design_all=True)

    agent = NoProgressAgent()
    q1, q2, q3, q4 = _patch_quality_noops(flow)
    with patch("backend.pipeline.dispatch.run_agent_task", new=agent), q1, q2, q3, q4:
        await flow.run_phase(8)

    assert agent.calls == 0, "verification-only phase 8 must not dispatch agents"
    row = flow.phase_store.get(8)
    assert row is not None
    assert row["status"] == "complete"
