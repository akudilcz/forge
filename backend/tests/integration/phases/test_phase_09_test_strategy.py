"""Phase 9 (Write Test Strategy) integration test — the phase in isolation.

Seeds the full precondition graph deterministically (PROJECT → DOCUMENT →
PARAs → HLRs → ARCHITECTURE → MODULE → CONTRACT → LLRs → DESIGN), then runs
ONLY phase 9 against a real LLM. Proves the phase closes its single UNSUITED
gap by writing exactly one SUITE node under PROJECT, and that a re-run is
idempotent.

Design reference: design/19_phase_09_write_test_strategy.md
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.analysis.gap_analyser import GapAnalyser
from backend.analysis.gaps import GapType
from backend.config.models import ForgeConfig
from backend.crew.flow import GAP_TYPE_TO_PHASE, ForgeFlow
from backend.forge_builder import ForgeBuilder
from backend.graph.models import GraphNode, LifecycleState, NodeType

pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.timeout(1200)]

_SPEC = (
    "# String Utilities\n\n"
    "The system shall reverse strings.\n\n"
    "The system shall count vowels.\n"
)

_HLR_SPECS = [
    (
        "Reverse String Output",
        "The system shall return the characters of the input string in reverse order.",
    ),
    (
        "Vowel Count Report",
        "The system shall report the number of vowel characters contained in the input string.",
    ),
]

_LLR_SPECS = [
    (
        "Empty Input Reversal",
        "The system shall return an empty string when the reverse operation receives "
        "an empty input string.",
    ),
    (
        "Case-Insensitive Vowel Match",
        "The system shall count uppercase and lowercase vowel characters identically "
        "when counting vowels.",
    ),
]


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def forge_flow(integration_config: ForgeConfig, tmp_path: Path) -> ForgeFlow:
    """A fully wired ForgeFlow on a fresh workspace and graph DB."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "forge.md").write_text(_SPEC, encoding="utf-8")

    config = integration_config.model_copy(deep=True)
    config.project.workspace_dir = str(workspace)
    config.project.name = "phase09-isolation"

    builder = ForgeBuilder(config=config, workspace=workspace, db_path=tmp_path / "forge.db")
    return await builder.build()


# ── Deterministic graph seeding ──────────────────────────────────────────────


async def _seed(
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


def _nodes(graph: Any, node_type: NodeType) -> list[GraphNode]:
    return [n for n in graph.all_nodes() if n.node_type == node_type.value]


async def _seed_designed_graph(flow: ForgeFlow) -> dict[str, Any]:
    """Seed everything phase 9 needs: the graph as phases 0-8 leave it."""
    graph = flow.graph
    await flow.run_phase(0)  # PROJECT (deterministic)
    await flow.run_phase(1)  # DOCUMENT (deterministic)
    project = _nodes(graph, NodeType.PROJECT)[0]
    document = _nodes(graph, NodeType.DOCUMENT)[0]

    hlrs: list[GraphNode] = []
    for title, content in _HLR_SPECS:
        para = await _seed(
            graph, NodeType.PARA, f"Para: {title}", content,
            document.node_id, [], {"sub_type": "requirement"},
        )
        hlrs.append(
            await _seed(graph, NodeType.HLR, title, content, para.node_id, [para.node_id], {})
        )

    arch = await _seed(
        graph, NodeType.ARCHITECTURE, "String Utility Architecture",
        "The system is a single string-utilities module exposing pure functions "
        "for string reversal and vowel counting.",
        project.node_id, [], {},
    )
    module = await _seed(
        graph, NodeType.MODULE, "String Utilities Module",
        "Owns string reversal and vowel counting behaviour.",
        arch.node_id, [h.node_id for h in hlrs], {},
    )
    await _seed(
        graph, NodeType.CONTRACT, "String Utilities Interface",
        "Public interface: reverse(text: str) -> str; count_vowels(text: str) -> int.",
        module.node_id, [module.node_id], {},
    )

    llrs: list[GraphNode] = []
    for (title, content), hlr in zip(_LLR_SPECS, hlrs, strict=True):
        llrs.append(
            await _seed(graph, NodeType.LLR, title, content, hlr.node_id, [hlr.node_id], {})
        )

    await _seed(
        graph, NodeType.DESIGN, "String Utilities Design",
        "Module src/string_utils.py implements reverse() and count_vowels() as "
        "pure functions with input validation on entry.",
        module.node_id, [llr.node_id for llr in llrs], {"file_path": "src/string_utils.py"},
    )
    return {"project": project, "hlrs": hlrs, "llrs": llrs, "module": module}


def _gaps_before_phase(graph: Any, phase: int) -> list[GapType]:
    """Structural gap types owned by phases earlier than *phase*."""
    return [
        g.type
        for g in GapAnalyser().analyse(graph)
        if g.type in GAP_TYPE_TO_PHASE and GAP_TYPE_TO_PHASE[g.type] < phase
    ]


def _gaps_of_phase(graph: Any, phase: int) -> list[GapType]:
    """Structural gap types owned by exactly *phase*."""
    return [
        g.type
        for g in GapAnalyser().analyse(graph)
        if g.type in GAP_TYPE_TO_PHASE and GAP_TYPE_TO_PHASE[g.type] == phase
    ]


# ── The test (one real LLM run + one idempotence re-run) ─────────────────────


async def test_phase_09_writes_single_suite_and_is_idempotent(
    forge_flow: ForgeFlow,
) -> None:
    """Real LLM run: exactly one SUITE under PROJECT, UNSUITED closed, re-run safe."""
    flow = forge_flow
    graph = flow.graph
    seeded = await _seed_designed_graph(flow)

    # Precondition: the seed left exactly phase 9's work open, nothing earlier.
    open_types = {g.type for g in GapAnalyser().analyse(graph)}
    assert GapType.UNSUITED in open_types, "seeded graph did not raise UNSUITED"
    assert _gaps_before_phase(graph, 9) == [], (
        f"seed left earlier-phase gaps open: {_gaps_before_phase(graph, 9)}"
    )

    await flow.run_phase(9)

    # Exactly ONE SUITE, hanging off PROJECT, with non-empty strategy content.
    suites = _nodes(graph, NodeType.SUITE)
    assert len(suites) == 1, f"expected exactly 1 SUITE node, got {len(suites)}"
    suite = suites[0]
    assert suite.parent_id == seeded["project"].node_id, (
        f"SUITE must be a child of PROJECT, got parent {suite.parent_id}"
    )
    assert len(suite.content.strip()) > 100, (
        "SUITE strategy content is empty or trivially short: "
        f"{suite.content[:80]!r}"
    )

    # Gap analyser: UNSUITED closed, no phase-9 structural gaps outstanding.
    after = {g.type for g in GapAnalyser().analyse(graph)}
    assert GapType.UNSUITED not in after, "phase 9 did not close its UNSUITED gap"
    assert _gaps_of_phase(graph, 9) == []

    # Re-run must not create a second SUITE (documented idempotence).
    await flow.run_phase(9)
    suites_after_rerun = _nodes(graph, NodeType.SUITE)
    assert len(suites_after_rerun) == 1, (
        f"re-running phase 9 changed SUITE count to {len(suites_after_rerun)}"
    )
    assert GapType.UNSUITED not in {g.type for g in GapAnalyser().analyse(graph)}
