"""Gap finder — identify code generation gaps.

Compares workspace state against the project graph to find gaps:
missing source files, missing test files, untraced functions,
failing tests, and coverage shortfalls. Used by the gap-first
code generation pipeline.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.codegen.failure_clustering import (  # noqa: F401 — re-exported for tests
    _build_error_summaries as _build_error_summaries,
)
from backend.codegen.failure_clustering import (
    _check_failing_tests as _check_failing_tests,
)
from backend.codegen.failure_clustering import (
    _fallback_import_check as _fallback_import_check,
)
from backend.codegen.failure_clustering import (
    _partition_dep_errors as _partition_dep_errors,
)
from backend.codegen.failure_clustering import (
    _report_dep_error_clusters as _report_dep_error_clusters,
)
from backend.codegen.failure_clustering import (
    _report_test_failures as _report_test_failures,
)
from backend.codegen.gap_model import Gap, GapKind
from backend.codegen.naming import slugify as _slugify
from backend.codegen.requirement_coverage import (
    _check_uncovered_requirement as _check_uncovered_requirement,
)
from backend.codegen.requirement_coverage import (
    _check_unimplemented_requirement as _check_unimplemented_requirement,
)
from backend.server.forge_logger import forge_logger

if TYPE_CHECKING:
    from backend.graph.engine import ProjectGraph
    from backend.workspace.scanner import FileState

__all__ = ["Gap", "GapKind", "find_gaps"]


# ── Public API ───────────────────────────────────────────────────────────────


def find_gaps(
    source_files: dict[str, FileState],
    test_files: dict[str, FileState],
    test_results: list[Any],
    graph: ProjectGraph,
    *,
    test_run_error: str = "",
    coverage_by_file: dict[str, float] | None = None,
    uncovered_lines: dict[str, list[int]] | None = None,
    branch_coverage_pct: float | None = None,
) -> list[Gap]:
    """Compare workspace state against the project graph and return gaps.

    Args:
        source_files: Mapping of relative path -> FileState.
        test_files: Mapping of relative path -> FileState.
        test_results: List of SingleTestResult objects.
        graph: The project graph instance (must support ``all_nodes()``).
        test_run_error: Error message if tests couldn't run at all.
        coverage_by_file: Per-file coverage percentages from LCOV.
        uncovered_lines: Per-file list of uncovered line numbers from LCOV.
        branch_coverage_pct: Overall MC/DC branch coverage percentage.

    Returns:
        Gaps sorted by priority (TEST_ENV_BROKEN first).
    """
    gaps: list[Gap] = []

    _check_test_env(gaps, test_run_error)
    _check_syntax_errors(gaps, source_files, test_files)
    _check_missing_sources(gaps, source_files, graph)
    _check_missing_tests(gaps, test_files, graph)
    _check_failing_tests(gaps, test_results)
    _check_invalid_traces(gaps, source_files, test_files, graph)
    _check_untraced_functions(gaps, source_files, test_files)
    _check_low_structural_coverage(
        gaps, source_files, coverage_by_file, uncovered_lines,
    )
    _check_low_branch_coverage(gaps, branch_coverage_pct)
    _check_unimplemented_requirement(gaps, source_files, graph)
    _check_uncovered_requirement(gaps, test_files, test_results, graph)

    # Quality checks (scope creep, suspicious names) only run once the
    # implementation is structurally complete — no point flagging scope
    # creep while the agent is still building, and we avoid wasting
    # effort writing tests for functions that will be removed.
    if not gaps:
        forge_logger.emit("INFO", "GAPF ", "Structural gaps clear — running quality checks")
        _check_suspicious_names(gaps, source_files, graph)

    gaps.sort(key=lambda g: g.kind.value)

    forge_logger.emit(
        "INFO", "GAPF ",
        f"Found {len(gaps)} gap(s): "
        + ", ".join(f"{k.name}={c}" for k, c in _gap_counts(gaps).items()),
    )
    return gaps


# ── Gap checkers ─────────────────────────────────────────────────────────────




def _check_missing_sources(
    gaps: list[Gap],
    source_files: dict[str, Any],
    graph: ProjectGraph,
) -> None:
    """Add MISSING_SOURCE gaps for DESIGN nodes without a generated file."""
    for node in graph.all_nodes():
        if node.node_type != "DESIGN":
            continue
        expected = _target_path(node, "source")
        if expected not in source_files:
            gaps.append(Gap(
                kind=GapKind.MISSING_SOURCE,
                node_id=node.node_id,
                file_path=expected,
                details=f"Source file missing for DESIGN node {node.node_id}",
            ))


def _check_missing_tests(
    gaps: list[Gap],
    test_files: dict[str, Any],
    graph: ProjectGraph,
) -> None:
    """Add MISSING_TEST gaps for CASE nodes without a generated test file."""
    for node in graph.all_nodes():
        if node.node_type not in ("CASE_HLR", "CASE_LLR"):
            continue
        expected = _target_path(node, "test")
        if expected not in test_files:
            gaps.append(Gap(
                kind=GapKind.MISSING_TEST,
                node_id=node.node_id,
                file_path=expected,
                details=f"Test file missing for {node.node_type} node {node.node_id}",
            ))


def _check_invalid_traces(
    gaps: list[Gap],
    source_files: dict[str, FileState],
    test_files: dict[str, FileState],
    graph: ProjectGraph,
) -> None:
    """Add INVALID_TRACES gaps for files with annotations referencing bad LLR IDs."""
    valid_llr_ids: set[str] = set()
    for node in graph.all_nodes():
        if node.node_type == "LLR":
            valid_llr_ids.add(node.node_id)

    if not valid_llr_ids:
        return

    all_files: dict[str, FileState] = {**source_files, **test_files}
    for path, file_state in all_files.items():
        invalid_ids: list[str] = []
        for trace in file_state.traces:
            for llr_id in trace.llr_ids:
                if llr_id not in valid_llr_ids:
                    invalid_ids.append(llr_id)
        if invalid_ids:
            unique_bad = sorted(set(invalid_ids))
            gaps.append(Gap(
                kind=GapKind.INVALID_TRACES,
                node_id="",
                file_path=path,
                details=(
                    f"{len(unique_bad)} invalid LLR ID(s) in annotations: "
                    f"{', '.join(unique_bad)}"
                ),
                context={"invalid_llr_ids": unique_bad},
            ))


def _check_untraced_functions(
    gaps: list[Gap],
    source_files: dict[str, FileState],
    test_files: dict[str, FileState],
) -> None:
    """Add UNTRACED_FUNCTIONS gaps for files containing untraced functions.

    Every function in src/ — including private helpers — must have a
    ``@traces`` decorator.  A private helper inherits the LLR of the
    public function it supports, but the trace must still be explicit
    for safety-critical traceability (DO-178C: every line of code maps
    to a requirement).
    """
    all_files: dict[str, FileState] = {**source_files, **test_files}
    for path, file_state in all_files.items():
        if not file_state.untraced_functions:
            continue
        names = [uf.name for uf in file_state.untraced_functions]
        if not names:
            continue
        gaps.append(Gap(
            kind=GapKind.UNTRACED_FUNCTIONS,
            node_id="",
            file_path=path,
            details=f"{len(names)} untraced function(s): {', '.join(names)}",
            context={"untraced_functions": names},
        ))


def _check_low_structural_coverage(
    gaps: list[Gap],
    source_files: dict[str, FileState],
    coverage_by_file: dict[str, float] | None,
    uncovered_lines: dict[str, list[int]] | None = None,
) -> None:
    """Add LOW_STRUCTURAL_COVERAGE gaps for source files with < 100% coverage."""
    if not coverage_by_file:
        return
    for path in source_files:
        pct = coverage_by_file.get(path)
        if pct is not None and pct < 100.0:
            missing = (uncovered_lines or {}).get(path, [])
            gaps.append(Gap(
                kind=GapKind.LOW_STRUCTURAL_COVERAGE,
                node_id="",
                file_path=path,
                details=f"Coverage {pct:.0f}% — uncovered lines: {missing}",
                context={
                    "coverage_pct": pct,
                    "uncovered_lines": missing,
                },
            ))


def _check_low_branch_coverage(
    gaps: list[Gap],
    branch_coverage_pct: float | None,
) -> None:
    """Add LOW_BRANCH_COVERAGE gap when MC/DC is below 100%.

    DO-178C DAL-B requires 100% MC/DC. Any value below 100% is a
    certification blocker. The agent should either write tests that
    exercise missing branches or refactor the source to make branches
    testable.
    """
    if branch_coverage_pct is None:
        return
    if branch_coverage_pct >= 100.0:
        return
    gaps.append(Gap(
        kind=GapKind.LOW_BRANCH_COVERAGE,
        node_id="",
        file_path="src/",
        details=(
            f"MC/DC branch coverage is {branch_coverage_pct:.1f}% — "
            f"100% required for DO-178C certification. "
            f"Write tests that exercise uncovered boolean conditions, "
            f"or refactor source to make branches testable."
        ),
        context={"branch_coverage_pct": branch_coverage_pct},
    ))


# Patterns that indicate scope creep unless explicitly required
_SUSPICIOUS_PATTERNS = [
    "fallback", "retry", "cache", "backup", "workaround",
    "alternative", "default_value", "get_or_default",
]


def _check_suspicious_names(
    gaps: list[Gap],
    source_files: dict[str, FileState],
    graph: ProjectGraph,
) -> None:
    """Rule-based check: flag functions with suspicious names.

    If a function name contains a pattern like 'fallback' or 'retry',
    check whether ANY requirement explicitly mentions that word. If not,
    it's scope creep.
    """
    llr_texts = {
        n.node_id: (n.content or n.title or "").strip()
        for n in graph.all_nodes()
        if n.node_type == "LLR"
    }
    if not llr_texts:
        return

    all_req_text = " ".join(llr_texts.values()).lower()

    for path, file_state in source_files.items():
        for t in file_state.traces:
            if not t.symbol:
                continue
            name_lower = t.symbol.lower()
            for pattern in _SUSPICIOUS_PATTERNS:
                if pattern in name_lower and pattern not in all_req_text:
                    gaps.append(Gap(
                        kind=GapKind.SCOPE_CREEP,
                        node_id="",
                        file_path=path,
                        details=(
                            f"Function `{t.symbol}` name contains '{pattern}' "
                            f"but no requirement mentions '{pattern}'. "
                            f"This is likely unrequired code — remove it."
                        ),
                        context={
                            "function_name": t.symbol,
                            "rationale": f"Name contains '{pattern}', not in any LLR",
                            "rule": "suspicious_name",
                        },
                    ))
                    break  # one gap per function


def _check_test_env(gaps: list[Gap], test_run_error: str) -> None:
    """Add a TEST_ENV_BROKEN gap if tests cannot run at all."""
    if not test_run_error:
        return
    gaps.append(Gap(
        kind=GapKind.TEST_ENV_BROKEN,
        node_id="",
        file_path="",
        details=test_run_error,
        context={"test_run_error": test_run_error},
    ))


def _check_syntax_errors(
    gaps: list[Gap],
    source_files: dict[str, FileState],
    test_files: dict[str, FileState],
) -> None:
    """Add SYNTAX_ERROR gaps for files that fail to parse as Python.

    Syntax errors are caught early (during file scan, before bazel test)
    so the agent gets fast, precise feedback instead of waiting for a
    full test run to discover the problem.
    """
    all_files: dict[str, FileState] = {**source_files, **test_files}
    for path, file_state in all_files.items():
        if file_state.syntax_error:
            gaps.append(Gap(
                kind=GapKind.SYNTAX_ERROR,
                node_id="",
                file_path=path,
                details=f"Syntax error in {path}: {file_state.syntax_error}",
                context={"syntax_error": file_state.syntax_error},
            ))


# ── Helpers ──────────────────────────────────────────────────────────────────


def _target_path(node: Any, kind: str) -> str:
    """Derive the relative output path from the node title."""
    slug = _slugify(node.title or node.node_id)
    if kind == "test":
        return f"tests/test_{slug}.py"
    return f"src/{slug}.py"




def _gap_counts(gaps: list[Gap]) -> dict[GapKind, int]:
    """Count gaps by kind for logging."""
    counts: dict[GapKind, int] = {}
    for g in gaps:
        counts[g.kind] = counts.get(g.kind, 0) + 1
    return counts
