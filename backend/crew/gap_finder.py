"""Gap finder — identify code generation gaps.

Compares workspace state against the project graph to find gaps:
missing source files, missing test files, untraced functions,
failing tests, and coverage shortfalls. Used by the gap-first
code generation pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Any

from backend.crew.naming import slugify as _slugify
from backend.server.forge_logger import forge_logger

if TYPE_CHECKING:
    from backend.crew.workspace_scanner import FileState
    from backend.graph.engine import ProjectGraph


# ── Data models ──────────────────────────────────────────────────────────────


class GapKind(IntEnum):
    """Gap categories ordered by priority (lower = higher priority).

    TEST_ENV_BROKEN is first because if the environment is broken,
    no other verification is meaningful.  SYNTAX_ERROR is next because
    a file with a syntax error cannot be imported or tested — fixing
    it unblocks all downstream checks.
    """

    TEST_ENV_BROKEN = 0
    SYNTAX_ERROR = 1              # file has a Python syntax error
    MISSING_SOURCE = 2
    MISSING_TEST = 3
    FAILING_TESTS = 4
    INVALID_TRACES = 5
    UNTRACED_FUNCTIONS = 6
    LOW_STRUCTURAL_COVERAGE = 7   # statement coverage < 100% for a file
    LOW_BRANCH_COVERAGE = 8      # MC/DC branch coverage < 100%
    UNIMPLEMENTED_REQUIREMENT = 9  # LLR absent from all source-file @traces
    UNCOVERED_REQUIREMENT = 10    # LLR with no passing test evidence
    WEAK_TRACE = 11              # function traces to LLR but doesn't implement it
    SCOPE_CREEP = 12             # function not backed by any requirement


@dataclass
class Gap:
    """A single code-generation gap detected in the workspace."""

    kind: GapKind
    node_id: str
    file_path: str
    details: str
    context: dict[str, Any] = field(default_factory=dict)


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


def _check_failing_tests(
    gaps: list[Gap],
    test_results: list[Any],
) -> None:
    """Add FAILING_TESTS gaps grouped by file.

    Import/dependency errors that share the same root module are
    clustered into a single TEST_ENV_BROKEN gap with a fix hint,
    instead of N separate FAILING_TESTS gaps. Uses the build
    environment protocol for language-agnostic detection.
    """
    dep_errors, other_failures = _partition_dep_errors(test_results)
    _report_dep_error_clusters(gaps, dep_errors)
    _report_test_failures(gaps, other_failures)


def _partition_dep_errors(
    test_results: list[Any],
) -> tuple[list[tuple[Any, str]], list[Any]]:
    """Split failures into dependency errors vs other test failures.

    Returns (dep_errors_with_module, other_failures).
    """
    from backend.crew.build_env import detect_build_environment

    # Try to detect the build environment for smart error classification
    build_env = None
    try:
        import os
        ws = os.environ.get("FORGE_WORKSPACE", "")
        if ws:
            from pathlib import Path
            build_env = detect_build_environment(Path(ws))
    except Exception:  # noqa: BLE001
        pass

    dep_errors: list[tuple[Any, str]] = []
    other_failures: list[Any] = []
    for result in test_results:
        if result.status not in ("failed", "error"):
            continue
        msg = (getattr(result, "error_message", "") or "") + (getattr(result, "error_detail", "") or "")
        module = build_env.is_import_error(msg) if build_env else _fallback_import_check(msg)
        if module:
            dep_errors.append((result, module))
        else:
            other_failures.append(result)
    return dep_errors, other_failures


def _fallback_import_check(msg: str) -> str | None:
    """Fallback import error detection when no build env is detected."""
    import re
    if "ModuleNotFoundError" not in msg and "ImportError" not in msg:
        return None
    match = re.search(r"No module named '([^']+)'", msg)
    return match.group(1).split(".")[0] if match else None


def _report_dep_error_clusters(
    gaps: list[Gap], dep_errors: list[tuple[Any, str]],
) -> None:
    """Cluster dependency errors by missing module into TEST_ENV_BROKEN gaps."""
    from backend.crew.build_env import detect_build_environment

    build_env = None
    try:
        import os
        ws = os.environ.get("FORGE_WORKSPACE", "")
        if ws:
            from pathlib import Path
            build_env = detect_build_environment(Path(ws))
    except Exception:  # noqa: BLE001
        pass

    clusters: dict[str, list[str]] = {}
    for result, module in dep_errors:
        clusters.setdefault(module, []).append(result.file_path or result.test_id)

    manifest = build_env.manifest_file() if build_env else "requirements.txt"
    for module, files in clusters.items():
        unique_files = sorted(set(files))
        fix = build_env.fix_hint_for_missing_dep(module) if build_env else f"Add '{module}' to {manifest}"
        gaps.append(Gap(
            kind=GapKind.TEST_ENV_BROKEN,
            node_id="",
            file_path=manifest,
            details=f"{len(files)} test(s) across {len(unique_files)} file(s) fail with missing dependency '{module}'. {fix}",
            context={
                "missing_module": module,
                "affected_files": unique_files,
                "affected_count": len(files),
            },
        ))


def _report_test_failures(
    gaps: list[Gap], failures: list[Any],
) -> None:
    """Add FAILING_TESTS gaps for non-dependency failures, grouped by file."""
    by_file: dict[str, list[Any]] = {}
    for result in failures:
        by_file.setdefault(result.file_path, []).append(result)

    for file_path, file_failures in by_file.items():
        test_ids = [r.test_id for r in file_failures]
        error_summaries = _build_error_summaries(file_failures)
        gaps.append(Gap(
            kind=GapKind.FAILING_TESTS,
            node_id="",
            file_path=file_path,
            details=f"{len(file_failures)} failing test(s)",
            context={
                "test_ids": test_ids,
                "failing_count": len(file_failures),
                "error_summaries": error_summaries,
            },
        ))


def _build_error_summaries(failures: list[Any]) -> list[str]:
    """Build rich per-test error summaries for the agent prompt.

    Includes the full traceback so the agent can trace the root cause
    through exception chains, broad except blocks, and internal errors.
    """
    summaries: list[str] = []
    for r in failures:
        msg = getattr(r, "error_message", "") or ""
        detail = getattr(r, "error_detail", "") or ""
        label = r.test_id
        if detail:
            # Include the full traceback — agents need the complete
            # chain to diagnose issues like swallowed exceptions
            lines = [ln for ln in detail.splitlines() if ln.strip()]
            summaries.append(f"{label}: {msg}\n  " + "\n  ".join(lines))
        elif msg:
            summaries.append(f"{label}: {msg}")
        else:
            summaries.append(f"{label}: (no error detail)")
    return summaries



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


def _check_unimplemented_requirement(
    gaps: list[Gap],
    source_files: dict[str, FileState],
    graph: ProjectGraph,
) -> None:
    """Add UNIMPLEMENTED_REQUIREMENT gaps for LLRs with no source ``@traces``.

    An LLR is *implemented* iff at least one source-file function carries
    a ``@traces`` annotation citing it. This is the source-side leg of the
    single coverage definition (design/22): a passing traced test alone is
    NOT coverage. Without this check, an LLR with no implementing code
    passed every completion gate — the live run reported "Req 53/53"
    while 15 LLRs never reached src/.
    """
    implemented: set[str] = {
        llr_id
        for file_state in source_files.values()
        for trace in file_state.traces
        for llr_id in trace.llr_ids
    }

    for node in graph.all_nodes():
        if node.node_type != "LLR":
            continue
        if node.node_id in implemented:
            continue
        shall = (node.content or "").strip().replace("\n", " ")
        if len(shall) > 240:
            shall = shall[:240] + "…"
        gaps.append(Gap(
            kind=GapKind.UNIMPLEMENTED_REQUIREMENT,
            node_id=node.node_id,
            file_path="",
            details=(
                f'{node.node_id} content: "{shall}" '
                f'No source function carries @traces("{node.node_id}"). '
                f'Fix: implement this requirement in src/ and annotate the '
                f'implementing function(s) with @traces("{node.node_id}").'
            ),
        ))


def _check_uncovered_requirement(
    gaps: list[Gap],
    test_files: dict[str, FileState],
    test_results: list[Any],
    graph: ProjectGraph,
) -> None:
    """Add UNCOVERED_REQUIREMENT gaps for LLRs with no passing test evidence.

    An LLR is 'covered' iff a *specific test function* that passed carries
    a ``@traces`` decorator listing it. Strict per-function match — no
    file-level fallback. A file-level fallback (previously enabled for
    bazel stubs that omit per-function detail) would let the mission
    agent declare "done" for LLRs that no specific passing test actually
    cites, while the coverage gate (which is strict) still blocks. The
    two must use the same definition for the mission to converge.
    """
    # Map (path, base_function_name) -> True if ANY parametrised variant passed.
    # pytest names parametrised cases as ``test_foo[param0]``, but the
    # ``@traces`` decorator is on the bare function ``test_foo``. We strip the
    # parameterisation suffix so traces on the base name match any passing
    # variant. A function is considered "passing" iff at least one of its
    # parametrisations passed and none failed.
    import re as _re
    _param_re = _re.compile(r"\[.*\]$")

    def _base(name: str) -> str:
        return _param_re.sub("", name) if name else name

    passed_bases: set[tuple[str, str]] = set()
    failed_bases: set[tuple[str, str]] = set()
    for result in test_results:
        if not result.function_name:
            continue
        key = (result.file_path, _base(result.function_name))
        if result.status == "passed":
            passed_bases.add(key)
        elif result.status in ("failed", "error"):
            failed_bases.add(key)
    # Only trust a function as "passing" if no variant failed.
    passing_fns = passed_bases - failed_bases

    covered_llrs: set[str] = set()
    for path, file_state in test_files.items():
        for trace in file_state.traces:
            if (path, trace.symbol) in passing_fns:
                covered_llrs.update(trace.llr_ids)

    # Pre-index CASE_LLR trace_to → LLR so each gap can cite the planned CASE.
    case_llr_for: dict[str, list[str]] = {}
    for case in graph.all_nodes():
        if case.node_type != "CASE_LLR":
            continue
        for llr_id in (case.trace_to or []):
            case_llr_for.setdefault(llr_id, []).append(case.node_id)

    for node in graph.all_nodes():
        if node.node_type != "LLR":
            continue
        if node.node_id in covered_llrs:
            continue
        shall = (node.content or "").strip().replace("\n", " ")
        if len(shall) > 240:
            shall = shall[:240] + "…"
        linked_cases = case_llr_for.get(node.node_id, [])
        case_hint = (
            f" Linked test case(s): {', '.join(linked_cases)}."
            if linked_cases else " No linked CASE_LLR — design a direct test."
        )
        gaps.append(Gap(
            kind=GapKind.UNCOVERED_REQUIREMENT,
            node_id=node.node_id,
            file_path="",
            details=(
                f'{node.node_id} content: "{shall}"{case_hint} '
                f'Fix: write (or reuse) a passing test function that exercises '
                f'this behaviour and carries @traces("{node.node_id}") on the '
                f'test function itself.'
            ),
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
