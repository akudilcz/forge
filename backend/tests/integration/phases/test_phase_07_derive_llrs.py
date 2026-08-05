"""Per-phase integration test for Phase 7 — Derive LLRs (real LLM).

Phase 7 batch-derives Low-Level Requirements from HLRs within their MODULE and
CONTRACT context (design/17_phase_07_derive_llrs.md). The precondition graph —
PROJECT → DOCUMENT → PARAs → HLRs → ARCHITECTURE → MODULE → CONTRACT, exactly
the shape phases 0-6 produce — is seeded deterministically, and ONLY phase 7
runs against the real LLM.

The graph is kept tiny (2 HLRs, 1 MODULE with 1 CONTRACT).  Covered:

* Happy path — every HLR gains at least one LLR child, the UNREFINED_HLR gap
  closes, and the phase audits complete.
* Idempotency — re-running phase 7 with a real LLM leaves every HLR covered
  and produces no duplicate LLRs.
* Empty precondition — with no HLRs the phase completes cleanly without
  creating nodes or making any LLM call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import backend.agents.factory as factory
from backend.analysis.gap_analyser import GapAnalyser
from backend.analysis.gaps import GapType
from backend.config.models import ForgeConfig
from backend.crew.flow import ForgeFlow
from backend.forge_builder import ForgeBuilder
from backend.graph.models import GraphNode, LifecycleState, NodeType

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.timeout(1200)]

_LLM_CALL_BUDGET = 300


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _llm_budget() -> Any:
    """Cap runaway loops; a converging phase 7 run needs far fewer calls."""
    factory.llm_call_count = 0
    factory.llm_call_limit = _LLM_CALL_BUDGET
    yield
    factory.llm_call_limit = None


@pytest.fixture
async def flow(integration_config: ForgeConfig, tmp_path: Path) -> ForgeFlow:
    """A fully wired ForgeFlow (real agent pool, real graph) in a temp dir."""
    config = integration_config.model_copy(deep=True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    config.project.workspace_dir = str(workspace)
    config.project.name = "phase07-it"
    builder = ForgeBuilder(config=config, workspace=workspace, db_path=tmp_path / "forge.db")
    return await builder.build()


# ── Deterministic graph seeding (mirrors phases 0-6 output) ──────────────────


async def _add(
    graph: Any,
    node_type: NodeType,
    title: str,
    content: str,
    parent_id: str | None,
    trace_to: list[str],
    properties: dict[str, Any],
) -> GraphNode:
    node_id = await graph.allocate_node_id(node_type.value)
    node = GraphNode(
        node_id=node_id,
        node_type=node_type.value,
        title=title,
        content=content,
        parent_id=parent_id,
        trace_to=trace_to,
        properties=properties,
        lifecycle=LifecycleState.ACTIVE,
        created_by="integration-seed",
    )
    await graph.add_node(node)
    return node


async def _seed_through_phase6(graph: Any) -> dict[str, GraphNode]:
    """PROJECT → DOCUMENT → 2 PARAs → 2 HLRs → ARCHITECTURE → MODULE → CONTRACT."""
    project = await _add(graph, NodeType.PROJECT, "phase07-it", "", None, [], {})
    document = await _add(
        graph, NodeType.DOCUMENT, "Temperature converter spec",
        "# Temperature Converter\n\n"
        "Converts Celsius temperatures to Fahrenheit and validates raw input.\n",
        project.node_id, [], {},
    )
    para_convert = await _add(
        graph, NodeType.PARA, "Conversion Requirement",
        "The converter accepts a temperature in degrees Celsius and returns the "
        "equivalent temperature in degrees Fahrenheit using the standard formula.",
        document.node_id, [], {"sub_type": "requirement"},
    )
    para_validate = await _add(
        graph, NodeType.PARA, "Validation Requirement",
        "The converter rejects non-numeric temperature input with a descriptive "
        "validation error so callers can correct their request.",
        document.node_id, [], {"sub_type": "requirement"},
    )
    hlr_convert = await _add(
        graph, NodeType.HLR, "Celsius To Fahrenheit Conversion",
        "The system shall convert a Celsius temperature value to its Fahrenheit "
        "equivalent using the formula F = C multiplied by 9/5 plus 32.",
        para_convert.node_id, [para_convert.node_id], {},
    )
    hlr_validate = await _add(
        graph, NodeType.HLR, "Numeric Input Validation",
        "The system shall reject non-numeric temperature input by raising a "
        "validation error that names the offending value.",
        para_validate.node_id, [para_validate.node_id], {},
    )
    architecture = await _add(
        graph, NodeType.ARCHITECTURE, "Single Module Architecture",
        "The system is a single cohesive converter module that owns both the "
        "numeric Celsius-to-Fahrenheit transformation and the parsing and "
        "validation of raw caller input. It exposes two pure functions and "
        "holds no state between calls.",
        project.node_id, [], {},
    )
    module = await _add(
        graph, NodeType.MODULE, "Converter Module",
        "Owns temperature conversion and input validation. Exposes a pure "
        "conversion function and a validating parser; raises a descriptive "
        "error for non-numeric input.",
        architecture.node_id, [hlr_convert.node_id, hlr_validate.node_id], {},
    )
    contract = await _add(
        graph, NodeType.CONTRACT, "Converter Public Interface",
        "Public interface:\n"
        "- celsius_to_fahrenheit(celsius: float) -> float — returns "
        "celsius * 9 / 5 + 32. Precondition: celsius is a finite float. "
        "Postcondition: result is finite.\n"
        "- parse_temperature(raw: str) -> float — parses raw into a finite float. "
        "Raises ValueError naming the offending value when raw is not numeric.\n"
        "Invariant: no function mutates shared state; all functions are pure.",
        module.node_id, [], {},
    )
    return {
        "project": project,
        "document": document,
        "hlr_convert": hlr_convert,
        "hlr_validate": hlr_validate,
        "architecture": architecture,
        "module": module,
        "contract": contract,
    }


async def _seed_minimal_no_hlrs(graph: Any) -> None:
    """A graph complete through phase 6 that simply contains no HLRs.

    A heading-only PARA keeps the DOCUMENT chunked without demanding HLR
    coverage; with no HLRs there are no MODULEs or CONTRACTs to require either.
    """
    project = await _add(graph, NodeType.PROJECT, "phase07-it", "", None, [], {})
    document = await _add(
        graph, NodeType.DOCUMENT, "Empty spec",
        "# Empty Spec\n", project.node_id, [], {},
    )
    await _add(
        graph, NodeType.PARA, "Introduction Section",
        "## Introduction", document.node_id, [], {"sub_type": "heading"},
    )
    await _add(
        graph, NodeType.ARCHITECTURE, "Placeholder System Architecture",
        "The system currently defines no modules because the specification "
        "contains no requirement-bearing paragraphs to modularise yet.",
        project.node_id, [], {},
    )


# ── Assertion helpers ────────────────────────────────────────────────────────


def _gap_types(graph: Any) -> set[GapType]:
    return {g.type for g in GapAnalyser().analyse(graph)}


def _nodes(graph: Any, node_type: NodeType) -> list[GraphNode]:
    return [n for n in graph.all_nodes() if n.node_type == node_type.value]


def _llrs_of(graph: Any, hlr_id: str) -> list[GraphNode]:
    return [
        c for c in graph.children_sync(hlr_id)
        if c.node_type == NodeType.LLR.value
    ]


def _status(flow: ForgeFlow, phase: int) -> str:
    row = flow.phase_store.get(phase)
    assert row is not None, f"phase {phase} missing from the phase store"
    return str(row["status"])


def _assert_no_duplicate_llrs(graph: Any) -> None:
    """No two LLRs under the same HLR may carry identical normalised content."""
    seen: dict[tuple[str | None, str], str] = {}
    for llr in _nodes(graph, NodeType.LLR):
        key = (llr.parent_id, (llr.content or "").strip().lower())
        if key in seen:
            pytest.fail(
                f"duplicate LLRs under {llr.parent_id}: "
                f"{seen[key]} and {llr.node_id} share identical content"
            )
        seen[key] = llr.node_id


# ── Tests ────────────────────────────────────────────────────────────────────


class TestPhase07DeriveLLRs:
    async def test_every_hlr_gets_llr_children(self, flow: ForgeFlow) -> None:
        """Happy path: phase 7 alone, on a seeded graph, refines every HLR."""
        seeded = await _seed_through_phase6(flow.graph)
        assert GapType.UNREFINED_HLR in _gap_types(flow.graph), (
            "seeding is broken — phase 7 would have nothing to do"
        )

        await flow.run_phase(7)

        for key in ("hlr_convert", "hlr_validate"):
            hlr = seeded[key]
            llrs = _llrs_of(flow.graph, hlr.node_id)
            assert llrs, f"HLR {hlr.node_id} ({hlr.title!r}) got no LLR children"
            for llr in llrs:
                assert llr.content.strip(), f"LLR {llr.node_id} has empty content"
                assert llr.layer == 2

        assert GapType.UNREFINED_HLR not in _gap_types(flow.graph), (
            "UNREFINED_HLR gaps still outstanding after phase 7"
        )
        assert _status(flow, 7) == "complete"

    async def test_rerun_keeps_coverage_and_creates_no_duplicates(
        self, flow: ForgeFlow
    ) -> None:
        """Re-running phase 7 with a real LLM must not duplicate LLRs."""
        seeded = await _seed_through_phase6(flow.graph)
        await flow.run_phase(7)
        count_after_first = len(_nodes(flow.graph, NodeType.LLR))
        assert count_after_first >= 2, (
            f"first run derived only {count_after_first} LLR(s) for 2 HLRs"
        )

        await flow.run_phase(7)

        for key in ("hlr_convert", "hlr_validate"):
            hlr = seeded[key]
            assert _llrs_of(flow.graph, hlr.node_id), (
                f"re-run lost LLR coverage for HLR {hlr.node_id}"
            )
        _assert_no_duplicate_llrs(flow.graph)
        assert GapType.UNREFINED_HLR not in _gap_types(flow.graph)
        assert _status(flow, 7) == "complete"

    async def test_no_hlrs_completes_cleanly_without_llm_calls(
        self, flow: ForgeFlow
    ) -> None:
        """With nothing to refine, phase 7 must not dispatch anything."""
        await _seed_minimal_no_hlrs(flow.graph)
        ids_before = {n.node_id for n in flow.graph.all_nodes()}
        calls_before = factory.llm_call_count

        await flow.run_phase(7)

        ids_after = {n.node_id for n in flow.graph.all_nodes()}
        assert ids_after == ids_before, (
            f"phase 7 created junk nodes with no HLRs present: "
            f"{sorted(ids_after - ids_before)}"
        )
        assert _nodes(flow.graph, NodeType.LLR) == []
        assert factory.llm_call_count == calls_before, (
            f"phase 7 made {factory.llm_call_count - calls_before} LLM call(s) "
            "despite having nothing to do"
        )
        assert _status(flow, 7) == "complete"
