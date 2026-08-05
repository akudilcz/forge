"""Parametrised integration tests: Phase 11 → Phase 12 → assertions.

Each scenario populates a project graph, renders docs (Phase 11),
runs code generation with a real LLM (Phase 12), then verifies:
  - all required docs rendered
  - source + test files generated
  - all LLRs traced via @traces decorators
  - all tests pass
  - 100% statement coverage
  - 100% branch coverage
  - no dead code (untraced functions)
  - gaps resolved

Run with:
    uv run pytest backend/tests/integration/ -m integration -x -v -s --timeout=600
"""

from __future__ import annotations

import importlib
import subprocess
from pathlib import Path
from typing import Any

import pytest

from backend.config.models import ForgeConfig
from backend.crew.bazel_gen import init_bazel_workspace
from backend.crew.code_gen import CodeGenResult, run_code_gen
from backend.graph.engine import ProjectGraph
from backend.rendering.dashboard import render_dashboard
from backend.tests.integration.conftest import HAS_BAZEL
from backend.tests.integration.scenarios._base import ExpectedOutcome
from backend.tools.base import ForgeTool
from backend.workspace.scanner import WorkspaceState, scan_workspace
from backend.workspace.trace_parser import analyse_traces


def _load_scenario(name: str) -> tuple[Any, ExpectedOutcome, Any]:
    """Import a scenario's graph builder, expected outcome, and optional seed."""
    graph_mod = importlib.import_module(
        f"backend.tests.integration.scenarios.{name}.graph"
    )
    expected_mod = importlib.import_module(
        f"backend.tests.integration.scenarios.{name}.expected"
    )
    try:
        seed_mod = importlib.import_module(
            f"backend.tests.integration.scenarios.{name}.seed"
        )
    except ModuleNotFoundError:
        seed_mod = None
    return graph_mod, expected_mod.EXPECTED, seed_mod


# ── Phase 11 assertions ─────────────────────────────────────────────────────


def assert_docs_rendered(
    written: list[Path],
    workspace: Path,
    expected: ExpectedOutcome,
) -> None:
    """Verify Phase 11 rendered the expected docs."""
    assert len(written) >= expected.doc_count, (
        f"Expected at least {expected.doc_count} docs, got {len(written)}"
    )

    all_doc_text = ""
    docs_dir = workspace / "docs"
    if docs_dir.exists():
        for md in docs_dir.rglob("*.md"):
            all_doc_text += md.read_text()

    for node_id in expected.required_doc_node_ids:
        assert node_id in all_doc_text, (
            f"Node {node_id} not found in rendered docs"
        )


# ── Phase 12 assertions ─────────────────────────────────────────────────────


def assert_source_files(
    workspace: Path,
    expected: ExpectedOutcome,
) -> None:
    """Verify source files were generated."""
    src_files = [
        f for f in (workspace / "src").rglob("*.py")
        if f.name != "__init__.py"
    ]
    assert len(src_files) >= expected.min_source_files, (
        f"Expected >= {expected.min_source_files} source files, "
        f"got {len(src_files)}: {[f.name for f in src_files]}"
    )


def assert_test_files(
    workspace: Path,
    expected: ExpectedOutcome,
) -> None:
    """Verify test files were generated."""
    test_files = list((workspace / "tests").rglob("test_*.py"))
    assert len(test_files) >= expected.min_test_files, (
        f"Expected >= {expected.min_test_files} test files, "
        f"got {len(test_files)}: {[f.name for f in test_files]}"
    )


def assert_all_llrs_traced(
    workspace: Path,
    expected: ExpectedOutcome,
) -> None:
    """Verify all required LLR IDs appear in @traces decorators."""
    all_traced: set[str] = set()
    for py_file in (workspace / "src").rglob("*.py"):
        analysis = analyse_traces(py_file.read_text())
        for lt in analysis.traces:
            all_traced.update(lt.llr_ids)

    for llr_id in expected.required_llr_ids:
        assert llr_id in all_traced, (
            f"LLR {llr_id} not traced in any source file. "
            f"Found traces: {all_traced}"
        )


