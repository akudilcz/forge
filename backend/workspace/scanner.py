"""Workspace scanner — discover files, traces, and test results.

Scans src/ and tests/ directories, parses LLR/CASE trace annotations,
and runs tests to build a complete picture of the workspace state.
This is the 'eyes' of the gap-first code generation pipeline.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.codegen.bazel_gen import init_bazel_workspace
from backend.server.forge_logger import forge_logger
from backend.workspace.result_recorder import SingleTestResult, purge_stale_test_artifacts
from backend.workspace.test_reports import (
    LcovResult,
    extract_error_summary,
    merge_test_results,
    parse_bazel_testlogs,
    parse_junit_xml,
    parse_lcov_file,
)
from backend.workspace.trace_parser import (
    LineTrace,
    TraceAnalysis,
    UntracedFunction,
    analyse_traces,
)

# Re-export for backwards compatibility
__all__ = ["FileState", "WorkspaceState", "LcovResult", "scan_files", "scan_workspace"]


# ── Data models ─────────────────────────────────────────────────────────────

@dataclass
class FileState:
    """Trace state of a single source or test file."""

    path: str
    traces: list[LineTrace] = field(default_factory=list)
    untraced_functions: list[UntracedFunction] = field(default_factory=list)
    total_functions: int = 0
    traced_functions: int = 0
    syntax_error: str = ""
    #: Top-level API facts for the phase-12 API-surface gate (design/22):
    #: name -> kind ("function" | "class" | "method" for "Class.method",
    #: "import" for names bound by absolute imports).
    symbols: dict[str, str] = field(default_factory=dict)
    #: Relative import statements (verbatim) — each one breaks top-level
    #: importability of src modules and is flagged by the gate.
    relative_imports: list[str] = field(default_factory=list)
    #: Alias-resolved imports: real dotted name -> line numbers
    #: (``import ast as t`` records "ast"; ``from ast import literal_eval``
    #: records "ast.literal_eval"). Consumed by the prohibited-constructs gate.
    imported_modules: dict[str, list[int]] = field(default_factory=dict)
    #: Alias-resolved call targets: dotted name -> line numbers
    #: (``t.parse(x)`` under ``import ast as t`` records "ast.parse").
    call_targets: dict[str, list[int]] = field(default_factory=dict)


@dataclass
class ApiFacts:
    """Static AST facts one file contributes to the phase-12 gates."""

    symbols: dict[str, str] = field(default_factory=dict)
    relative_imports: list[str] = field(default_factory=list)
    imported_modules: dict[str, list[int]] = field(default_factory=dict)
    call_targets: dict[str, list[int]] = field(default_factory=dict)


@dataclass
class WorkspaceState:
    """Complete snapshot of workspace files, traces, tests, and coverage."""

    source_files: dict[str, FileState] = field(default_factory=dict)
    test_files: dict[str, FileState] = field(default_factory=dict)
    test_results: list[SingleTestResult] = field(default_factory=list)
    coverage_pct: float | None = None
    coverage_missing: str = ""
    coverage_by_file: dict[str, float] = field(default_factory=dict)
    uncovered_lines: dict[str, list[int]] = field(default_factory=dict)
    branch_coverage_pct: float | None = None
    test_run_error: str = ""


# ── File scanning ───────────────────────────────────────────────────────────

def _analyse_file(filepath: Path, rel_path: str) -> FileState:
    """Read a file and analyse its trace annotations and API facts."""
    code = filepath.read_text(encoding="utf-8")
    syntax_err = _check_syntax(code, rel_path)
    analysis: TraceAnalysis = analyse_traces(code)
    facts = _collect_api_facts(code)
    return FileState(
        path=rel_path,
        traces=analysis.traces,
        untraced_functions=analysis.untraced,
        total_functions=analysis.total_functions,
        traced_functions=analysis.traced_functions,
        syntax_error=syntax_err,
        symbols=facts.symbols,
        relative_imports=facts.relative_imports,
        imported_modules=facts.imported_modules,
        call_targets=facts.call_targets,
    )


def _collect_api_facts(code: str) -> ApiFacts:
    """Statically collect symbols, imports, and call targets via AST.

    A file that fails to parse yields empty facts; its ``syntax_error``
    gap already blocks the phase, so the gates stay quiet for it.
    """
    import ast  # noqa: PLC0415

    try:
        tree = ast.parse(code)
    except SyntaxError:
        return ApiFacts()

    facts = ApiFacts()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            facts.symbols[node.name] = "function"
        elif isinstance(node, ast.ClassDef):
            facts.symbols[node.name] = "class"
            for member in node.body:
                if isinstance(member, ast.FunctionDef | ast.AsyncFunctionDef):
                    facts.symbols[f"{node.name}.{member.name}"] = "method"
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                facts.relative_imports.append(ast.unparse(node))
            else:
                for alias in node.names:
                    facts.symbols[alias.asname or alias.name] = "import"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                facts.symbols[alias.asname or alias.name.split(".")[0]] = "import"

    aliases = _collect_imports(tree, facts.imported_modules)
    _collect_call_targets(tree, aliases, facts.call_targets)
    return facts


def _collect_imports(tree: Any, imported: dict[str, list[int]]) -> dict[str, str]:
    """Record absolute imports (real dotted name -> lines); return alias map."""
    import ast  # noqa: PLC0415

    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.setdefault(alias.name, []).append(node.lineno)
                bound = alias.asname or alias.name.split(".")[0]
                aliases[bound] = alias.name if alias.asname else bound
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                dotted = f"{node.module}.{alias.name}"
                imported.setdefault(dotted, []).append(node.lineno)
                aliases[alias.asname or alias.name] = dotted
    return aliases


def _collect_call_targets(
    tree: Any, aliases: dict[str, str], targets: dict[str, list[int]],
) -> None:
    """Record every call's alias-resolved dotted target name -> lines."""
    import ast  # noqa: PLC0415

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        parts: list[str] = []
        func = node.func
        while isinstance(func, ast.Attribute):
            parts.append(func.attr)
            func = func.value
        if not isinstance(func, ast.Name):
            continue  # call on a computed expression — not statically named
        root = aliases.get(func.id) or func.id
        parts.append(root)
        dotted = ".".join(reversed(parts))
        targets.setdefault(dotted, []).append(node.lineno)


