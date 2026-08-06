"""Phase 2 (Parse Document) in isolation — the first real-LLM phase.

Phases 0 and 1 are deterministic and free, so the fixture runs them inline to
seed the graph, then dispatches the real Document Specialist agent once for
phase 2. A tiny two-section spec keeps the paid calls fast and cheap. The
expensive run is cached at module scope so every assertion below shares one
build, mirroring the ``built`` fixture in ``test_algorithm_builds.py``.

Design reference: specs/03-build-pipeline.md
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from backend.analysis.gap_analyser import GapAnalyser
from backend.analysis.gaps import GapType
from backend.config.models import ForgeConfig
from backend.core.forge_builder import ForgeBuilder
from backend.graph.models import GraphNode, NodeType
from backend.pipeline.flow import ForgeFlow

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.timeout(900)]

TINY_SPEC = """\
# Temperature Converter

## Overview

A tiny library converting temperatures between Celsius and Fahrenheit.
It exposes two pure functions and validates its inputs strictly.

## Conversion Requirements

The system shall convert Celsius to Fahrenheit using F = C * 9 / 5 + 32.
The system shall convert Fahrenheit to Celsius using C = (F - 32) * 5 / 9.
The system shall reject temperatures below absolute zero (-273.15 C).

## Error Handling

The system shall raise a ValueError for non-numeric input.
The system shall include the offending value in every error message.
"""

# Section headings the PARA tree must cover (lowercased for matching).
SPEC_HEADINGS = ("overview", "conversion requirements", "error handling")

# Per-phase agent budget: parsing a 20-line document must not spiral.
MAX_LLM_CALLS = 60

VALID_PARA_TYPES = {"functional", "rationale", "constraint", "non_functional", "heading"}


@dataclasses.dataclass
class Parsed:
    """The shared result of one real phase-0-1-2 run."""

    flow: ForgeFlow
    workspace: Path
    document_id: str
    llm_calls: int


_cache: dict[str, Parsed] = {}


@pytest.fixture
async def parsed(
    integration_config: ForgeConfig,
    tmp_path_factory: pytest.TempPathFactory,
) -> Parsed:
    """Run phases 0-2 once (real Document Specialist), then serve the cache."""
    if "run" in _cache:
        return _cache["run"]

    import backend.agents.factory as factory

    root = tmp_path_factory.mktemp("phase02")
    workspace = root / "workspace"
    workspace.mkdir()
    (workspace / "forge.md").write_text(TINY_SPEC, encoding="utf-8")

    config = integration_config.model_copy(deep=True)
    config.project.workspace_dir = str(workspace)
    config.project.name = "phase02-integration"

    builder = ForgeBuilder(config=config, workspace=workspace, db_path=root / "forge.db")
    flow = await builder.build()

    await flow.run_phase(0)
    await flow.run_phase(1)

    factory.llm_call_count = 0
    factory.llm_call_limit = MAX_LLM_CALLS
    try:
        await flow.run_phase(2)
    finally:
        llm_calls = factory.llm_call_count
        factory.llm_call_limit = None

    document_id = (await flow.graph.nodes_by_type(NodeType.DOCUMENT.value))[0].node_id
    _cache["run"] = Parsed(
        flow=flow, workspace=workspace, document_id=document_id, llm_calls=llm_calls
    )
    return _cache["run"]


async def _paras(flow: ForgeFlow) -> list[GraphNode]:
    nodes: list[GraphNode] = await flow.graph.nodes_by_type(NodeType.PARA.value)
    return nodes


# ── Happy path: PARA tree shape ──────────────────────────────────────────────


async def test_phase_completes_and_creates_para_tree(parsed: Parsed) -> None:
    """The Document Specialist must chunk the DOCUMENT into a valid PARA tree."""
    row = parsed.flow.phase_store.get(2)
    assert row is not None
    assert row["status"] == "complete", f"phase 2 finished with status {row['status']!r}"

    paras = await _paras(parsed.flow)
    assert len(paras) >= 2, (
        f"a three-section document must yield at least 2 PARAs, got {len(paras)}"
    )

    para_ids = {p.node_id for p in paras}
    for para in paras:
        assert para.layer == 1, f"{para.node_id} is PARA so layer must be 1, got {para.layer}"
        assert para.trace_to == [], f"PARA {para.node_id} must not trace to anything"
        assert para.parent_id is not None, f"PARA {para.node_id} has no parent"
        assert para.parent_id == parsed.document_id or para.parent_id in para_ids, (
            f"PARA {para.node_id} parents {para.parent_id!r}, "
            f"which is neither the DOCUMENT nor another PARA"
        )
        if para.para_type:
            assert para.para_type in VALID_PARA_TYPES, (
                f"PARA {para.node_id} has unknown para_type {para.para_type!r}"
            )
        if (para.para_type or "") != "heading":
            assert (para.content or "").strip(), (
                f"non-heading PARA {para.node_id} has empty content"
            )


async def test_para_tree_covers_all_document_headings(parsed: Parsed) -> None:
    """Every section of the source document must be represented somewhere."""
    paras = await _paras(parsed.flow)
    corpus = "\n".join(f"{p.title}\n{p.content}" for p in paras).lower()

    missing = [h for h in SPEC_HEADINGS if h not in corpus]
    assert missing == [], (
        f"document sections not represented in any PARA title/content: {missing}"
    )
    # Spot-check that section *bodies* were carried over, not just headings.
    for body_fragment in ("9 / 5 + 32", "absolute zero", "valueerror"):
        assert body_fragment in corpus, (
            f"body text {body_fragment!r} from the spec never reached a PARA"
        )


# ── Gap analyser: UNCHUNKED_DOCUMENT closed, tree structurally sound ─────────


async def test_unchunked_document_gap_is_closed(parsed: Parsed) -> None:
    gaps = GapAnalyser().analyse(parsed.flow.graph)
    unchunked = [g for g in gaps if g.type == GapType.UNCHUNKED_DOCUMENT]
    assert unchunked == [], (
        f"phase 2 completed but UNCHUNKED_DOCUMENT persists: "
        f"{[g.node_id for g in unchunked]}"
    )


async def test_no_structural_gaps_on_para_nodes(parsed: Parsed) -> None:
    """The stabilised PARA tree may not carry orphan or empty-content damage.

    DUPLICATE_NODE is deliberately not asserted here: the phase audit for
    phase 2 only requires UNCHUNKED_DOCUMENT to be absent, and the quality
    pipeline stabilises on "no deletions", so an agent that declines to delete
    a duplicate can leave one behind on a completed phase (observed live).
    The re-run test below bounds duplicates instead.
    """
    gaps = GapAnalyser().analyse(parsed.flow.graph)
    para_ids = {p.node_id for p in await _paras(parsed.flow)}
    broken_types = {GapType.ORPHAN_NODE, GapType.EMPTY_CONTENT}
    broken = [g for g in gaps if g.node_id in para_ids and g.type in broken_types]
    assert broken == [], (
        f"PARA tree left structural gaps: {[(g.type, g.node_id) for g in broken]}"
    )


# ── Robustness: idempotent re-run with a real LLM ────────────────────────────


async def test_rerun_does_not_duplicate_paras(parsed: Parsed) -> None:
    """Re-running phase 2 on an already-chunked DOCUMENT must not re-chunk it.

    The structural step sees no UNCHUNKED_DOCUMENT gap, so no parsing agent is
    dispatched; quality/semantic steps may run but must not inflate the tree.
    """
    import backend.agents.factory as factory

    before = await _paras(parsed.flow)
    assert before, "precondition: first run produced PARAs"
    dupes_before = _duplicate_sibling_count(before)

    factory.llm_call_limit = MAX_LLM_CALLS
    try:
        await parsed.flow.run_phase(2)
    finally:
        factory.llm_call_limit = None

    after = await _paras(parsed.flow)
    assert len(after) <= len(before), (
        f"re-run grew the PARA tree from {len(before)} to {len(after)} nodes"
    )
    dupes_after = _duplicate_sibling_count(after)
    assert dupes_after <= dupes_before, (
        f"re-run introduced content-identical sibling PARAs "
        f"({dupes_before} before, {dupes_after} after)"
    )

    gaps = GapAnalyser().analyse(parsed.flow.graph)
    assert [g for g in gaps if g.type == GapType.UNCHUNKED_DOCUMENT] == []


def _duplicate_sibling_count(paras: list[GraphNode]) -> int:
    """Number of PARAs whose (parent, normalised content) repeats a sibling's."""
    seen: set[tuple[str | None, str]] = set()
    duplicates = 0
    for para in paras:
        key = (para.parent_id, (para.content or "").strip().lower())
        if not key[1]:
            continue
        if key in seen:
            duplicates += 1
        seen.add(key)
    return duplicates


# ── Telemetry guard ──────────────────────────────────────────────────────────


async def test_parse_stayed_within_llm_budget(parsed: Parsed) -> None:
    """Chunking 20 lines must not cost dozens of calls — that is a regression."""
    assert 0 < parsed.llm_calls < MAX_LLM_CALLS, (
        f"phase 2 made {parsed.llm_calls} LLM calls (budget {MAX_LLM_CALLS})"
    )
