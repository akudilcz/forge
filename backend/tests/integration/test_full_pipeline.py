"""Full-pipeline integration tests: Phase 0 → Phase 14.

Each test ingests a forge.md, runs every phase with real LLM calls, and
asserts graph correctness at each phase boundary.

Uses ``ForgeBuilder`` — the same dependency wiring as production.

Safety nets:
- Per-phase asyncio timeout
- Global LLM call limit (catches infinite loops)

Run with:
    uv run pytest backend/tests/integration/test_full_pipeline.py \
        -m integration -x -v -s --timeout=900
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from backend.analysis.gap_analyser import GapAnalyser
from backend.config.models import ForgeConfig
from backend.forge_builder import ForgeBuilder
from backend.graph.engine import ProjectGraph
from backend.graph.models import NodeType
from backend.pipeline.flow import ForgeFlow

# ── Graph query helpers ──────────────────────────────────────────────────────


def _nodes_of_type(graph: ProjectGraph, ntype: NodeType) -> list[dict[str, Any]]:
    """Return all nodes of the given type from the in-memory NetworkX graph."""
    return [data for _, data in graph._g.nodes(data=True) if data.get("node_type") == ntype.value]


def _gap_types_present(graph: ProjectGraph) -> set[str]:
    """Return the set of gap type values currently detected in the graph."""
    analyser = GapAnalyser()
    return {g.type.value for g in analyser.analyse(graph)}


# ── Per-phase assertions ─────────────────────────────────────────────────────


def assert_phase_0(graph: ProjectGraph) -> None:
    projects = _nodes_of_type(graph, NodeType.PROJECT)
    assert len(projects) >= 1, "Phase 0: no PROJECT node"


def assert_phase_1(graph: ProjectGraph) -> None:
    docs = _nodes_of_type(graph, NodeType.DOCUMENT)
    assert len(docs) >= 1, "Phase 1: no DOCUMENT node"
    assert docs[0].get("content"), "Phase 1: DOCUMENT has no content"


def assert_phase_2(graph: ProjectGraph, min_paras: int = 2) -> None:
    paras = _nodes_of_type(graph, NodeType.PARA)
    assert len(paras) >= min_paras, f"Phase 2: expected >={min_paras} PARAs, got {len(paras)}"
    gaps = _gap_types_present(graph)
    assert "UNCHUNKED_DOCUMENT" not in gaps


def assert_phase_3(graph: ProjectGraph, min_hlrs: int = 2) -> None:
    hlrs = _nodes_of_type(graph, NodeType.HLR)
    assert len(hlrs) >= min_hlrs, f"Phase 3: expected >={min_hlrs} HLRs, got {len(hlrs)}"
    gaps = _gap_types_present(graph)
    assert "UNCOVERED_PARA" not in gaps
    for hlr in hlrs:
        assert hlr.get("content"), "Phase 3: HLR has no content"


def assert_phase_4(graph: ProjectGraph) -> None:
    archs = _nodes_of_type(graph, NodeType.ARCHITECTURE)
    assert len(archs) >= 1, "Phase 4: no ARCHITECTURE node"
    assert archs[0].get("content"), "Phase 4: ARCHITECTURE has no content"
    gaps = _gap_types_present(graph)
    assert "UNARCHITECTED" not in gaps


def assert_phase_5(graph: ProjectGraph, min_modules: int = 1) -> None:
    modules = _nodes_of_type(graph, NodeType.MODULE)
    assert len(modules) >= min_modules, (
        f"Phase 5: expected >={min_modules} MODULEs, got {len(modules)}"
    )
    gaps = _gap_types_present(graph)
    assert "UNMODULARISED" not in gaps


def assert_phase_6(graph: ProjectGraph) -> None:
    contracts = _nodes_of_type(graph, NodeType.CONTRACT)
    modules = _nodes_of_type(graph, NodeType.MODULE)
    assert len(contracts) >= len(modules), (
        f"Phase 6: {len(contracts)} CONTRACTs for {len(modules)} MODULEs"
    )
    gaps = _gap_types_present(graph)
    assert "UNCONTRACTED" not in gaps


def assert_phase_7(graph: ProjectGraph, min_llrs: int = 2) -> None:
    llrs = _nodes_of_type(graph, NodeType.LLR)
    assert len(llrs) >= min_llrs, f"Phase 7: expected >={min_llrs} LLRs, got {len(llrs)}"
    gaps = _gap_types_present(graph)
    assert "UNREFINED_HLR" not in gaps


def assert_phase_8(graph: ProjectGraph, min_designs: int = 1) -> None:
    designs = _nodes_of_type(graph, NodeType.DESIGN)
    assert len(designs) >= min_designs, (
        f"Phase 8: expected >={min_designs} DESIGNs, got {len(designs)}"
    )
    gaps = _gap_types_present(graph)
    assert "UNDESIGNED" not in gaps


def assert_phase_9(graph: ProjectGraph) -> None:
    suites = _nodes_of_type(graph, NodeType.SUITE)
    assert len(suites) >= 1, "Phase 9: no SUITE node"
    assert suites[0].get("content"), "Phase 9: SUITE has no content"
    gaps = _gap_types_present(graph)
    assert "UNSUITED" not in gaps


def assert_phase_10(graph: ProjectGraph) -> None:
    cases_hlr = _nodes_of_type(graph, NodeType.CASE_HLR)
    cases_llr = _nodes_of_type(graph, NodeType.CASE_LLR)
    assert len(cases_hlr) >= 1, "Phase 10: no CASE_HLR nodes"
    assert len(cases_llr) >= 1, "Phase 10: no CASE_LLR nodes"
    gaps = _gap_types_present(graph)
    assert "UNTESTED_HLR" not in gaps, "Phase 10: UNTESTED_HLR gap remains"
    assert "UNTESTED_LLR" not in gaps, "Phase 10: UNTESTED_LLR gap remains"


def assert_phase_11(workspace: Path) -> None:
    docs_dir = workspace / "docs"
    assert docs_dir.exists(), "Phase 11: docs/ directory not created"
    md_files = list(docs_dir.glob("*.md"))
    assert len(md_files) >= 4, f"Phase 11: expected >=4 docs, got {len(md_files)}"


def assert_phase_12(workspace: Path) -> None:
    """Phase 12 must produce src + tests AND hit 100% coverage on every axis.

    The old check only verified file presence, which let the pipeline ship
    results with only ~73% requirement coverage. The coverage gate inside
    run_code_gen should already raise on any gap, but we cross-check here
    so a regression that removes the gate is still caught by the test.
    """
    src_files = [f for f in (workspace / "src").rglob("*.py") if f.name != "__init__.py"]
    test_files = list((workspace / "tests").rglob("test_*.py"))
    assert len(src_files) >= 1, "Phase 12: no source files"
    assert len(test_files) >= 1, "Phase 12: no test files"

    # Cross-check coverage via the lcov report produced by the codegen loop.
    lcov = workspace / "coverage.lcov"
    assert lcov.exists(), "Phase 12: coverage.lcov not produced"
    text = lcov.read_text()
    # LCOV summary lines: LF = lines found, LH = lines hit, BRF/BRH same for branches.
    summary: dict[str, int] = {}
    for raw in text.splitlines():
        if ":" not in raw:
            continue
        key, _, val = raw.partition(":")
        if key in {"LF", "LH", "BRF", "BRH"}:
            try:
                summary[key] = summary.get(key, 0) + int(val)
            except ValueError:
                pass
    assert summary.get("LF", 0) > 0, "Phase 12: no lines found in lcov"
    assert summary["LF"] == summary.get("LH", 0), (
        f"Phase 12: statement coverage not 100% "
        f"(LH={summary.get('LH', 0)}/{summary['LF']})"
    )
    # BRF>0 → real branches present, assert 100%. BRF=0 → source has no
    # boolean logic, nothing to cover, skip (matches the pipeline gate).
    if summary.get("BRF", 0) > 0:
        assert summary["BRF"] == summary.get("BRH", 0), (
            f"Phase 12: branch/MC-DC coverage not 100% "
            f"(BRH={summary.get('BRH', 0)}/{summary['BRF']})"
        )

    # Cross-check that the JUnit XML shows zero failures/errors.
    xml = workspace / "coverage-test-results.xml"
    if xml.exists():
        body = xml.read_text()
        import re as _re
        m = _re.search(r'errors="(\d+)"[^>]*failures="(\d+)"', body)
        if m:
            assert m.group(1) == "0", f"Phase 12: {m.group(1)} test error(s)"
            assert m.group(2) == "0", f"Phase 12: {m.group(2)} test failure(s)"


def assert_phase_13(graph: ProjectGraph) -> None:
    codes = _nodes_of_type(graph, NodeType.CODE)
    tests = _nodes_of_type(graph, NodeType.TEST)
    assert len(codes) >= 1 or len(tests) >= 1, "Phase 13: no CODE or TEST nodes created"


def assert_phase_14(workspace: Path) -> None:
    zips = list(workspace.rglob("deliverables.zip"))
    assert len(zips) >= 1, "Phase 14: deliverables.zip not found"


def assert_no_structural_gaps(graph: ProjectGraph) -> None:
    structural = {
        "UNCHUNKED_DOCUMENT",
        "UNCOVERED_PARA",
        "UNARCHITECTED",
        "UNMODULARISED",
        "UNCONTRACTED",
        "UNREFINED_HLR",
        "UNDESIGNED",
        "UNSUITED",
        "UNTESTED_HLR",
        "UNTESTED_LLR",
    }
    remaining = _gap_types_present(graph) & structural
    assert not remaining, f"Structural gaps remain: {remaining}"


# ── Run-phase wrapper with timeout ───────────────────────────────────────────


async def _run_phase_with_timeout(
    flow: ForgeFlow,
    phase: int,
    timeouts: dict[int, int],
) -> None:
    """Run a single phase with a per-phase asyncio timeout."""
    timeout = timeouts.get(phase, 120)
    try:
        await asyncio.wait_for(flow.run_phase(phase), timeout=timeout)
    except TimeoutError:
        pytest.fail(f"Phase {phase} timed out after {timeout}s")


# ── Shared pipeline runner ───────────────────────────────────────────────────


async def _run_full_pipeline(
    integration_config: ForgeConfig,
    tmp_path: Path,
    forge_md: str,
    *,
    max_llm_calls: int,
    phase_timeouts: dict[int, int],
    phase_assertions: dict[int, dict[str, int]],
) -> None:
    """Run phases 0-14 with per-phase assertions and safety nets."""
    import backend.agents.factory as _fmod

    _fmod.llm_call_count = 0
    _fmod.llm_call_limit = max_llm_calls

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "forge.md").write_text(forge_md)
    integration_config.project.workspace_dir = str(workspace)

    builder = ForgeBuilder(
        config=integration_config,
        workspace=workspace,
        db_path=tmp_path / "forge.db",
    )
    flow = await builder.build()

    try:
        for phase in range(15):
            await _run_phase_with_timeout(flow, phase, phase_timeouts)

            # Run phase-specific assertions
            kwargs = phase_assertions.get(phase, {})
            if phase == 0:
                assert_phase_0(flow.graph)
            elif phase == 1:
                assert_phase_1(flow.graph)
            elif phase == 2:
                assert_phase_2(flow.graph, **kwargs)
            elif phase == 3:
                assert_phase_3(flow.graph, **kwargs)
            elif phase == 4:
                assert_phase_4(flow.graph)
            elif phase == 5:
                assert_phase_5(flow.graph, **kwargs)
            elif phase == 6:
                assert_phase_6(flow.graph)
            elif phase == 7:
                assert_phase_7(flow.graph, **kwargs)
            elif phase == 8:
                assert_phase_8(flow.graph, **kwargs)
            elif phase == 9:
                assert_phase_9(flow.graph)
            elif phase == 10:
                assert_phase_10(flow.graph)
            elif phase == 11:
                assert_phase_11(workspace)
            elif phase == 12:
                assert_phase_12(workspace)
            elif phase == 13:
                assert_phase_13(flow.graph)
            elif phase == 14:
                assert_phase_14(workspace)

    finally:
        print(f"\n[integration] Total LLM calls: {_fmod.llm_call_count}")
        _fmod.llm_call_limit = None

    assert_no_structural_gaps(flow.graph)


# ── Test: bubble sort (minimal, fast) ────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(18000)
async def test_full_pipeline_bubble_sort(
    integration_config: ForgeConfig,
    tmp_path: Path,
) -> None:
    """Full pipeline on a trivial bubble-sort spec.

    Per-phase budgets are sized for claude-opus-4-7 and prioritise
    quality over throughput — the goal is a correct, fully-traced
    generated workspace, not a fast test.
    """
    from backend.tests.integration.scenarios.bubble_sort.forge_md import (
        FORGE_MD as BUBBLE_SORT_MD,
    )

    await _run_full_pipeline(
        integration_config,
        tmp_path,
        BUBBLE_SORT_MD,
        max_llm_calls=2000,
        phase_timeouts={
            0: 30,
            1: 30,
            2: 300,
            3: 360,
            4: 300,
            5: 300,
            6: 300,
            7: 360,
            8: 360,
            9: 300,
            10: 600,
            11: 60,
            12: 9000,
            13: 60,
            14: 60,
        },
        phase_assertions={
            2: {"min_paras": 2},
            3: {"min_hlrs": 2},
            7: {"min_llrs": 2},
        },
    )


# ── Test: A* search (multi-module, stress test) ─────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(21600)
async def test_full_pipeline_astar_search(
    integration_config: ForgeConfig,
    tmp_path: Path,
) -> None:
    """Full pipeline on a multi-module A* search spec.

    Exercises:
    - 4+ PARAs (graph, heuristics, search, performance, errors)
    - 8+ HLRs across 3 modules
    - 2-3 MODULEs (graph, heuristics, search)
    - Multiple CONTRACTs with cross-module interfaces
    - 10-15 LLRs with algorithmic constraints
    - Non-trivial DESIGNs (priority queue, adjacency list, registry)
    - 10-15 CASE nodes in Phase 10

    Per-phase budgets are sized for claude-opus-4-7 and prioritise
    quality over throughput.
    """
    from backend.tests.integration.scenarios.astar_search.forge_md import (
        FORGE_MD as ASTAR_MD,
    )

    await _run_full_pipeline(
        integration_config,
        tmp_path,
        ASTAR_MD,
        max_llm_calls=3000,
        phase_timeouts={
            0: 30,
            1: 30,
            2: 1800,
            3: 1800,
            4: 900,
            5: 900,
            6: 1200,
            7: 1800,
            8: 1800,
            9: 900,
            10: 2400,
            11: 120,
            12: 9000,
            13: 120,
            14: 120,
        },
        phase_assertions={
            2: {"min_paras": 3},
            3: {"min_hlrs": 6},
            5: {"min_modules": 2},
            7: {"min_llrs": 8},
            8: {"min_designs": 2},
        },
    )

