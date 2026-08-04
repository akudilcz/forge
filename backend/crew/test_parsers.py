"""Test result and coverage parsers — extracted from workspace_scanner.

Contains JUnit XML parsing, LCOV coverage parsing, bazel testlog parsing,
and test result merging. These are pure functions with no workspace state
dependencies.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from backend.crew.result_recorder import SingleTestResult
from backend.server.forge_logger import forge_logger

# ── Data models ─────────────────────────────────────────────────────────────


@dataclass
class LcovResult:
    """Parsed LCOV coverage data."""

    line_pct: float | None = None
    branch_pct: float | None = None
    missing: str = ""
    by_file: dict[str, float] = field(default_factory=dict)
    uncovered_lines: dict[str, list[int]] = field(default_factory=dict)


# ── JUnit XML parsing ──────────────────────────────────────────────────────


def parse_junit_xml(path: Path) -> list[SingleTestResult]:
    """Parse a JUnit XML file into per-test results.

    Handles two XML formats:
    - Per-function: classname="tests.test_foo" name="test_bar"
    - Per-target (bazel stub): name="tests/test_foo" with no classname
    """
    if not path.exists():
        return []
    try:
        tree = ET.parse(path)  # noqa: S314
    except ET.ParseError:
        forge_logger.emit("WARN", "SCAN ", f"Failed to parse JUnit XML: {path}")
        return []

    results: list[SingleTestResult] = []
    for testcase in tree.iter("testcase"):
        file_path, func_name = _resolve_test_identity(testcase)
        status, error_message, error_detail = _extract_test_status(testcase, path)

        test_id = f"{file_path}::{func_name}" if func_name else file_path
        results.append(SingleTestResult(
            test_id=test_id,
            file_path=file_path,
            function_name=func_name,
            status=status,
            duration_ms=int(float(testcase.get("time", "0") or "0") * 1000),
            error_message=error_message,
            error_detail=error_detail,
        ))
    return results


def _resolve_test_identity(testcase: ET.Element) -> tuple[str, str]:
    """Extract (file_path, func_name) from a JUnit testcase element."""
    classname = testcase.get("classname", "")
    name = testcase.get("name", "")

    if classname:
        parts = classname.split(".")
        module_parts = []
        for part in parts:
            if part and part[0].isupper():
                break
            module_parts.append(part)
        file_path = "/".join(module_parts) + ".py" if module_parts else ""
        return file_path, name
    elif "/" in name or name.startswith("tests"):
        file_path = name.replace(".", "/")
        if not file_path.endswith(".py"):
            file_path += ".py"
        return file_path, ""
    return "", name


def _extract_test_status(
    testcase: ET.Element, xml_path: Path,
) -> tuple[str, str, str]:
    """Determine test status and extract error details.

    Returns (status, error_message, error_detail).
    """
    log_tail = read_log_tail(xml_path.with_name("test.log"))

    failure_el = testcase.find("failure")
    if failure_el is not None:
        return "failed", (failure_el.get("message") or "")[:300], log_tail[:2000]

    error_el = testcase.find("error")
    if error_el is not None:
        return "error", (error_el.get("message") or "")[:300], log_tail[:2000]

    if testcase.find("skipped") is not None:
        return "skipped", "", ""

    return "passed", "", ""


# ── Bazel testlogs ──────────────────────────────────────────────────────────


def read_log_tail(log_path: Path) -> str:
    """Read the last 50 non-empty lines from a bazel test.log file."""
    if not log_path.exists():
        return ""
    try:
        text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-50:])


def parse_bazel_testlogs(workspace: Path) -> list[SingleTestResult]:
    """Parse test.xml files from bazel-testlogs/tests/*/test.xml.

    Only includes results for test files that currently exist on disk.
    """
    testlogs = workspace / "bazel-testlogs" / "tests"
    if not testlogs.exists():
        return []

    tests_dir = workspace / "tests"
    existing = {f"tests/{p.name}" for p in tests_dir.glob("test_*.py")} if tests_dir.exists() else set()

    results: list[SingleTestResult] = []
    for xml_file in sorted(testlogs.glob("*/test.xml")):
        parsed = parse_junit_xml(xml_file)
        results.extend(r for r in parsed if not r.file_path or r.file_path in existing)
    return results


# ── Test result merging ─────────────────────────────────────────────────────


