"""Per-phase integration test for Phase 8 — Create Designs (real LLM).

Phase 8 batch-authors DESIGN nodes per MODULE so that every LLR is traced-to by
a DESIGN, then consolidates DESIGN sprawl (design/18_phase_08_create_designs.md).
The precondition graph — PROJECT → DOCUMENT → PARAs → HLRs → ARCHITECTURE →
MODULE → CONTRACT plus LLR children under each HLR, exactly the shape phases
0-7 produce — is seeded deterministically, and ONLY phase 8 runs against the
real LLM.

The graph is kept tiny (2 HLRs, 2 LLRs, 1 MODULE).  Covered:

* Happy path — every LLR ends up covered by a DESIGN under its MODULE, the
  UNDESIGNED gap closes, and the phase audits complete.
* Idempotency — re-running phase 8 with a real LLM keeps every LLR covered
  and produces no duplicate DESIGNs.
* Empty precondition — with no LLRs the phase completes cleanly without
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
from backend.core.forge_builder import ForgeBuilder
from backend.graph.models import GraphNode, LifecycleState, NodeType
from backend.pipeline.flow import ForgeFlow

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.timeout(1200)]

_LLM_CALL_BUDGET = 300


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _llm_budget() -> Any:
    """Cap runaway loops; a converging phase 8 run needs far fewer calls."""
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
    config.project.name = "phase08-it"
    builder = ForgeBuilder(config=config, workspace=workspace, db_path=tmp_path / "forge.db")
    return await builder.build()


# ── Deterministic graph seeding (mirrors phases 0-7 output) ──────────────────


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


async def _seed_through_phase7(graph: Any) -> dict[str, GraphNode]:
    """Full skeleton plus one LLR per HLR — everything phase 8 needs."""
    project = await _add(graph, NodeType.PROJECT, "phase08-it", "", None, [], {})
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
    llr_formula = await _add(
        graph, NodeType.LLR, "Fahrenheit Formula Application",
        "The system shall, when celsius_to_fahrenheit is called with a finite "
        "Celsius value, return that value multiplied by 9/5 plus 32.",
        hlr_convert.node_id, [hlr_convert.node_id], {},
    )
    llr_reject = await _add(
        graph, NodeType.LLR, "Non Numeric Input Rejection",
        "The system shall, when parse_temperature receives a string that is not "
        "a valid number, raise a ValueError naming the offending value.",
        hlr_validate.node_id, [hlr_validate.node_id], {},
    )
    return {
        "project": project,
        "document": document,
        "hlr_convert": hlr_convert,
        "hlr_validate": hlr_validate,
        "architecture": architecture,
        "module": module,
        "contract": contract,
        "llr_formula": llr_formula,
        "llr_reject": llr_reject,
    }


async def _seed_minimal_no_llrs(graph: Any) -> None:
    """A graph complete through phase 7 that simply contains no LLRs.

    With no HLRs there is nothing to refine, modularise, or contract, so every
    cumulative audit criterion up to phase 8 is trivially satisfied.
    """
    project = await _add(graph, NodeType.PROJECT, "phase08-it", "", None, [], {})
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


def _designs_covering(graph: Any, llr_id: str) -> list[GraphNode]:
    return [
        d for d in _nodes(graph, NodeType.DESIGN)
        if llr_id in (d.trace_to or [])
    ]


def _status(flow: ForgeFlow, phase: int) -> str:
    row = flow.phase_store.get(phase)
    assert row is not None, f"phase {phase} missing from the phase store"
    return str(row["status"])


def _assert_every_llr_designed_under_module(
    graph: Any, module_id: str, llr_ids: list[str]
) -> None:
    for llr_id in llr_ids:
        designs = _designs_covering(graph, llr_id)
        assert designs, f"LLR {llr_id} is not traced-to by any DESIGN"
        under_module = [d for d in designs if d.parent_id == module_id]
        assert under_module, (
            f"no DESIGN covering LLR {llr_id} lives under its MODULE {module_id}; "
            f"covering designs sit under {[d.parent_id for d in designs]}"
        )
        for design in under_module:
            assert design.content.strip(), f"DESIGN {design.node_id} has empty content"
            assert design.layer == 5


# ── Tests ────────────────────────────────────────────────────────────────────


class TestPhase08CreateDesigns:
    async def test_every_llr_is_covered_by_a_design_under_its_module(
        self, flow: ForgeFlow
    ) -> None:
        """Happy path: phase 8 alone, on a seeded graph, designs every LLR."""
        seeded = await _seed_through_phase7(flow.graph)
        assert GapType.UNDESIGNED in _gap_types(flow.graph), (
            "seeding is broken — phase 8 would have nothing to do"
        )

        await flow.run_phase(8)

        _assert_every_llr_designed_under_module(
            flow.graph,
            seeded["module"].node_id,
            [seeded["llr_formula"].node_id, seeded["llr_reject"].node_id],
        )
        assert GapType.UNDESIGNED not in _gap_types(flow.graph), (
            "UNDESIGNED gaps still outstanding after phase 8"
        )
        assert _status(flow, 8) == "complete"

    async def test_rerun_keeps_coverage_and_creates_no_duplicate_designs(
        self, flow: ForgeFlow
    ) -> None:
        """Re-running phase 8 with a real LLM must not duplicate DESIGNs."""
        seeded = await _seed_through_phase7(flow.graph)
        await flow.run_phase(8)
        count_after_first = len(_nodes(flow.graph, NodeType.DESIGN))
        assert count_after_first >= 1, "first run produced no DESIGN nodes"

        await flow.run_phase(8)

        count_after_second = len(_nodes(flow.graph, NodeType.DESIGN))
        assert count_after_second <= count_after_first, (
            f"re-run grew the DESIGN count: {count_after_first} → {count_after_second}"
        )
        _assert_every_llr_designed_under_module(
            flow.graph,
            seeded["module"].node_id,
            [seeded["llr_formula"].node_id, seeded["llr_reject"].node_id],
        )
        assert GapType.UNDESIGNED not in _gap_types(flow.graph)
        assert _status(flow, 8) == "complete"

    async def test_no_llrs_completes_cleanly_without_llm_calls(
        self, flow: ForgeFlow
    ) -> None:
        """With nothing to design, phase 8 must not dispatch anything."""
        await _seed_minimal_no_llrs(flow.graph)
        ids_before = {n.node_id for n in flow.graph.all_nodes()}
        calls_before = factory.llm_call_count

        await flow.run_phase(8)

        ids_after = {n.node_id for n in flow.graph.all_nodes()}
        assert ids_after == ids_before, (
            f"phase 8 created junk nodes with no LLRs present: "
            f"{sorted(ids_after - ids_before)}"
        )
        assert _nodes(flow.graph, NodeType.DESIGN) == []
        assert factory.llm_call_count == calls_before, (
            f"phase 8 made {factory.llm_call_count - calls_before} LLM call(s) "
            "despite having nothing to do"
        )
        assert _status(flow, 8) == "complete"
