"""Per-phase integration test for Phase 6 — Write Contracts (real LLM).

Phase 6 must give every MODULE a CONTRACT child (design/16_phase_06_write_contracts.md).
Rather than paying for phases 0-5 with live agents, the precondition graph is
seeded deterministically — PROJECT → DOCUMENT → PARAs → HLRs → ARCHITECTURE →
MODULEs with trace links, mirroring exactly what those phases produce — and then
ONLY phase 6 runs against the real LLM.

The graph is kept tiny (2 HLRs, 2 MODULEs) so each test costs a handful of
LLM calls.  Covered:

* Happy path — every MODULE gains a CONTRACT child with substantive content,
  the UNCONTRACTED gap closes, and the phase audits complete.
* Idempotency — re-running phase 6 with a real LLM creates no duplicate
  CONTRACTs.
* Empty precondition — with no MODULEs the phase completes cleanly without
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
    """Cap runaway loops; a converging phase 6 run needs far fewer calls."""
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
    config.project.name = "phase06-it"
    builder = ForgeBuilder(config=config, workspace=workspace, db_path=tmp_path / "forge.db")
    return await builder.build()


# ── Deterministic graph seeding (mirrors phases 0-5 output) ──────────────────


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


async def _seed_through_phase5(graph: Any) -> dict[str, GraphNode]:
    """PROJECT → DOCUMENT → 2 requirement PARAs → 2 HLRs → ARCHITECTURE → 2 MODULEs.

    Node order matters: ancestors first, ARCHITECTURE after HLRs (so the
    STALE_ARCHITECTURE check sees no HLRs newer than it), MODULEs last.
    """
    project = await _add(
        graph, NodeType.PROJECT, "phase06-it",
        "", None, [], {},
    )
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
        graph, NodeType.ARCHITECTURE, "Layered Converter Architecture",
        "The system decomposes into two modules: a conversion engine that owns "
        "the numeric Celsius-to-Fahrenheit transformation, and an input validator "
        "that parses and validates raw caller input before conversion. Modules "
        "communicate through plain typed function calls.",
        project.node_id, [], {},
    )
    module_engine = await _add(
        graph, NodeType.MODULE, "Conversion Engine",
        "Owns the numeric temperature transformation. Exposes a pure function "
        "converting a Celsius value to Fahrenheit; holds no state.",
        architecture.node_id, [hlr_convert.node_id], {},
    )
    module_validator = await _add(
        graph, NodeType.MODULE, "Input Validator",
        "Owns parsing and validation of raw caller input. Rejects non-numeric "
        "values with a descriptive error before any conversion is attempted.",
        architecture.node_id, [hlr_validate.node_id], {},
    )
    return {
        "project": project,
        "document": document,
        "hlr_convert": hlr_convert,
        "hlr_validate": hlr_validate,
        "architecture": architecture,
        "module_engine": module_engine,
        "module_validator": module_validator,
    }


async def _seed_minimal_no_modules(graph: Any) -> None:
    """A graph complete through phase 5 that simply contains no MODULEs.

    No HLRs either — an HLR without a MODULE would be an open phase-5 gap and
    the phase-6 audit is cumulative. A heading-only PARA keeps the DOCUMENT
    chunked without demanding HLR coverage.
    """
    project = await _add(graph, NodeType.PROJECT, "phase06-it", "", None, [], {})
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


def _contracts_of(graph: Any, module_id: str) -> list[GraphNode]:
    return [
        c for c in graph.children_sync(module_id)
        if c.node_type == NodeType.CONTRACT.value
    ]


def _status(flow: ForgeFlow, phase: int) -> str:
    row = flow.phase_store.get(phase)
    assert row is not None, f"phase {phase} missing from the phase store"
    return str(row["status"])


# ── Tests ────────────────────────────────────────────────────────────────────


class TestPhase06WriteContracts:
    async def test_every_module_gets_a_contract_with_real_content(
        self, flow: ForgeFlow
    ) -> None:
        """Happy path: phase 6 alone, on a seeded graph, contracts every MODULE."""
        seeded = await _seed_through_phase5(flow.graph)
        assert GapType.UNCONTRACTED in _gap_types(flow.graph), (
            "seeding is broken — phase 6 would have nothing to do"
        )

        await flow.run_phase(6)

        for key in ("module_engine", "module_validator"):
            module = seeded[key]
            contracts = _contracts_of(flow.graph, module.node_id)
            assert contracts, f"MODULE {module.node_id} ({module.title!r}) got no CONTRACT"
            for contract in contracts:
                assert contract.content.strip(), (
                    f"CONTRACT {contract.node_id} has empty content"
                )
                assert len(contract.content.strip()) >= 50, (
                    f"CONTRACT {contract.node_id} content is trivially short: "
                    f"{contract.content!r}"
                )
                assert contract.layer == 4

        assert GapType.UNCONTRACTED not in _gap_types(flow.graph), (
            "UNCONTRACTED gaps still outstanding after phase 6"
        )
        assert _status(flow, 6) == "complete"

    async def test_rerun_creates_no_duplicate_contracts(self, flow: ForgeFlow) -> None:
        """Re-running phase 6 with a real LLM must not add a second CONTRACT."""
        seeded = await _seed_through_phase5(flow.graph)
        await flow.run_phase(6)
        counts_after_first = {
            key: len(_contracts_of(flow.graph, seeded[key].node_id))
            for key in ("module_engine", "module_validator")
        }
        assert all(c >= 1 for c in counts_after_first.values()), (
            f"first run failed to contract every module: {counts_after_first}"
        )

        await flow.run_phase(6)

        counts_after_second = {
            key: len(_contracts_of(flow.graph, seeded[key].node_id))
            for key in ("module_engine", "module_validator")
        }
        assert counts_after_second == counts_after_first, (
            f"re-run changed CONTRACT counts: {counts_after_first} → {counts_after_second}"
        )
        assert GapType.UNCONTRACTED not in _gap_types(flow.graph)
        assert _status(flow, 6) == "complete"

    async def test_no_modules_completes_cleanly_without_llm_calls(
        self, flow: ForgeFlow
    ) -> None:
        """With nothing to contract, phase 6 must not dispatch anything."""
        await _seed_minimal_no_modules(flow.graph)
        ids_before = {n.node_id for n in flow.graph.all_nodes()}
        calls_before = factory.llm_call_count

        await flow.run_phase(6)

        ids_after = {n.node_id for n in flow.graph.all_nodes()}
        assert ids_after == ids_before, (
            f"phase 6 created junk nodes with no MODULEs present: "
            f"{sorted(ids_after - ids_before)}"
        )
        assert _nodes(flow.graph, NodeType.CONTRACT) == []
        assert factory.llm_call_count == calls_before, (
            f"phase 6 made {factory.llm_call_count - calls_before} LLM call(s) "
            "despite having nothing to do"
        )
        assert _status(flow, 6) == "complete"