def _check_syntax(code: str, rel_path: str) -> str:
    """Return a human-readable syntax error message, or empty string if valid."""
    import ast as _ast

    try:
        _ast.parse(code, filename=rel_path)
    except SyntaxError as exc:
        line_info = f" (line {exc.lineno})" if exc.lineno else ""
        return f"{exc.msg}{line_info}: {exc.text.strip()}" if exc.text else f"{exc.msg}{line_info}"
    return ""


def _discover_py_files(directory: Path) -> list[Path]:
    """Find all .py files excluding infrastructure and __pycache__."""
    if not directory.exists():
        return []
    return [
        p for p in sorted(directory.rglob("*.py"))
        if p.name not in ("__init__.py", "conftest.py")
        and "__pycache__" not in p.parts
    ]


def scan_files(
    workspace: Path,
) -> tuple[dict[str, FileState], dict[str, FileState]]:
    """Scan src/ and tests/ for .py files and analyse traces."""
    source_files: dict[str, FileState] = {}
    test_files: dict[str, FileState] = {}

    for py_file in _discover_py_files(workspace / "src"):
        rel = str(py_file.relative_to(workspace))
        source_files[rel] = _analyse_file(py_file, rel)

    for py_file in _discover_py_files(workspace / "tests"):
        rel = str(py_file.relative_to(workspace))
        test_files[rel] = _analyse_file(py_file, rel)

    forge_logger.emit(
        "INFO", "SCAN ",
        f"Scanned {len(source_files)} source, {len(test_files)} test file(s)",
    )
    return source_files, test_files


# ── Test & coverage runner ──────────────────────────────────────────────────

def _run_tests_and_coverage(
    workspace: Path,
) -> tuple[list[SingleTestResult], LcovResult, str]:
    """Run tests via bazel and return (results, lcov, error)."""
    tests_dir = workspace / "tests"
    if not tests_dir.exists() or not any(tests_dir.glob("test_*.py")):
        return [], LcovResult(), ""
    return _run_bazel_tests(workspace)


