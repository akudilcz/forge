"""Code Gen — Phase 12 vertical-slice source code and test generation.

Uses a plan -> slice -> verify pipeline:
1. An LLM planner reads the graph and produces an ordered slice plan.
2. For each DESIGN node (in plan order), a single agent completes
   the full vertical slice: source -> test -> pass -> trace.
3. After each slice, a lightweight gap scan verifies completeness.
4. Post-loop: tidy-up, persist traces, audit, record results.

Idempotent: re-running on a complete workspace finds no gaps per
slice and exits immediately.

Design reference: design/22_phase_12_generate_code.md
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.graph.engine import ProjectGraph

from backend.codegen.bazel_gen import init_bazel_workspace
from backend.codegen.gap_finder import Gap, GapKind, find_gaps
from backend.codegen.helpers import (
    compute_requirement_coverage_detail as _compute_requirement_coverage_detail,
)
from backend.codegen.helpers import (
    find_available_modules,
    has_broken_imports,
    has_syntax_error,
)
from backend.codegen.helpers import (
    find_graph_orphans as _find_graph_orphans,  # noqa: F401 — re-exported for tests
)
from backend.codegen.helpers import (
    strip_markdown_fences as _strip_markdown_fences,  # noqa: F401 — re-exported for tests
)
from backend.codegen.naming import slugify as _slugify
from backend.codegen.post_gen import (  # noqa: F401 — re-exported for tests
    _persist_coverage_metrics as _persist_coverage_metrics,
)
from backend.codegen.post_gen import (
    _run_trace_audit as _run_trace_audit,
)
from backend.codegen.trace_persistence import (  # noqa: F401 — re-exported for callers/tests
    _owning_contract_content as _owning_contract_content,
)
from backend.codegen.trace_persistence import (
    _persist_single_file as _persist_single_file,
)
from backend.codegen.trace_persistence import (
    _persist_traces as _persist_traces,
)
from backend.codegen.trace_persistence import (
    _stamp_codegen_error as _stamp_codegen_error,
)
from backend.codegen.trace_persistence import (
    codegen_hash as codegen_hash,
)
from backend.config.models import ForgeConfig
from backend.server.forge_logger import forge_logger
from backend.workspace.result_recorder import is_not_passing, is_passed
from backend.workspace.trace_parser import LineTrace, UntracedFunction, analyse_traces

logger = logging.getLogger(__name__)


@dataclass
class GeneratedFile:
    """One generated source or test file with its trace map."""

    node_id: str
    file_path: str
    line_traces: list[LineTrace] = field(default_factory=list)
    untraced_functions: list[UntracedFunction] = field(default_factory=list)
    total_functions: int = 0
    traced_functions: int = 0


@dataclass
class CodeGenResult:
    """Aggregate result of Phase 12 code generation."""

    source_files: list[GeneratedFile] = field(default_factory=list)
    test_files: list[GeneratedFile] = field(default_factory=list)
    gaps_resolved: bool = False


async def run_code_gen(
    graph: ProjectGraph,
    workspace: Path,
    *,
    config: ForgeConfig,
    tool_instances: list[Any],
) -> CodeGenResult:
    """Run work-queue-driven code generation for Phase 12.

    ``config`` and ``tool_instances`` are required — a missing tool set
    previously degraded silently into a mission agent with zero tools
    (design/22, Required tools).
    """
    t0 = time.monotonic()
    forge_logger.emit("INFO", "CGEN ", f"Phase 12 Code Gen started — workspace={workspace}")

    _init_workspace(workspace)
    _remove_broken_files(workspace)
    init_bazel_workspace(workspace)

    forge_logger.emit("INFO", "CGEN ", "Starting work-queue-driven gap closer")
    last_state, mission_stats = await _close_remaining_gaps(
        workspace, graph, config, tool_instances,
    )
    gaps_resolved = not find_gaps(
        last_state.source_files,
        last_state.test_files,
        last_state.test_results,
        graph,
        test_run_error=last_state.test_run_error,
        coverage_by_file=last_state.coverage_by_file,
        uncovered_lines=last_state.uncovered_lines,
        branch_coverage_pct=last_state.branch_coverage_pct,
    )

    await _tidy_up(workspace)
    result = _build_result(workspace, graph)
    result.gaps_resolved = gaps_resolved
    await _persist_traces(result, graph)
    await _run_trace_audit(result, workspace, graph)

    # RESULT recording happens in phase 13 (after TEST sync) — here we
    # only persist coverage metrics from the final gap-loop state.
    await _persist_coverage_metrics(graph, last_state)

    elapsed = time.monotonic() - t0
    _log_summary(result, last_state, graph, gaps_resolved, elapsed, mission_stats)

    # Strict coverage gate. Earlier behaviour accepted "INCOMPLETE" with
    # sub-100% requirement coverage and advanced to phase 13 silently,
    # allowing the integration test to pass with only ~73% LLR runtime
    # coverage. Per the "fail loudly" invariant, codegen now raises when
    # any of stmt/branch/requirement coverage is below 100% OR any test
    # failed. Downstream phases must see a clean phase-12.
    _enforce_coverage_gate(last_state, graph, result)
    return result


class CodeGenIncompleteError(RuntimeError):
    """Raised when Phase 12 finishes without 100% coverage or all tests green."""


def _enforce_coverage_gate(
    last_state: Any,
    graph: ProjectGraph,
    result: CodeGenResult,
) -> None:
    """Raise ``CodeGenIncompleteError`` if phase 12 outputs are not fully green.

    Hard gates (all must be satisfied):
        * At least one test was executed.
        * Every test passed (``failed == 0``).
        * Statement coverage == 100%.
        * Branch / MC/DC coverage == 100%.
        * Requirement coverage == 100% — every LLR is cited by a source
          ``@traces`` AND has a passing traced test (single coverage
          definition, design/22).
    """
    # Each gate only fires when the thing it covers is actually present:
    # - A graph with no LLRs → no requirement-coverage check (trivially OK).
    # - No source files → no statement/branch check (nothing to cover).
    # - No tests executed → only a problem when the graph has LLRs (every
    #   LLR should have driven a test).
    passed = sum(1 for r in last_state.test_results if is_passed(r.status))
    # Gate semantics: a skipped test is not evidence the behaviour works.
    failed = sum(1 for r in last_state.test_results if is_not_passing(r.status))
    has_source = len(result.source_files) > 0
    stmt_pct = last_state.coverage_pct
    branch_pct = last_state.branch_coverage_pct
    req_detail = _compute_requirement_coverage_detail(last_state, graph)

    problems: list[str] = []
    if failed > 0:
        problems.append(f"{failed} test(s) failed")
    if has_source:
        if stmt_pct is None or stmt_pct < 99.999:
            problems.append(
                f"statement coverage {stmt_pct if stmt_pct is not None else 'n/a'} (need 100%)"
            )
        # branch_pct=None means lcov found zero branches to measure (no
        # boolean logic in source) — that's not a gap, there's simply
        # nothing to cover. Only fail if we have a measured value <100%.
        if branch_pct is not None and branch_pct < 99.999:
            problems.append(
                f"branch/MC-DC coverage {branch_pct} (need 100%)"
            )
    if req_detail["total"] > 0:
        if req_detail["unimplemented"]:
            problems.append(
                f"{len(req_detail['unimplemented'])} LLR(s) have no implementing source "
                f"@traces: {req_detail['unimplemented']}"
            )
        if req_detail["uncovered"]:
            problems.append(
                f"requirement coverage {len(req_detail['covered'])}/{req_detail['total']} — "
                f"uncovered: {req_detail['uncovered']}"
            )
        if passed == 0 and failed == 0:
            problems.append("no tests executed despite LLRs present")

    if problems:
        msg = "Phase 12 coverage gate failed: " + "; ".join(problems)
        forge_logger.emit(
            "ERROR", "CGEN ", msg,
            stmt_pct=int(stmt_pct) if stmt_pct is not None else None,
            branch_pct=int(branch_pct) if branch_pct is not None else None,
            req_covered=len(req_detail["covered"]),
            req_total=req_detail["total"],
            uncovered_llrs=req_detail["uncovered"] or None,
            unimplemented_llrs=req_detail["unimplemented"] or None,
            tests_passed=passed,
            tests_failed=failed,
        )
        raise CodeGenIncompleteError(msg)


def _log_summary(
    result: CodeGenResult,
    last_state: Any,
    graph: ProjectGraph,
    gaps_resolved: bool,
    elapsed: float,
    mission_stats: Any = None,
) -> None:
    """Emit the final Phase 12 summary with detailed statistics + structured
    coverage fields (obs uplift).

    Besides the human-readable one-liner, a second record carries the
    coverage metrics as queryable columns/extras:
        stmt_pct, branch_pct, req_covered, req_total, uncovered_llrs,
        tests_passed, tests_failed, fn_traced, fn_total.
    """
    total_traces = sum(len(g.line_traces) for g in result.source_files + result.test_files)
    fn_traced = sum(g.traced_functions for g in result.source_files)
    fn_total = sum(g.total_functions for g in result.source_files)
    fn_cov = f"{fn_traced}/{fn_total}" if fn_total else "0/0"
    req_detail = _compute_requirement_coverage_detail(last_state, graph)
    req_cov = f"{len(req_detail['covered'])}/{req_detail['total']}"
    stmt_pct = last_state.coverage_pct
    branch_pct = last_state.branch_coverage_pct
    passed = sum(1 for r in last_state.test_results if is_passed(r.status))
    # Gate semantics: a skipped test is not evidence the behaviour works.
    failed = sum(1 for r in last_state.test_results if is_not_passing(r.status))

    stmt = f"Stmt {stmt_pct:.0f}%" if stmt_pct is not None else "Stmt n/a"
    mcdc = f"MC/DC {branch_pct:.0f}%" if branch_pct is not None else "MC/DC n/a"
    status = "complete" if gaps_resolved else "INCOMPLETE (unresolved gaps)"

    forge_logger.emit(
        "INFO",
        "CGEN ",
        f"Phase 12 {status} — {len(result.source_files)} src, "
        f"{len(result.test_files)} test, {total_traces} traces, "
        f"Fn {fn_cov}, {stmt}, Req {req_cov}, {mcdc}, "
        f"{elapsed:.1f}s elapsed",
        # Obs uplift: structured coverage fields (land in extras so queryable
        # via json_extract rather than substring-matching the msg string).
        stmt_pct=int(stmt_pct) if stmt_pct is not None else None,
        branch_pct=int(branch_pct) if branch_pct is not None else None,
        req_covered=len(req_detail["covered"]),
        req_total=req_detail["total"],
        uncovered_llrs=req_detail["uncovered"] or None,
        tests_passed=passed,
        tests_failed=failed,
        fn_traced=fn_traced,
        fn_total=fn_total,
        duration_ms=int(elapsed * 1000),
    )
    # Obs uplift: separate record lists the uncovered LLRs by id (not just
    # a count) so "which requirements are uncovered" is a single filter.
    if req_detail["uncovered"]:
        forge_logger.emit(
            "WARN", "CGEN ",
            f"Uncovered LLRs ({len(req_detail['uncovered'])}): "
            f"{', '.join(req_detail['uncovered'])}",
            uncovered_llrs=req_detail["uncovered"],
        )
    if mission_stats:
        _log_phase_statistics(mission_stats)


def _log_phase_statistics(stats: Any) -> None:
    """Emit detailed statistics for the Phase 12 report."""
    forge_logger.emit("INFO", "CGEN ", "── Phase 12 Detailed Statistics ──")
    forge_logger.emit(
        "INFO",
        "CGEN ",
        f"Tool calls: {stats.total_tool_calls} | "
        f"Wall time: {stats.total_elapsed_s:.1f}s | "
        f"Final score: {stats.final_score:.0%} | "
        f"Remaining gaps: {stats.final_gap_count} | "
        f"Stop reason: {stats.stop_reason}",
    )


def _init_workspace(workspace: Path) -> None:
    """Create directories, seed tracing decorator, and remove stale bazel symlinks."""
    for link in ("bazel-bin", "bazel-out", "bazel-testlogs", "bazel-workspace"):
        p = workspace / link
        if p.is_symlink() and not p.exists():
            p.unlink()

    (workspace / "src").mkdir(parents=True, exist_ok=True)
    (workspace / "tests").mkdir(parents=True, exist_ok=True)
    _seed_tracing_decorator(workspace)
    forge_logger.emit("INFO", "CGEN ", "Workspace initialised")


def _seed_tracing_decorator(workspace: Path) -> None:
    """Copy backend/tracing/ into workspace/tracing/ as a Python package."""
    forge_src = Path(__file__).resolve().parent.parent / "tracing"
    dest_dir = workspace / "tracing"
    dest_dir.mkdir(parents=True, exist_ok=True)

    for src_file in forge_src.glob("*.py"):
        (dest_dir / src_file.name).write_text(src_file.read_text())

    forge_logger.emit("INFO", "CGEN ", "Seeded tracing package -> tracing/")


def _remove_broken_files(workspace: Path) -> None:
    """Remove test files with syntax errors or imports of absent modules."""
    tests_dir = workspace / "tests"
    if not tests_dir.is_dir():
        return

    available = find_available_modules(workspace)
    removed: list[str] = []

    for f in sorted(tests_dir.glob("test_*.py")):
        try:
            code = f.read_text(encoding="utf-8")
        except OSError:
            f.unlink()
            removed.append(f"tests/{f.name}")
            continue

        if has_syntax_error(code) or has_broken_imports(code, available):
            f.unlink()
            removed.append(f"tests/{f.name}")

    if removed:
        forge_logger.emit(
            "INFO",
            "CGEN ",
            f"Removed {len(removed)} broken test file(s): {', '.join(removed[:10])}",
        )


async def _close_remaining_gaps(
    workspace: Path,
    graph: Any,
    config: ForgeConfig,
    tools: list[Any],
    extra_prompt: str = "",
) -> tuple[Any, Any]:
    """Mission-agent-based gap closing. Returns (WorkspaceState, MissionStats)."""
    from backend.codegen.mission_agent import run_mission_agent  # noqa: PLC0415

    return await run_mission_agent(
        workspace,
        graph,
        config,
        tools,
        extra_prompt=extra_prompt,
    )


def _gap_counts(gaps: list[Gap]) -> dict[GapKind, int]:
    """Count gaps by kind for logging."""
    counts: dict[GapKind, int] = {}
    for g in gaps:
        counts[g.kind] = counts.get(g.kind, 0) + 1
    return counts


def _build_result(workspace: Path, graph: ProjectGraph) -> CodeGenResult:
    """Build a CodeGenResult from the current workspace state."""
    result = CodeGenResult()

    node_map: dict[str, str] = {}
    for node in graph.all_nodes():
        if node.node_type == "DESIGN":
            slug = _slugify(node.title or node.node_id)
            node_map[f"src/{slug}.py"] = node.node_id
        elif node.node_type in ("CASE_HLR", "CASE_LLR"):
            slug = _slugify(node.title or node.node_id)
            node_map[f"tests/test_{slug}.py"] = node.node_id

    for py_file in sorted((workspace / "src").rglob("*.py")):
        if py_file.name == "__init__.py":
            continue
        rel = str(py_file.relative_to(workspace))
        node_id = node_map.get(rel, "")
        gen = _read_generated_file(node_id, rel, workspace)
        if gen:
            result.source_files.append(gen)

    for py_file in sorted((workspace / "tests").rglob("*.py")):
        if py_file.name in ("__init__.py", "conftest.py"):
            continue
        rel = str(py_file.relative_to(workspace))
        node_id = node_map.get(rel, "")
        gen = _read_generated_file(node_id, rel, workspace)
        if gen:
            result.test_files.append(gen)

    return result


def _read_generated_file(
    node_id: str,
    rel_path: str,
    workspace: Path,
) -> GeneratedFile | None:
    """Read the generated file and parse its traces."""
    target = workspace / rel_path
    if not target.exists():
        forge_logger.emit("WARN", "CGEN ", f"File missing: {rel_path} (node {node_id})")
        return None

    code = target.read_text(encoding="utf-8")
    analysis = analyse_traces(code)

    def _qualified(t: LineTrace) -> str:
        return f"{t.class_name}.{t.symbol}" if t.class_name else t.symbol

    forge_logger.emit(
        "INFO",
        "CGEN ",
        f"Parsed {rel_path}: {len(analysis.traces)} trace(s), "
        f"{analysis.traced_functions}/{analysis.total_functions} funcs",
        ", ".join(f"{_qualified(t)}:{t.llr_ids}" for t in analysis.traces)
        if analysis.traces
        else None,
    )
    return GeneratedFile(
        node_id=node_id,
        file_path=rel_path,
        line_traces=analysis.traces,
        untraced_functions=analysis.untraced,
        total_functions=analysis.total_functions,
        traced_functions=analysis.traced_functions,
    )


async def _tidy_up(workspace: Path) -> None:
    """Deterministic workspace cleanup -- no LLM agent needed."""
    import shutil  # noqa: PLC0415

    for cache_dir in workspace.rglob("__pycache__"):
        shutil.rmtree(cache_dir, ignore_errors=True)
    for pyc in workspace.rglob("*.pyc"):
        pyc.unlink(missing_ok=True)

    init_py = workspace / "src" / "__init__.py"
    if not init_py.exists() and (workspace / "src").exists():
        init_py.write_text("", encoding="utf-8")

    forge_logger.emit("INFO", "CGEN ", "Tidy-up complete")


