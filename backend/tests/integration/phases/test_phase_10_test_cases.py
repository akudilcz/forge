"""Phase 10 (Write Test Cases) integration test — the phase in isolation.

Seeds the full precondition graph deterministically (PROJECT → DOCUMENT →
PARAs → HLRs → ARCHITECTURE → MODULE → CONTRACT → LLRs → DESIGN → SUITE),
then runs ONLY phase 10 against a real LLM. Proves the batched case authoring
step (``batch_phase10``) plus the case-trace coverage check leave every HLR
and LLR traced by at least one CASE, close the UNTESTED_HLR / UNTESTED_LLR
gaps, and that a re-run is idempotent.

Design reference: design/20_phase_10_write_test_cases.md
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

_SUITE_CONTENT = (
    "## Test Types\n"
    "Unit tests verify each pure function in isolation; integration tests verify "
    "the module's public interface end to end.\n\n"
    "## Coverage Targets\n"
    "Every HLR and LLR is verified by at least one behavioural test case; "
    "statement coverage target is 90% for the string-utilities module.\n\n"
    "## Test Environment\n"
    "pytest on Python 3.12 with no external services; deterministic inputs only.\n\n"
    "## Verification Approach\n"
    "Each requirement maps to one focused test case exercising its happy path "
    "and its documented edge case (empty input, mixed case)."
)


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
async def forge_flow(integration_config: ForgeConfig, tmp_path: Path) -> ForgeFlow:
    """A fully wired ForgeFlow on a fresh workspace and graph DB."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "forge.md").write_text(_SPEC, encoding="utf-8")

    config = integration_config.model_copy(deep=True)
    config.project.workspace_dir = str(workspace)
    config.project.name = "phase10-isolation"

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


async def _seed_suited_graph(flow: ForgeFlow) -> dict[str, Any]:
    """Seed everything phase 10 needs: the graph as phases 0-9 leave it."""
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
    suite = await _seed(
        graph, NodeType.SUITE, "Test Strategy", _SUITE_CONTENT, project.node_id, [], {},
    )
    return {"project": project, "hlrs": hlrs, "llrs": llrs, "suite": suite}


# ── Assertion helpers ────────────────────────────────────────────────────────


def _assert_full_case_coverage(graph: Any, seeded: dict[str, Any]) -> None:
    """Every HLR/LLR must be traced by ≥1 CASE, and every trace must resolve."""
    case_hlrs = _nodes(graph, NodeType.CASE_HLR)
    case_llrs = _nodes(graph, NodeType.CASE_LLR)
    all_ids = {n.node_id for n in graph.all_nodes()}

    for case in case_hlrs + case_llrs:
        assert case.content.strip(), f"{case.node_id} has empty content"
        assert case.trace_to, f"{case.node_id} traces to nothing"
        unresolved = [t for t in case.trace_to if t not in all_ids]
        assert unresolved == [], f"{case.node_id} traces to missing nodes: {unresolved}"

    hlr_covered = {t for c in case_hlrs for t in c.trace_to}
    llr_covered = {t for c in case_llrs for t in c.trace_to}
    untested_hlrs = [h.node_id for h in seeded["hlrs"] if h.node_id not in hlr_covered]
    untested_llrs = [llr.node_id for llr in seeded["llrs"] if llr.node_id not in llr_covered]
    assert untested_hlrs == [], f"HLRs with no CASE_HLR trace: {untested_hlrs}"
    assert untested_llrs == [], f"LLRs with no CASE_LLR trace: {untested_llrs}"


def _phase10_gap_types(graph: Any) -> set[GapType]:
    return {
        g.type
        for g in GapAnalyser().analyse(graph)
        if g.type in GAP_TYPE_TO_PHASE and GAP_TYPE_TO_PHASE[g.type] == 10
    }


# ── The test (one real LLM run + one idempotence re-run) ─────────────────────


async def test_phase_10_cases_cover_every_requirement_and_rerun_is_idempotent(
    forge_flow: ForgeFlow,
) -> None:
    """Real LLM run: batch case authoring covers all HLRs/LLRs; re-run is a no-op."""
    flow = forge_flow
    graph = flow.graph
    seeded = await _seed_suited_graph(flow)

    # Precondition: exactly phase 10's work is open — one gap per requirement.
    pre = GapAnalyser().analyse(graph)
    pre_types = {g.type for g in pre}
    assert GapType.UNTESTED_HLR in pre_types, "seeded graph did not raise UNTESTED_HLR"
    assert GapType.UNTESTED_LLR in pre_types, "seeded graph did not raise UNTESTED_LLR"
    earlier = [
        g.type for g in pre
        if g.type in GAP_TYPE_TO_PHASE and GAP_TYPE_TO_PHASE[g.type] < 10
    ]
    assert earlier == [], f"seed left earlier-phase gaps open: {earlier}"

    await flow.run_phase(10)

    # Coverage: every requirement traced by ≥1 valid CASE — this also proves the
    # case_trace_coverage step kept at least one valid trace per requirement.
    _assert_full_case_coverage(graph, seeded)

    # Gap analyser: UNTESTED_HLR / UNTESTED_LLR fully closed.
    assert _phase10_gap_types(graph) == set(), (
        f"phase 10 left its own gaps open: {_phase10_gap_types(graph)}"
    )

    cases_after_first_run = len(_nodes(graph, NodeType.CASE_HLR)) + len(
        _nodes(graph, NodeType.CASE_LLR)
    )

    # Re-run: idempotent — no new cases, coverage and gap closure preserved.
    await flow.run_phase(10)
    _assert_full_case_coverage(graph, seeded)
    assert _phase10_gap_types(graph) == set()
    cases_after_rerun = len(_nodes(graph, NodeType.CASE_HLR)) + len(
        _nodes(graph, NodeType.CASE_LLR)
    )
    assert cases_after_rerun == cases_after_first_run, (
        f"re-running phase 10 changed CASE count "
        f"{cases_after_first_run} → {cases_after_rerun}"
    )