def _run_bazel_tests(
    workspace: Path,
) -> tuple[list[SingleTestResult], LcovResult, str]:
    """Run tests via bazel test, then collect coverage via coverage.py."""
    try:
        # Regenerate BUILD files so tests written since the last run have
        # targets, then purge stale artifacts so only fresh evidence parses.
        init_bazel_workspace(workspace)
        purge_stale_test_artifacts(workspace)

        forge_logger.emit("INFO", "SCAN ", "Running bazel test //tests/... (up to 10 min)")
        proc = subprocess.run(
            ["bazel", "test", "//tests/...",
             "--test_output=all", "--verbose_failures",
             "--test_timeout=30"],
            cwd=str(workspace),
            capture_output=True, text=True, timeout=600,
        )
        output = proc.stdout + proc.stderr
        results = parse_bazel_testlogs(workspace)

        # Artifacts were purged before the run, so any parsed XML is fresh:
        # nonzero exit with no results means the run produced no evidence.
        if proc.returncode != 0 and not results:
            error_summary = extract_error_summary(output)
            forge_logger.emit("WARN", "SCAN ", f"Bazel tests failed: {error_summary}")
            return [], LcovResult(), error_summary

        lcov = _run_coverage_py(workspace)
        cov_xml = workspace / "coverage-test-results.xml"
        if cov_xml.exists():
            cov_results = parse_junit_xml(cov_xml)
            results = merge_test_results(results, cov_results)

        cov_str = f"{lcov.line_pct:.0f}%" if lcov.line_pct is not None else "n/a"
        forge_logger.emit("INFO", "SCAN ", f"Parsed {len(results)} test result(s), coverage {cov_str}")
        return results, lcov, ""
    except FileNotFoundError:
        error = "bazel not found on PATH"
        forge_logger.emit("WARN", "SCAN ", error)
        return [], LcovResult(), error
    except Exception as exc:  # noqa: BLE001
        error = f"Bazel test run failed: {exc}"
        forge_logger.emit("WARN", "SCAN ", error)
        return [], LcovResult(), error


def _run_coverage_py(workspace: Path) -> LcovResult:
    """Run coverage.py directly for accurate statement + branch coverage.

    Raises loudly on any failure — there is deliberately no fallback to a
    leftover on-disk LCOV report, which would present stale coverage from a
    previous workspace revision as current.
    """
    import shutil

    coverage_bin = shutil.which("coverage")
    if not coverage_bin:
        raise RuntimeError(
            "coverage binary not found on PATH — cannot measure fresh coverage"
        )

    tests_dir = workspace / "tests"
    if not tests_dir.exists() or not any(tests_dir.glob("test_*.py")):
        return LcovResult()

    import os
    env = {**os.environ, "PYTHONPATH": str(workspace / "src")}

    test_count = len(list((workspace / "tests").glob("test_*.py")))
    forge_logger.emit("INFO", "SCAN ", f"Running coverage analysis — {test_count} test file(s)")

    cov_result = _run_coverage_with_progress(coverage_bin, workspace, env)
    if cov_result.returncode != 0:
        # Failing tests give a nonzero rc but still produce fresh coverage
        # data; the failures themselves are reported via the JUnit XML.
        forge_logger.emit(
            "WARN", "SCAN ",
            f"coverage run failed (rc={cov_result.returncode}): "
            + (cov_result.stderr or cov_result.stdout)[:200],
        )

    forge_logger.emit("INFO", "SCAN ", "Exporting coverage report")
    lcov_path = workspace / "coverage.lcov"
    export = subprocess.run(
        [coverage_bin, "lcov", "-o", str(lcov_path)],
        cwd=str(workspace), capture_output=True, text=True, timeout=30,
    )
    if export.returncode != 0:
        raise RuntimeError(
            f"coverage lcov export failed (rc={export.returncode}): "
            + (export.stderr or export.stdout)[:200]
        )
    if not lcov_path.exists():
        raise RuntimeError("coverage.lcov not created by coverage lcov export")
    return parse_lcov_file(lcov_path)