def merge_test_results(
    bazel_results: list[SingleTestResult],
    coverage_results: list[SingleTestResult],
) -> list[SingleTestResult]:
    """Merge coverage-run results into bazel results.

    For each test_id, prefer the coverage-run result when it shows a
    failure that bazel missed, or vice versa. Also drops bazel file-level
    error stubs when coverage has per-function results for that file
    (indicates bazel sandbox issue, not a real test failure).
    """
    by_id: dict[str, SingleTestResult] = {r.test_id: r for r in bazel_results}
    merged_count = 0

    # Collect files that coverage.py tested successfully
    cov_files: set[str] = {cr.file_path for cr in coverage_results if cr.file_path}

    for cr in coverage_results:
        existing = by_id.get(cr.test_id)
        if existing is None:
            by_id[cr.test_id] = cr
            merged_count += 1
        elif cr.status in ("failed", "error") and existing.status == "passed":
            by_id[cr.test_id] = cr
            merged_count += 1
        elif cr.status == "passed" and existing.status in ("failed", "error"):
            by_id[cr.test_id] = cr
            merged_count += 1

    # Drop bazel file-level error stubs when coverage has real results
    # for that file. These are sandbox import errors (e.g. numpy missing
    # from bazel deps) — the tests actually pass under coverage.py.
    stale_ids = [
        tid for tid, r in by_id.items()
        if r.status in ("error", "failed")
        and not r.function_name  # file-level stub (no function)
        and r.file_path in cov_files  # coverage has results for this file
    ]
    for tid in stale_ids:
        del by_id[tid]
        merged_count += 1

    if merged_count:
        forge_logger.emit(
            "INFO", "SCAN ",
            f"Merged {merged_count} result(s) from coverage run",
        )
    return list(by_id.values())


# ── LCOV coverage parsing ──────────────────────────────────────────────────


def parse_lcov_file(lcov_path: Path) -> LcovResult:
    """Parse an LCOV file and return coverage data."""
    if not lcov_path.exists():
        return LcovResult()

    try:
        text = lcov_path.read_text(encoding="utf-8")
    except OSError:
        return LcovResult()

    counters = _LcovCounters()
    for line in text.splitlines():
        counters.process_line(line)

    return counters.build_result()


@dataclass
class _LcovCounters:
    """Accumulator for LCOV file parsing."""

    total_hit: int = 0
    total_found: int = 0
    branch_hit: int = 0
    branch_found: int = 0
    missing_files: list[str] = field(default_factory=list)
    coverage_by_file: dict[str, float] = field(default_factory=dict)
    uncovered_lines: dict[str, list[int]] = field(default_factory=dict)
    current_file: str = ""
    file_found: int = 0
    file_hit: int = 0
    file_uncovered: list[int] = field(default_factory=list)

    def process_line(self, line: str) -> None:
        """Process a single LCOV line."""
        if line.startswith("SF:"):
            self.current_file = line[3:]
            self.file_found = 0
            self.file_hit = 0
            self.file_uncovered = []
        elif line.startswith("DA:"):
            parts = line[3:].split(",")
            if len(parts) >= 2 and parts[1] == "0":
                self.file_uncovered.append(int(parts[0]))
        elif line.startswith("LF:"):
            self.file_found = int(line[3:])
            self.total_found += self.file_found
        elif line.startswith("LH:"):
            self.file_hit = int(line[3:])
            self.total_hit += self.file_hit
        elif line.startswith("BRF:"):
            self.branch_found += int(line[4:])
        elif line.startswith("BRH:"):
            self.branch_hit += int(line[4:])
        elif line == "end_of_record":
            self._finalize_file()

    def _finalize_file(self) -> None:
        """Record per-file coverage when a record ends."""
        if self.current_file and self.file_found > 0:
            pct = (self.file_hit / self.file_found) * 100
            self.coverage_by_file[self.current_file] = round(pct, 1)
            if self.file_hit < self.file_found:
                self.missing_files.append(self.current_file)
        if self.file_uncovered:
            self.uncovered_lines[self.current_file] = sorted(self.file_uncovered)
        self.current_file = ""

    def build_result(self) -> LcovResult:
        """Build final LcovResult from accumulated counters."""
        line_pct: float | None = None
        if self.total_found > 0:
            line_pct = round((self.total_hit / self.total_found) * 100, 1)
        elif self.coverage_by_file:
            line_pct = 100.0

        branch_pct: float | None = None
        if self.branch_found > 0:
            branch_pct = round((self.branch_hit / self.branch_found) * 100, 1)

        missing = ", ".join(self.missing_files) if self.missing_files else ""
        return LcovResult(
            line_pct=line_pct, branch_pct=branch_pct,
            missing=missing, by_file=self.coverage_by_file,
            uncovered_lines=self.uncovered_lines,
        )


def parse_lcov_coverage(workspace: Path) -> LcovResult:
    """Parse bazel's combined LCOV coverage report."""
    lcov_path = workspace / "bazel-out" / "_coverage" / "_coverage_report.dat"
    return parse_lcov_file(lcov_path)


# ── Error extraction ────────────────────────────────────────────────────────


def extract_error_summary(output: str) -> str:
    """Extract a meaningful error summary from failed test output."""
    import re

    error_blocks = re.findall(
        r"_{5,} ERROR collecting .+? _{5,}\n(.*?)(?=\n_{5,}|\nshort test summary|\Z)",
        output,
        re.DOTALL,
    )
    if error_blocks:
        block = error_blocks[0].strip()
        lines = block.splitlines()[:30]
        return "\n".join(lines)

    lines = output.strip().splitlines()
    tail = [line.strip() for line in lines[-10:] if line.strip()]
    return "\n".join(tail) if tail else "tests exited with errors (no output)"