def assert_no_dead_code(workspace: Path, expected: ExpectedOutcome) -> None:
    """Verify no untraced functions exist in source files."""
    if not expected.no_dead_code:
        return
    dead: list[str] = []
    for py_file in (workspace / "src").rglob("*.py"):
        if py_file.name == "__init__.py":
            continue
        analysis = analyse_traces(py_file.read_text())
        for uf in analysis.untraced:
            dead.append(f"{py_file.name}:{uf.name}")
    assert not dead, f"Dead code (untraced functions): {dead}"


def assert_no_scope_creep(workspace: Path, expected: ExpectedOutcome) -> None:
    """Verify no unrequired functions exist — trace quality check."""
    # Check function count limit
    if expected.max_source_functions is not None:
        total_funcs = 0
        for py_file in (workspace / "src").rglob("*.py"):
            if py_file.name == "__init__.py":
                continue
            analysis = analyse_traces(py_file.read_text())
            total_funcs += analysis.total_functions
        assert total_funcs <= expected.max_source_functions, (
            f"Expected <= {expected.max_source_functions} source functions, "
            f"got {total_funcs} — likely scope creep"
        )

    # Check forbidden function names
    if expected.forbidden_function_names:
        found: list[str] = []
        for py_file in (workspace / "src").rglob("*.py"):
            if py_file.name == "__init__.py":
                continue
            analysis = analyse_traces(py_file.read_text())
            all_names = [t.symbol for t in analysis.traces] + [u.name for u in analysis.untraced]
            for name in all_names:
                for forbidden in expected.forbidden_function_names:
                    if forbidden in name.lower():
                        found.append(f"{py_file.name}:{name}")
        assert not found, (
            f"Scope creep: functions with forbidden names found: {found}"
        )


def assert_no_grid_bfs_patterns(workspace: Path, expected: ExpectedOutcome) -> None:
    """Verify source code doesn't contain BFS grid-cell traversal patterns.

    Checks for structural patterns that indicate non-kinematic path search:
    - deque-based BFS (from collections import deque + neighbor iteration)
    - 8-connected grid neighbor lists without motion primitives
    - Functions that return paths without using get_primitives()
    """
    if not expected.forbidden_function_names:
        return  # Only run if scenario specifies forbidden patterns

    import ast
    import re

    bfs_indicators: list[str] = []

    for py_file in (workspace / "src").rglob("*.py"):
        if py_file.name == "__init__.py":
            continue
        code = py_file.read_text(encoding="utf-8")

        # Check for deque-based BFS patterns in path-returning functions
        # (deque is fine for heuristic computation, but not for path search)
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            func_code = ast.get_source_segment(code, node) or ""
            func_name = node.name

            # Skip heuristic functions — they legitimately use grid BFS
            if "heuristic" in func_name.lower() or "compute" == func_name:
                continue

            # Check if this function uses deque + grid neighbors for path search
            has_deque = "deque" in func_code
            has_grid_neighbors = bool(re.search(
                r"\(\s*-1\s*,\s*0\s*\).*\(\s*1\s*,\s*0\s*\).*\(\s*0\s*,\s*-1\s*\)",
                func_code, re.DOTALL,
            ))
            returns_path = "return" in func_code and "path" in func_name.lower()

            if has_deque and has_grid_neighbors and returns_path:
                bfs_indicators.append(
                    f"{py_file.name}:{func_name} uses deque + grid neighbors "
                    f"for path search (violates kinematic constraint)"
                )

    assert not bfs_indicators, (
        "BFS grid-cell path patterns found in source:\n"
        + "\n".join(f"  - {i}" for i in bfs_indicators)
    )


def assert_all_tests_pass(state: WorkspaceState) -> None:
    """Verify every test function passed."""
    failed = [
        r.test_id for r in state.test_results
        if r.status in ("failed", "error")
    ]
    assert not failed, f"Failing tests: {failed}"
    assert len(state.test_results) > 0, "No test results found"


def assert_statement_coverage(
    state: WorkspaceState,
    expected: ExpectedOutcome,
) -> None:
    """Verify statement (line) coverage meets threshold."""
    if state.coverage_pct is None:
        pytest.skip("Statement coverage data not available")
    assert state.coverage_pct >= expected.min_statement_coverage, (
        f"Statement coverage {state.coverage_pct:.1f}% "
        f"< {expected.min_statement_coverage}%"
    )


