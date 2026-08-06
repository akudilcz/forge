"""Tests for pipeline step functions — the phase-2 deterministic parse step.

Behavioural reference: specs/03-build-pipeline.md §Phase 2 — a qualifying
markdown DOCUMENT is split into its PARA tree with zero LLM dispatches; a
non-qualifying document is left for the structural loop's LLM chunking route.
This is a documented primary/exception split, not a fallback.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.analysis.gap_analyser import GapAnalyser
from backend.analysis.gaps import GapType
from backend.graph.engine import ProjectGraph
from backend.graph.models import GraphNode, LifecycleState, NodeType
from backend.pipeline.steps import deterministic_parse

MARKDOWN_SPEC = """\
# Widget Spec

## Requirements

The system shall frobnicate widgets.

## Error Handling

The system shall raise ValueError on bad input.
"""

PROSE_SPEC = (
    "This specification has no markdown structure whatsoever. "
    "It is a single flowing narrative.\n\n"
    "The system shall still work, but only an agent can chunk this."
)


@pytest.fixture
async def graph() -> AsyncIterator[ProjectGraph]:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    g = ProjectGraph(db_path)
    await g.initialise()
    yield g
    os.unlink(db_path)


async def _seed_document(graph: ProjectGraph, content: str) -> GraphNode:
    doc_id = await graph.allocate_node_id("DOCUMENT")
    doc = GraphNode(
        node_id=doc_id, node_type=NodeType.DOCUMENT.value, layer=1,
        title="Spec", content=content,
        properties={"doc_type": "reference", "doc_order": 99, "slug": "forgemd"},
        lifecycle=LifecycleState.DRAFT, created_by="test",
    )
    return await graph.add_node(doc)


def _flow_for(graph: ProjectGraph) -> MagicMock:
    """A flow stub whose gap collection is the real analyser over a real graph."""
    flow = MagicMock()
    flow.graph = graph
    flow._dispatch = AsyncMock()

    def collect(phase: int, skipped: set[str]) -> list[Any]:
        return [
            g for g in GapAnalyser().analyse(graph)
            if g.type == GapType.UNCHUNKED_DOCUMENT
        ]

    flow._collect_phase_gaps = MagicMock(side_effect=collect)
    return flow


# ── Qualifying markdown: deterministic, zero dispatches ──────────────────────


@pytest.mark.asyncio
async def test_qualifying_doc_parsed_without_any_dispatch(graph: ProjectGraph) -> None:
    doc = await _seed_document(graph, MARKDOWN_SPEC)
    flow = _flow_for(graph)

    result = await deterministic_parse(flow, 2)

    assert result["step_name"] == "deterministic_parse"
    assert result["deletions"] == 0
    flow._dispatch.assert_not_awaited()

    paras = [n for n in graph.all_nodes() if n.node_type == NodeType.PARA.value]
    assert paras, "deterministic parse must write the PARA tree"
    para_ids = {p.node_id for p in paras}
    assert all(p.parent_id == doc.node_id or p.parent_id in para_ids for p in paras)

    # UNCHUNKED_DOCUMENT is closed, so the structural loop has nothing to
    # dispatch — the qualifying path costs zero LLM calls.
    gaps = GapAnalyser().analyse(graph)
    assert [g for g in gaps if g.type == GapType.UNCHUNKED_DOCUMENT] == []


@pytest.mark.asyncio
async def test_rerun_on_chunked_document_is_a_noop(graph: ProjectGraph) -> None:
    await _seed_document(graph, MARKDOWN_SPEC)
    flow = _flow_for(graph)
    await deterministic_parse(flow, 2)
    count_first = len([n for n in graph.all_nodes() if n.node_type == NodeType.PARA.value])

    await deterministic_parse(flow, 2)
    count_second = len([n for n in graph.all_nodes() if n.node_type == NodeType.PARA.value])
    assert count_second == count_first, "re-run must not duplicate PARAs"


# ── Non-qualifying prose: gap stays open for the LLM route ───────────────────


@pytest.mark.asyncio
async def test_non_qualifying_doc_left_for_llm_route(graph: ProjectGraph) -> None:
    await _seed_document(graph, PROSE_SPEC)
    flow = _flow_for(graph)

    result = await deterministic_parse(flow, 2)

    assert result["deletions"] == 0
    paras = [n for n in graph.all_nodes() if n.node_type == NodeType.PARA.value]
    assert paras == [], "non-qualifying docs must not be split deterministically"

    # The gap survives, so the subsequent structural step dispatches the
    # Document Specialist agent exactly as before.
    gaps = GapAnalyser().analyse(graph)
    unchunked = [g for g in gaps if g.type == GapType.UNCHUNKED_DOCUMENT]
    assert len(unchunked) == 1


# ── Registry wiring ──────────────────────────────────────────────────────────


def test_phase2_pipeline_runs_deterministic_parse_before_structural() -> None:
    from backend.pipeline.runner import get_steps

    names = [s.__name__ for s in get_steps(2)]
    assert "deterministic_parse" in names
    assert "structural" in names, "LLM chunking route must remain for prose docs"
    assert names.index("deterministic_parse") < names.index("structural")


# ── Phase 10 · suite_authoring guard (U9) ────────────────────────────────────


from types import SimpleNamespace  # noqa: E402
from unittest.mock import patch  # noqa: E402

from backend.analysis.gaps import Gap, GapPriority  # noqa: E402
from backend.pipeline.steps import oracle_check, suite_authoring  # noqa: E402
from backend.quality.oracle_check import OracleItem, oracle_pass_key  # noqa: E402


def _simple_node(node_id: str, node_type: str) -> SimpleNamespace:
    return SimpleNamespace(
        node_id=node_id, node_type=node_type, content="x", parent_id=None,
        trace_to=[], properties={},
    )


def _fake_graph(nodes: list[SimpleNamespace]) -> MagicMock:
    g = MagicMock()
    g.all_nodes.return_value = nodes
    g.node_sync.side_effect = lambda nid: next(
        (n for n in nodes if n.node_id == nid), None
    )
    return g


def _unsuited_gap() -> Gap:
    return Gap(
        type=GapType.UNSUITED, priority=GapPriority.TEST_SUITE,
        node_id="PROJECT-0001", description="no SUITE",
    )


@pytest.mark.asyncio
async def test_suite_authoring_is_a_noop_when_suite_exists() -> None:
    flow = MagicMock()
    flow.graph = _fake_graph([_simple_node("SUITE-0001", "SUITE")])
    flow._dispatch = AsyncMock()

    result = await suite_authoring(flow, 10)

    assert result["step_name"] == "suite_authoring"
    flow._dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_suite_authoring_dispatches_unsuited_when_suite_missing() -> None:
    nodes: list[SimpleNamespace] = [_simple_node("PROJECT-0001", "PROJECT")]
    flow = MagicMock()
    flow.graph = _fake_graph(nodes)
    flow._collect_phase_gaps = MagicMock(return_value=[_unsuited_gap()])

    async def dispatch(gap: Gap, attempt: int = 1) -> str:
        nodes.append(_simple_node("SUITE-0001", "SUITE"))
        return "ok"

    flow._dispatch = AsyncMock(side_effect=dispatch)
    result = await suite_authoring(flow, 10)

    assert result["step_name"] == "suite_authoring"
    flow._dispatch.assert_awaited_once()
    # The guard collects the PHASE 9 gap (UNSUITED belongs to phase 9).
    assert flow._collect_phase_gaps.call_args.args[0] == 9


@pytest.mark.asyncio
async def test_suite_authoring_raises_loudly_when_no_suite_results() -> None:
    """No silent fallback: authoring CASEs without a SUITE parent is never
    an acceptable degraded mode."""
    flow = MagicMock()
    flow.graph = _fake_graph([_simple_node("PROJECT-0001", "PROJECT")])
    flow._collect_phase_gaps = MagicMock(return_value=[_unsuited_gap()])
    flow._dispatch = AsyncMock(return_value="claims ok, writes nothing")

    with pytest.raises(RuntimeError, match="SUITE"):
        await suite_authoring(flow, 10)


# ── Phase 10 · oracle_check step (U9) ────────────────────────────────────────


def _oracle_item(node_id: str) -> OracleItem:
    return OracleItem(
        node_id=node_id,
        case_content=f"content of {node_id}",
        requirement_block="[HLR-0001] The system shall parse.",
        contract_block="(no contract record)",
    )


def _oracle_flow(items: list[OracleItem]) -> MagicMock:
    flow = MagicMock()
    flow.graph = _fake_graph(
        [_simple_node(i.node_id, "CASE_HLR") for i in items]
    )
    flow._oracle_verdict_cache = {}
    flow._dispatch = AsyncMock()
    flow.config.llm.quality_judge_batch_size = 25
    return flow


class _CheckerFactory:
    """Stands in for create_oracle_checker; scripts per-chunk gap results."""

    def __init__(self, gaps_per_call: list[list[Gap]]) -> None:
        self._results = list(gaps_per_call)
        self.built = 0
        self.chunks: list[list[OracleItem]] = []

    def __call__(self, llm: Any) -> Any:
        self.built += 1

        async def check(items: list[OracleItem]) -> list[Gap]:
            self.chunks.append(list(items))
            return self._results.pop(0)

        return check


@pytest.mark.asyncio
async def test_oracle_check_stamps_sticky_pass_and_skips_next_cycle() -> None:
    items = [_oracle_item("CASE_HLR-0001"), _oracle_item("CASE_HLR-0002")]
    flow = _oracle_flow(items)
    factory = _CheckerFactory([[]])

    with (
        patch("backend.quality.oracle_check.collect_oracle_items", return_value=items),
        patch("backend.quality.oracle_check.create_oracle_checker", factory),
        patch("backend.agents.factory.build_llm", return_value=MagicMock()),
    ):
        result = await oracle_check(flow, 10)
        assert result["step_name"] == "oracle_check"
        assert flow._oracle_verdict_cache == {
            oracle_pass_key(items[0]): "PASS",
            oracle_pass_key(items[1]): "PASS",
        }

        # Second cycle: everything cached — the judge is never rebuilt.
        await oracle_check(flow, 10)
    assert factory.built == 1
    flow._dispatch.assert_not_awaited()


@pytest.mark.asyncio
async def test_oracle_check_dispatches_fail_gap_and_never_caches_it() -> None:
    items = [_oracle_item("CASE_HLR-0001"), _oracle_item("CASE_HLR-0002")]
    flow = _oracle_flow(items)
    fail_gap = Gap(
        type=GapType.INCONSISTENT_CONTENT, priority=GapPriority.MAINTENANCE,
        node_id="CASE_HLR-0001", description="wrong oracle",
        context={"oracle_failures": [{"axis": "OUTCOME", "reason": "wrong"}]},
    )
    factory = _CheckerFactory([[fail_gap]])

    with (
        patch("backend.quality.oracle_check.collect_oracle_items", return_value=items),
        patch("backend.quality.oracle_check.create_oracle_checker", factory),
        patch("backend.agents.factory.build_llm", return_value=MagicMock()),
    ):
        await oracle_check(flow, 10)

    flow._dispatch.assert_awaited_once_with(fail_gap)
    assert oracle_pass_key(items[0]) not in flow._oracle_verdict_cache
    assert flow._oracle_verdict_cache[oracle_pass_key(items[1])] == "PASS"


@pytest.mark.asyncio
async def test_oracle_check_chunks_by_quality_judge_batch_size() -> None:
    items = [_oracle_item(f"CASE_HLR-000{i}") for i in range(1, 4)]
    flow = _oracle_flow(items)
    flow.config.llm.quality_judge_batch_size = 2
    factory = _CheckerFactory([[], []])

    with (
        patch("backend.quality.oracle_check.collect_oracle_items", return_value=items),
        patch("backend.quality.oracle_check.create_oracle_checker", factory),
        patch("backend.agents.factory.build_llm", return_value=MagicMock()),
    ):
        await oracle_check(flow, 10)

    assert [len(c) for c in factory.chunks] == [2, 1]


@pytest.mark.asyncio
async def test_oracle_check_unjudged_error_propagates() -> None:
    """Oracle quality GATES completion: an unjudged CASE fails the step
    loudly (UnjudgedQualityError) — unlike dedup, silence never passes."""
    from backend.quality.combined_check import UnjudgedQualityError

    items = [_oracle_item("CASE_HLR-0001")]
    flow = _oracle_flow(items)

    def factory(llm: Any) -> Any:
        async def check(chunk: list[OracleItem]) -> list[Gap]:
            raise UnjudgedQualityError({"CASE_HLR-0001": {"OUTCOME"}})

        return check

    with (
        patch("backend.quality.oracle_check.collect_oracle_items", return_value=items),
        patch("backend.quality.oracle_check.create_oracle_checker", factory),
        patch("backend.agents.factory.build_llm", return_value=MagicMock()),
        pytest.raises(UnjudgedQualityError),
    ):
        await oracle_check(flow, 10)


@pytest.mark.asyncio
async def test_oracle_check_with_no_cases_makes_no_llm_calls() -> None:
    flow = _oracle_flow([])
    factory = _CheckerFactory([])

    with (
        patch("backend.quality.oracle_check.collect_oracle_items", return_value=[]),
        patch("backend.quality.oracle_check.create_oracle_checker", factory),
    ):
        result = await oracle_check(flow, 10)

    assert result["deletions"] == 0
    assert factory.built == 0
