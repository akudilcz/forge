"""Shared machinery for the adversarial robustness harness.

Scripted agents occupy the only LLM seam — ``backend.pipeline.dispatch.
run_agent_task`` — while the four direct-LLM checker factories are patched
with clean verdicts, so the real pipeline (structural loop, quality loop,
audit, real ``ProjectGraph`` on SQLite) runs fully offline.

Pattern copied from ``backend/tests/pipeline/test_phase9_suite.py``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

from backend.analysis.gaps import Gap
from backend.graph.engine import ProjectGraph
from backend.graph.models import GraphNode, LifecycleState, NodeType
from backend.pipeline.flow import ForgeFlow

SPEC_TEXT = (
    "# Spec\n\n"
    "The system shall do the first thing.\n\n"
    "The system shall do the second thing.\n\n"
    "The system shall do the third thing.\n"
)


def write_spec(workspace: Path) -> None:
    """Create the workspace layout with a three-requirement forge.md."""
    (workspace / "src").mkdir(parents=True)
    (workspace / "tests").mkdir(parents=True)
    (workspace / "forge.md").write_text(SPEC_TEXT, encoding="utf-8")


# ── Scripted agents ──────────────────────────────────────────────────────────


class ScriptedAgentBase:
    """Base for scripted stand-ins on both LLM seams.

    ``__call__`` covers the per-gap dispatch seam
    (``backend.pipeline.dispatch.run_agent_task``); ``astream_events`` covers
    the batch seam (phases 3/5/7/8/10 invoke the pool agent directly). The
    batch variant resolves every currently-open UNCOVERED_PARA gap, mirroring
    ``test_phase_contracts_llm.py`` — install the agent as
    ``flow.pool.get_agent_for_gap.return_value`` for batch phases.
    """

    def __init__(self, graph: ProjectGraph) -> None:
        self.graph = graph
        self.seen: list[Gap] = []

    async def __call__(self, flow: Any, agent: Any, gap: Gap, **kwargs: Any) -> str:
        self.seen.append(gap)
        return await self.resolve(gap)

    async def astream_events(self, _payload: Any, **_kwargs: Any) -> Any:
        from backend.analysis.gap_analyser import GapAnalyser
        from backend.analysis.gaps import GapType

        for gap in GapAnalyser().analyse(self.graph):
            if gap.type is GapType.UNCOVERED_PARA:
                self.seen.append(gap)
                await self.resolve(gap)
        return
        yield  # pragma: no cover — makes this an async generator

    async def resolve(self, gap: Gap) -> str:
        raise NotImplementedError

    async def add(
        self,
        node_type: NodeType,
        title: str,
        content: str,
        parent_id: str | None,
        trace_to: list[str],
        properties: dict[str, Any],
    ) -> GraphNode:
        node_id = await self.graph.allocate_node_id(node_type.value)
        node = GraphNode(
            node_id=node_id,
            node_type=node_type.value,
            title=title,
            content=content,
            parent_id=parent_id,
            trace_to=trace_to,
            properties=properties,
            lifecycle=LifecycleState.ACTIVE,
            created_by="scripted-agent",
        )
        await self.graph.add_node(node)
        return node


class WellBehavedAgent(ScriptedAgentBase):
    """Resolves phase-2 (PARA) and phase-3 (HLR) gaps correctly."""

    async def resolve(self, gap: Gap) -> str:
        handler = getattr(self, f"resolve_{gap.type.value.lower()}", None)
        if handler is None:
            return f"no scripted resolution for {gap.type.value}"
        result: str = await handler(gap)
        return result

    async def resolve_unchunked_document(self, gap: Gap) -> str:
        for i in range(3):
            await self.add(
                NodeType.PARA,
                f"Requirement paragraph {i + 1}",
                f"The system shall perform documented behaviour number {i + 1} "
                f"whenever the corresponding precondition holds.",
                parent_id=gap.node_id,
                trace_to=[],
                properties={"para_type": "requirement"},
            )
        return "created 3 PARA nodes"

    async def resolve_uncovered_para(self, gap: Gap) -> str:
        await self.add(
            NodeType.HLR,
            f"HLR for {gap.node_id}",
            "The system shall satisfy the behaviour described by "
            f"{gap.node_id} under all documented preconditions.",
            parent_id=gap.node_id,
            trace_to=[gap.node_id],
            properties={},
        )
        return f"created HLR for {gap.node_id}"


# ── Seam patching ────────────────────────────────────────────────────────────


@contextmanager
def scripted_seams(agent: Any) -> Iterator[Any]:
    """Close every LLM seam: per-gap dispatch plus the direct-LLM checkers.

    Checker failures propagate instead of failing open, so the fake must
    stand in for those checkers explicitly with a clean verdict from each.
    """

    async def _not_a_duplicate(node_id: str, node_content: str, peers_text: str) -> bool:
        return False

    async def _clean_combined_verdict(items: Any) -> list[Any]:
        return []

    async def _all_traces_valid(only_ids: Any) -> int:
        return 0

    async def _no_consolidation(**kwargs: Any) -> int:
        return 0

    with (
        patch("backend.pipeline.dispatch.run_agent_task", new=agent),
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


# ── Small helpers ────────────────────────────────────────────────────────────


def phase_status(flow: ForgeFlow, phase: int) -> str:
    record = flow.phase_store.get(phase)
    assert record is not None, f"phase {phase} missing from phase store"
    status: str = record["status"]
    return status


def nodes_of(graph: ProjectGraph, node_type: NodeType) -> list[GraphNode]:
    return [n for n in graph.all_nodes() if n.node_type == node_type.value]


def assert_no_orphans(graph: ProjectGraph) -> None:
    """Every node's parent_id must resolve to a live node — no stranded subtrees."""
    all_ids = {n.node_id for n in graph.all_nodes()}
    orphans = [
        (n.node_id, n.parent_id)
        for n in graph.all_nodes()
        if n.parent_id and n.parent_id not in all_ids
    ]
    assert orphans == [], f"orphaned nodes after hostile run: {orphans}"


def assert_unique_node_ids(graph: ProjectGraph) -> None:
    ids = [n.node_id for n in graph.all_nodes()]
    assert len(ids) == len(set(ids)), f"node ID collision: {sorted(ids)}"


async def run_phases_0_and_1(flow: ForgeFlow) -> None:
    """Deterministic setup: PROJECT node + DOCUMENT ingest, no LLM involved."""
    await flow.run_phase(0)
    await flow.run_phase(1)
    assert phase_status(flow, 0) == "complete"
    assert phase_status(flow, 1) == "complete"
