"""Phase 1 (Ingest Document) in isolation, on a fully wired ForgeFlow.

Phase 1 is deterministic — no agent, no LLM call — so these tests are free.
They run the production wiring (``ForgeBuilder``) rather than the mocked flow
of ``backend/tests/test_phase_contracts.py``.

Design reference: specs/03-build-pipeline.md
"""

from __future__ import annotations

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
Unicode survives ingestion verbatim: café — ✓

## Conversion Requirements

The system shall convert Celsius to Fahrenheit using F = C * 9 / 5 + 32.
The system shall convert Fahrenheit to Celsius using C = (F - 32) * 5 / 9.
The system shall reject temperatures below absolute zero (-273.15 C).
"""


def _make_config(integration_config: ForgeConfig, workspace: Path) -> ForgeConfig:
    config = integration_config.model_copy(deep=True)
    config.project.workspace_dir = str(workspace)
    config.project.name = "phase01-integration"
    return config


@pytest.fixture
async def flow(integration_config: ForgeConfig, tmp_path: Path) -> ForgeFlow:
    """Workspace containing a tiny forge.md, production-wired flow."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "forge.md").write_text(TINY_SPEC, encoding="utf-8")
    config = _make_config(integration_config, workspace)
    builder = ForgeBuilder(config=config, workspace=workspace, db_path=tmp_path / "forge.db")
    return await builder.build()


@pytest.fixture
async def flow_without_spec(integration_config: ForgeConfig, tmp_path: Path) -> ForgeFlow:
    """Same wiring, but the workspace deliberately has no forge.md."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config = _make_config(integration_config, workspace)
    builder = ForgeBuilder(config=config, workspace=workspace, db_path=tmp_path / "forge.db")
    return await builder.build()


async def _documents(flow: ForgeFlow) -> list[GraphNode]:
    nodes: list[GraphNode] = await flow.graph.nodes_by_type(NodeType.DOCUMENT.value)
    return nodes


def _phase_status(flow: ForgeFlow, phase: int) -> str:
    row = flow.phase_store.get(phase)
    assert row is not None, f"phase {phase} missing from phase store"
    return str(row["status"])


# ── Happy path ───────────────────────────────────────────────────────────────


async def test_creates_document_node_with_verbatim_content(flow: ForgeFlow) -> None:
    """One DOCUMENT node under PROJECT, carrying forge.md byte-for-byte."""
    await flow.run_phase(0)
    await flow.run_phase(1)

    docs = await _documents(flow)
    assert len(docs) == 1, f"expected exactly 1 DOCUMENT node, got {len(docs)}"
    doc = docs[0]

    project = (await flow.graph.nodes_by_type(NodeType.PROJECT.value))[0]
    assert doc.parent_id == project.node_id, "DOCUMENT must hang off PROJECT"
    assert doc.layer == 1
    assert doc.content == TINY_SPEC, "forge.md must be stored verbatim, not reflowed"
    assert doc.trace_to == [], "DOCUMENT never traces to anything"
    assert doc.properties["slug"] == "forgemd", "slug must be persisted for lookup"
    assert _phase_status(flow, 1) == "complete"


# ── Robustness: missing forge.md ─────────────────────────────────────────────


async def test_missing_forgemd_leaves_phase_pending_and_creates_nothing(
    flow_without_spec: ForgeFlow,
) -> None:
    """No forge.md is a loud non-event: phase stays pending, graph untouched."""
    flow = flow_without_spec
    await flow.run_phase(0)

    await flow.run_phase(1)

    assert _phase_status(flow, 1) == "pending", (
        "phase 1 must not report complete when forge.md is absent"
    )
    assert await _documents(flow) == [], "no DOCUMENT may be created without a source file"
    # Only the PROJECT node from phase 0 exists.
    assert [n.node_type for n in flow.graph.all_nodes()] == [NodeType.PROJECT.value]


# ── Robustness: idempotent re-ingest ─────────────────────────────────────────


async def test_reingest_does_not_duplicate_document(flow: ForgeFlow) -> None:
    await flow.run_phase(0)
    await flow.run_phase(1)
    first_id = (await _documents(flow))[0].node_id

    await flow.run_phase(1)

    docs = await _documents(flow)
    assert len(docs) == 1, "re-ingesting must update the DOCUMENT, not add another"
    assert docs[0].node_id == first_id
    assert docs[0].content == TINY_SPEC


# ── Gap analyser: no phase-relevant structural gaps ──────────────────────────


async def test_gap_analyser_reports_only_downstream_gaps(flow: ForgeFlow) -> None:
    """Phase 1 has no gap type; the only gaps after it are downstream inputs.

    UNCHUNKED_DOCUMENT is *expected* here — it is precisely the gap phase 2
    consumes. UNARCHITECTED / UNSUITED belong to phases 4 and 9. Anything else
    means ingestion damaged the graph.
    """
    await flow.run_phase(0)
    await flow.run_phase(1)

    gaps = GapAnalyser().analyse(flow.graph)
    downstream_only = {GapType.UNCHUNKED_DOCUMENT, GapType.UNARCHITECTED, GapType.UNSUITED}
    unexpected = [g for g in gaps if g.type not in downstream_only]
    assert unexpected == [], (
        f"phase 1 left unexpected gaps: {[(g.type, g.node_id) for g in unexpected]}"
    )

    doc_id = (await _documents(flow))[0].node_id
    unchunked = [g for g in gaps if g.type == GapType.UNCHUNKED_DOCUMENT]
    assert [g.node_id for g in unchunked] == [doc_id], (
        "exactly one UNCHUNKED_DOCUMENT gap must target the fresh DOCUMENT "
        "(it is the input that drives phase 2)"
    )
