"""Result recorder — create RESULT nodes from pytest output.

After tests run, this module parses per-test-function pass/fail results
and creates/updates RESULT nodes in the graph. Each RESULT node traces
to the TEST node whose CASE annotation matches the test function.

Graph chain: RESULT → TEST → CASE_HLR/CASE_LLR
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from backend.crew.bazel_gen import init_bazel_workspace
from backend.graph.models import GraphNode, LifecycleState, NodeType
from backend.server.forge_logger import forge_logger

logger = logging.getLogger(__name__)


# ── Data model ──────────────────────────────────────────────────────────────

class TestStatus(str, Enum):
    """The four outcomes the JUnit/bazel parsers emit for a test function."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


def is_passed(status: str) -> bool:
    """True only for a test that actually ran and passed."""
    return status == TestStatus.PASSED


def is_not_passing(status: str) -> bool:
    """True for anything that is not positive evidence of a pass.

    Gate semantics: a skipped test is not a failure, but it is not proof the
    behaviour works either, so a coverage gate must not accept it.
    """
    return status != TestStatus.PASSED


def is_failure(status: str) -> bool:
    """True for a test that ran and did not pass.

    Reporting semantics: excludes ``skipped``, so passed + failed + skipped
    partitions the results. Distinct from :func:`is_not_passing` — both used to
    be spelled ``failed`` at different call sites in the same module, giving two
    different answers to "how many failed?".
    """
    return status in (TestStatus.FAILED, TestStatus.ERROR)


@dataclass
class SingleTestResult:
    """A single test function's pass/fail result."""

    test_id: str        # e.g. "tests/test_motion.py::test_plan"
    file_path: str      # e.g. "tests/test_motion.py"
    function_name: str  # e.g. "test_plan"
    status: str         # one of TestStatus; kept as str for parser output
    duration_ms: int = 0
    error_message: str = ""  # short failure/error message (first line)
    error_detail: str = ""   # full traceback or body from JUnit XML


# ── Pytest runner with JUnit XML output ──────────────────────────────────────


def purge_stale_test_artifacts(workspace: Path) -> None:
    """Delete leftover bazel/coverage artifacts so runs parse only fresh evidence.

    Bazel leaves prior-run ``test.xml`` files for targets that later fail to
    build, and coverage exports persist on disk. Parsing them would present
    stale results as current, so every bazel invocation purges them first.
    """
    testlogs = workspace / "bazel-testlogs"
    if testlogs.exists():
        for xml in testlogs.rglob("test.xml"):
            xml.unlink()
    for artifact in (
        workspace / "coverage.lcov",
        workspace / "coverage-test-results.xml",
        workspace / "bazel-out" / "_coverage" / "_coverage_report.dat",
    ):
        if artifact.exists():
            artifact.unlink()


def run_and_parse_tests(workspace: Path) -> list[SingleTestResult]:
    """Run tests via bazel and parse fresh results from bazel-testlogs XML.

    Regenerates BUILD files first (so tests written since the last run have
    targets), purges stale artifacts, and raises loudly when bazel exits
    nonzero without producing any fresh results — never returns stale or
    silently empty evidence.
    """
    tests_dir = workspace / "tests"
    if not tests_dir.exists() or not any(tests_dir.glob("test_*.py")):
        return []

    from backend.workspace.test_reports import extract_error_summary, parse_bazel_testlogs

    init_bazel_workspace(workspace)
    purge_stale_test_artifacts(workspace)

    proc = subprocess.run(
        ["bazel", "test", "//tests/...",
         "--test_output=all"],
        cwd=str(workspace),
        capture_output=True,
        text=True,
        timeout=600,
    )
    results = parse_bazel_testlogs(workspace)
    if proc.returncode != 0 and not results:
        summary = extract_error_summary(proc.stdout + proc.stderr)
        forge_logger.emit("ERROR", "CGEN ", f"Test run produced no fresh results: {summary}")
        raise RuntimeError(
            f"bazel test failed (rc={proc.returncode}) with no fresh results: {summary}"
        )
    return results


# ── RESULT node creation ────────────────────────────────────────────────────