def assert_branch_coverage(
    state: WorkspaceState,
    expected: ExpectedOutcome,
) -> None:
    """Verify branch coverage meets threshold."""
    if state.branch_coverage_pct is None:
        # No branches in the code — that's fine, not a skip
        return
    assert state.branch_coverage_pct >= expected.min_branch_coverage, (
        f"Branch coverage {state.branch_coverage_pct:.1f}% "
        f"< {expected.min_branch_coverage}%"
    )


def assert_gaps_resolved(result: CodeGenResult, expected: ExpectedOutcome) -> None:
    """Verify gaps_resolved matches expectation."""
    if expected.gaps_resolved:
        assert result.gaps_resolved, "Expected all gaps resolved but some remain"


def assert_bazel_tests_pass(
    workspace: Path,
    expected: ExpectedOutcome,
) -> None:
    """Run bazel test independently and verify it passes."""
    if not expected.bazel_tests_pass:
        return
    if not HAS_BAZEL:
        pytest.skip("bazel not available — skipping bazel assertion")

    result = subprocess.run(
        ["bazel", "test", "//tests/...", "--test_output=errors"],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"bazel test failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


# ── Main parametrised test ───────────────────────────────────────────────────


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.timeout(3600)
@pytest.mark.parametrize("scenario_name", ["calculator", "deadcode", "mcdc", "branches", "coverage_gaps", "scope_creep", "no_grid_fallback"])
async def test_scenario(
    scenario_name: str,
    integration_config: ForgeConfig,
    scenario_workspace: Path,
    scenario_tools: list[ForgeTool],
) -> None:
    """End-to-end integration test: Phase 11 docs + Phase 12 code gen."""
    # 1. Load scenario
    graph_mod, expected, seed_mod = _load_scenario(scenario_name)

    # 2. Setup graph — DB lives outside workspace so agents can't corrupt it
    db_path = scenario_workspace.parent / f"{scenario_name}_graph.db"
    graph = ProjectGraph(db_path)
    await graph.initialise()
    await graph_mod.build_graph(graph)

    # 3. Phase 11: render docs
    written = await render_dashboard(graph, scenario_workspace)
    assert_docs_rendered(written, scenario_workspace, expected)

    # 4. Seed workspace with pre-existing files (if scenario provides them)
    if seed_mod is not None:
        seed_mod.seed_workspace(scenario_workspace)

    # 5. Phase 12: code gen with real LLM. The graph-bound mission
    # feedback tools are added here (the scenario_tools fixture has no
    # graph) — create_mission_agent refuses to run without them.
    from backend.tools.check_trace_quality import CheckTraceQualityTool
    from backend.tools.evaluate_progress import EvaluateProgressTool
    from backend.tools.workspace_doctor import WorkspaceDoctorTool

    ws = str(scenario_workspace)
    tools = [
        *scenario_tools,
        WorkspaceDoctorTool(ws),
        EvaluateProgressTool(ws, graph),
        CheckTraceQualityTool(ws, graph, integration_config.llm),
    ]
    init_bazel_workspace(scenario_workspace)
    result = await run_code_gen(
        graph,
        scenario_workspace,
        config=integration_config,
        tool_instances=tools,
    )

    # 6. Post-generation workspace scan (coverage + test results)
    state = await scan_workspace(scenario_workspace)

    # 7. Assertions
    assert_source_files(scenario_workspace, expected)
    assert_test_files(scenario_workspace, expected)
    assert_all_llrs_traced(scenario_workspace, expected)
    assert_no_dead_code(scenario_workspace, expected)
    assert_all_tests_pass(state)
    assert_statement_coverage(state, expected)
    assert_branch_coverage(state, expected)
    assert_no_scope_creep(scenario_workspace, expected)
    assert_no_grid_bfs_patterns(scenario_workspace, expected)
    assert_gaps_resolved(result, expected)
    assert_bazel_tests_pass(scenario_workspace, expected)
