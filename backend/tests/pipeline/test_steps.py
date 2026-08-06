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