def _run_coverage_with_progress(
    coverage_bin: str, workspace: Path, env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """Run coverage with pytest, logging progress per test file."""

    xml_path = workspace / "coverage-test-results.xml"
    cmd = [
        coverage_bin, "run", "--branch", "--source=src",
        "-m", "pytest", "tests/", "--maxfail=5", "-v", "--no-header", "--timeout=10",
        f"--junitxml={xml_path}",
    ]
    proc = subprocess.Popen(
        cmd, cwd=str(workspace),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, env=env,
    )

    output_lines, passed, failed = _collect_coverage_output(proc)

    proc.wait(timeout=30)
    forge_logger.emit(
        "INFO", "SCAN ",
        f"Coverage run complete: {passed} passed, {failed} failed",
    )

    return subprocess.CompletedProcess(
        args=cmd, returncode=proc.returncode,
        stdout="\n".join(output_lines), stderr="",
    )


def _collect_coverage_output(
    proc: subprocess.Popen[str],
) -> tuple[list[str], int, int]:
    """Read coverage subprocess output, log progress. Returns (lines, passed, failed)."""
    if proc.stdout is None:
        raise RuntimeError("coverage subprocess must be started with stdout=PIPE")

    output_lines: list[str] = []
    passed = 0
    failed = 0

    for line in proc.stdout:
        line = line.rstrip()
        output_lines.append(line)
        if " PASSED" in line:
            passed += 1
            short = line.split("::")[1].split(" ")[0] if "::" in line else line[:60]
            forge_logger.emit("INFO", "COV  ", f"PASS {short} ({passed}P/{failed}F)")
        elif " FAILED" in line:
            failed += 1
            short = line.split("::")[1].split(" ")[0] if "::" in line else line[:60]
            forge_logger.emit("WARN", "COV  ", f"FAIL {short} ({passed}P/{failed}F)")
        elif " ERROR" in line:
            failed += 1
            forge_logger.emit("ERROR", "COV  ", f"{line[:80]} ({passed}P/{failed}F)")

    # Obs uplift: one terminal summary record with structured pass/fail counts
    # so "did this test run go green?" is a single query, not a reconstruction
    # from the running (NP/NF) counters on every per-test record.
    level = "INFO" if failed == 0 else "WARN"
    forge_logger.emit(
        level, "COV  ",
        f"Test run complete — {passed} passed, {failed} failed",
        tests_passed=passed,
        tests_failed=failed,
    )
    return output_lines, passed, failed


# ── Main entry point ────────────────────────────────────────────────────────

async def scan_workspace(workspace: Path) -> WorkspaceState:
    """Scan the full workspace: files, traces, test results, and coverage."""
    import asyncio

    forge_logger.emit("INFO", "SCAN ", f"Scanning workspace: {workspace}")

    source_files, test_files = scan_files(workspace)
    test_results, lcov, error = await asyncio.to_thread(
        _run_tests_and_coverage, workspace,
    )

    cov_str = f"{lcov.line_pct:.0f}%" if lcov.line_pct is not None else "n/a"
    emit_kwargs: dict[str, str] = {}
    if lcov.line_pct is not None:
        emit_kwargs["line_coverage"] = str(round(lcov.line_pct, 1))
    if lcov.branch_pct is not None:
        emit_kwargs["branch_coverage"] = str(round(lcov.branch_pct, 1))
    forge_logger.emit(
        "INFO", "SCAN ",
        f"Workspace state: {len(test_results)} test result(s), coverage {cov_str}"
        + (f" — {error}" if error else ""),
        **emit_kwargs,
    )

    return WorkspaceState(
        source_files=source_files,
        test_files=test_files,
        test_results=test_results,
        coverage_pct=lcov.line_pct,
        coverage_missing=lcov.missing,
        coverage_by_file=lcov.by_file,
        uncovered_lines=lcov.uncovered_lines,
        branch_coverage_pct=lcov.branch_pct,
        test_run_error=error,
    )