def _find_trace_targets(
    func_name: str,
    file_path: str,
    graph: Any,
) -> list[str]:
    """Find TEST nodes this test function should trace to.

    Follows the canonical chain: RESULT → TEST → CASE.
    Looks at CASE nodes owning this file, finds the function in line_traces,
    then finds TEST nodes that trace to those CASEs.
    Falls back to the CASE node itself if no TEST node exists.

    When func_name is empty (bazel per-target results), matches by
    file_path alone — the RESULT covers the whole test file.
    """
    all_nodes = graph.all_nodes()

    # Step 1: find CASE nodes matching this test
    case_ids: list[str] = []
    for node in all_nodes:
        if node.node_type not in ("CASE_HLR", "CASE_LLR"):
            continue
        props = node.properties or {}
        node_file = props.get("file_path", "")
        if not node_file or node_file != file_path:
            continue

        if func_name:
            # Per-function match: look for specific function in line_traces
            line_traces = props.get("line_traces", [])
            for trace in line_traces:
                if trace.get("symbol") == func_name:
                    case_ids.append(node.node_id)
                    break
        else:
            # Per-file match (bazel stub): RESULT covers the whole file
            case_ids.append(node.node_id)

    if not case_ids:
        return []

    # Step 2: find TEST nodes tracing to those CASEs
    test_ids: list[str] = []
    for node in all_nodes:
        if node.node_type != "TEST":
            continue
        if any(cid in node.trace_to for cid in case_ids):
            test_ids.append(node.node_id)

    # Prefer TEST nodes (canonical chain), fall back to CASE
    return test_ids if test_ids else case_ids


def _resolve_requirement_traces(
    parent_candidates: list[str], graph: Any,
) -> list[str]:
    """Follow CASE/TEST → trace_to to find the HLR/LLR requirements verified."""
    req_ids: list[str] = []
    for nid in parent_candidates:
        node = graph.node_sync(nid)
        if node and node.trace_to:
            req_ids.extend(node.trace_to)
    return list(dict.fromkeys(req_ids))  # dedupe, preserve order


async def record_results(
    workspace: Path, graph: Any, last_state: Any | None = None,
) -> list[SingleTestResult]:
    """Record test RESULT nodes in the graph.

    If *last_state* is provided and has test results, uses those instead
    of re-running tests (avoids hitting the same errors twice).
    """
    forge_logger.emit("INFO", "CGEN ", "Recording test results as RESULT nodes...")

    # Use cached results from the gap loop when available
    results: list[SingleTestResult]
    if last_state and getattr(last_state, "test_results", None):
        results = last_state.test_results
    else:
        results = run_and_parse_tests(workspace)

    if not results:
        forge_logger.emit("INFO", "CGEN ", "No test results to record")
        return results

    created = 0
    for tr in results:
        parent_candidates = _find_trace_targets(
            tr.function_name, tr.file_path, graph,
        )
        parent_id = parent_candidates[0] if parent_candidates else None

        # trace_to: include CASE/TEST parents (for frontend RESULT→CASE
        # status resolution) AND the HLR/LLR requirements they trace to.
        req_ids = _resolve_requirement_traces(parent_candidates, graph)
        trace_to = list(dict.fromkeys(parent_candidates + req_ids))

        node_id = _result_node_id(tr)

        result_node = GraphNode(
            node_id=node_id,
            node_type=NodeType.RESULT.value,
            title=f"{tr.function_name} — {tr.status}",
            content=f"Test {tr.status}: {tr.test_id}",
            lifecycle=LifecycleState.ACTIVE,
            parent_id=parent_id,
            trace_to=trace_to,
            properties={
                "status": tr.status,
                "test_id": tr.test_id,
                "file_path": tr.file_path,
                "function_name": tr.function_name,
            },
        )
        await graph.add_node(result_node)
        created += 1

    forge_logger.emit(
        "INFO", "CGEN ",
        f"Recorded {created} RESULT node(s): "
        f"{sum(1 for r in results if r.status == 'passed')} passed, "
        f"{sum(1 for r in results if r.status == 'failed')} failed",
    )
    return results


def _result_node_id(tr: SingleTestResult) -> str:
    """Generate a stable, collision-free RESULT node ID from the test identifier."""
    # Deterministic ID so re-runs update the same node. The slug is truncated
    # to keep IDs readable; the sha256 suffix keeps long test_ids sharing a
    # 60-char prefix distinct (the graph stores with INSERT OR REPLACE, so a
    # collision would silently overwrite test evidence).
    slug = tr.test_id.replace("/", "_").replace("::", "_").replace(".", "_")
    digest = hashlib.sha256(tr.test_id.encode("utf-8")).hexdigest()[:8]
    return f"RESULT-{slug[:60]}-{digest}"
