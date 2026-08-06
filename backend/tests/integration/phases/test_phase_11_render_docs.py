"""Phase 11 (Render Documentation) integration test — the phase in isolation.

Phase 11 is a deterministic template render (no LLM): ``_run_dashboard_phase``
→ ``render_dashboard`` reads every graph node from phases 3-10 and writes one
Markdown file per phase into ``[workspace]/docs/``. These tests seed the full
graph deterministically (PROJECT → DOCUMENT → PARAs → HLRs → ARCHITECTURE →
MODULE → CONTRACT → LLRs → DESIGN → SUITE → CASEs) and prove the docs are
rendered with the seeded requirement text inlined, that an empty graph renders
without crashing, and that a re-run overwrites cleanly and reproducibly.

Design reference: specs/03-build-pipeline.md
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.analysis.gap_analyser import GapAnalyser
from backend.config.models import ForgeConfig
from backend.core.forge_builder import ForgeBuilder
from backend.graph.models import GraphNode, LifecycleState, NodeType
from backend.pipeline.flow import ForgeFlow

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

_CONTRACT_CONTENT = (
    "Public interface: reverse(text: str) -> str; count_vowels(text: str) -> int."
)

_SUITE_CONTENT = (
    "Every requirement is verified by at least one behavioural test case "
    "exercising its happy path and its documented edge cases."
)

_DOC_FILES = [
    "03-HLR.md",
    "04-Architecture.md",
    "05-Modules.md",
    "06-Contracts.md",
    "07-LLR.md",
    "08-Design.md",
    "09-Test-Suite.md",
    "10-Verification.md",
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
    config.project.name = "phase11-isolation"

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


async def _seed_requirements(flow: ForgeFlow) -> dict[str, Any]:
    """PROJECT → DOCUMENT → PARAs → HLRs → ARCHITECTURE → MODULE → CONTRACT."""
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
        graph, NodeType.CONTRACT, "String Utilities Interface", _CONTRACT_CONTENT,
        module.node_id, [module.node_id], {},
    )
    return {"project": project, "module": module, "hlrs": hlrs}


async def _seed_full_graph(flow: ForgeFlow) -> dict[str, Any]:
    """Seed everything phase 11 reads: the graph as phases 0-10 leave it."""
    graph = flow.graph
    seeded = await _seed_requirements(flow)

    llrs: list[GraphNode] = []
    for (title, content), hlr in zip(_LLR_SPECS, seeded["hlrs"], strict=True):
        llrs.append(
            await _seed(graph, NodeType.LLR, title, content, hlr.node_id, [hlr.node_id], {})
        )
    await _seed(
        graph, NodeType.DESIGN, "String Utilities Design",
        "Module src/string_utils.py implements reverse() and count_vowels() as "
        "pure functions with input validation on entry.",
        seeded["module"].node_id, [llr.node_id for llr in llrs],
        {"file_path": "src/string_utils.py"},
    )
    suite = await _seed(
        graph, NodeType.SUITE, "Test Strategy", _SUITE_CONTENT,
        seeded["project"].node_id, [], {},
    )
    for hlr in seeded["hlrs"]:
        await _seed(
            graph, NodeType.CASE_HLR, f"Case for {hlr.title}",
            f"Given a valid input, when the system runs, then {hlr.node_id} holds.",
            suite.node_id, [hlr.node_id], {},
        )
    for llr in llrs:
        await _seed(
            graph, NodeType.CASE_LLR, f"Case for {llr.title}",
            f"Given a valid input, when the method runs, then {llr.node_id} holds.",
            suite.node_id, [llr.node_id], {},
        )
    return {**seeded, "llrs": llrs, "suite": suite}


def _docs_dir(flow: ForgeFlow) -> Path:
    return Path(flow.config.project.workspace_dir) / "docs"


def _read_docs(flow: ForgeFlow) -> dict[str, str]:
    return {name: (_docs_dir(flow) / name).read_text(encoding="utf-8") for name in _DOC_FILES}


# ── Tests (deterministic — zero LLM calls) ───────────────────────────────────


async def test_renders_all_docs_with_seeded_requirement_text(
    forge_flow: ForgeFlow,
) -> None:
    """All 8 docs exist, are non-empty, and inline the seeded node content."""
    flow = forge_flow
    await _seed_full_graph(flow)
    gaps_before = {(g.type, g.node_id) for g in GapAnalyser().analyse(flow.graph)}
    nodes_before = len(flow.graph.all_nodes())

    await flow.run_phase(11)

    for name in _DOC_FILES:
        path = _docs_dir(flow) / name
        assert path.exists(), f"phase 11 did not render {name}"
        assert path.read_text(encoding="utf-8").strip(), f"{name} is empty"

    docs = _read_docs(flow)
    for _, hlr_content in _HLR_SPECS:
        assert hlr_content in docs["03-HLR.md"], "HLR text missing from 03-HLR.md"
    for _, llr_content in _LLR_SPECS:
        assert llr_content in docs["07-LLR.md"], "LLR text missing from 07-LLR.md"
    # LLR docs inline the traced HLR text; CASE docs inline the requirement text.
    assert _HLR_SPECS[0][1] in docs["07-LLR.md"], "07-LLR.md does not inline HLR text"
    assert _HLR_SPECS[0][1] in docs["10-Verification.md"], (
        "10-Verification.md does not inline the requirement under test"
    )
    assert _LLR_SPECS[0][1] in docs["10-Verification.md"]
    # DESIGN docs inline sibling CONTRACT interfaces.
    assert _CONTRACT_CONTENT in docs["08-Design.md"], (
        "08-Design.md does not inline the sibling CONTRACT"
    )
    assert _SUITE_CONTENT in docs["09-Test-Suite.md"]

    # Deterministic phase: no graph mutation, no gap changes, marked complete.
    assert len(flow.graph.all_nodes()) == nodes_before, "phase 11 mutated the graph"
    gaps_after = {(g.type, g.node_id) for g in GapAnalyser().analyse(flow.graph)}
    assert gaps_after == gaps_before, "phase 11 changed the gap population"
    row = flow.phase_store.get(11)
    assert row is not None
    assert str(row["status"]) == "complete"


async def test_empty_graph_renders_without_crashing(forge_flow: ForgeFlow) -> None:
    """Edge case: a completely empty graph still renders all 8 placeholder docs."""
    flow = forge_flow
    assert flow.graph.all_nodes() == []

    await flow.run_phase(11)

    for name in _DOC_FILES:
        path = _docs_dir(flow) / name
        assert path.exists(), f"empty-graph render did not produce {name}"
        content = path.read_text(encoding="utf-8")
        assert "No nodes produced for this phase." in content, (
            f"{name} lacks the empty-graph placeholder"
        )
    row = flow.phase_store.get(11)
    assert row is not None
    assert str(row["status"]) == "complete"


async def test_rerun_overwrites_cleanly_and_reproducibly(forge_flow: ForgeFlow) -> None:
    """Re-running phase 11 restores every doc byte-for-byte, clobbering edits."""
    flow = forge_flow
    await _seed_full_graph(flow)
    await flow.run_phase(11)
    first_render = _read_docs(flow)

    # Corrupt one doc and delete another — the re-run must repair both.
    (_docs_dir(flow) / "03-HLR.md").write_text("CORRUPTED BY TEST", encoding="utf-8")
    (_docs_dir(flow) / "10-Verification.md").unlink()

    await flow.run_phase(11)

    second_render = _read_docs(flow)
    assert second_render == first_render, (
        "re-running phase 11 on an unchanged graph did not reproduce identical docs"
    )
