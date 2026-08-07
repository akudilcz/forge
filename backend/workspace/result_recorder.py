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
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.pipeline.steps import StepResult

from backend.codegen.bazel_gen import init_bazel_workspace
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
         "--test_output=all",
         # Fresh-evidence invariant: purge deletes prior test.xml files, and a
         # CACHED pass would skip execution and regenerate nothing — forcing
         # re-execution guarantees the parser always sees fresh results.
         "--nocache_test_results"],
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
    """Find the TEST nodes this test function's RESULT must be parented to.

    Follows the canonical chain: RESULT → TEST → CASE.
    Looks at CASE nodes owning this file, finds the function in line_traces,
    then finds TEST nodes that trace to those CASEs.

    When func_name is empty (bazel per-target results), matches by
    file_path alone — the RESULT covers the whole test file.

    Raises:
        RuntimeError: if no CASE node owns this test function, or the
            matched CASEs have no TEST node. A RESULT's only valid parent
            is a TEST node (specs/12-artifact-model-and-traceability.md §2); a former
            fall-back to the CASE node produced 230 ORPHAN_NODE gaps in
            a live build. This function runs after phase 13 TEST sync,
            so an unresolvable TEST parent is a real bug, never a state
            to write around.
    """
    all_nodes = graph.all_nodes()

    # Step 1: find CASE nodes matching this test
    file_owned = False
    case_ids: list[str] = []
    for node in all_nodes:
        if node.node_type not in ("CASE_HLR", "CASE_LLR"):
            continue
        props = node.properties or {}
        node_file = props.get("file_path", "")
        if not node_file or node_file != file_path:
            continue
        file_owned = True

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
        if not file_owned:
            # Auxiliary test file (specs/03): no CASE owns this file at
            # all — it is infrastructure, not traceability evidence.
            return []
        raise RuntimeError(
            f"No CASE node owns test function {func_name!r} in {file_path!r} — "
            "cannot record a RESULT without a TEST parent. The file belongs to "
            "CASE(s) but this function is missing from their line_traces; fix "
            "the CASE line_traces before recording evidence."
        )

    # Step 2: find TEST nodes tracing to those CASEs
    test_ids: list[str] = []
    for node in all_nodes:
        if node.node_type != "TEST":
            continue
        if any(cid in node.trace_to for cid in case_ids):
            test_ids.append(node.node_id)

    if not test_ids:
        raise RuntimeError(
            f"No TEST node traces to CASE(s) {case_ids} for test function "
            f"{func_name!r} in {file_path!r}. RESULT nodes may only be "
            "parented to TEST nodes — run phase 13 workspace sync first."
        )
    return test_ids


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


async def record_results(workspace: Path, graph: Any) -> list[SingleTestResult]:
    """Record test RESULT nodes in the graph (phase 13, after TEST sync).

    Always runs the suite via bazel and parses fresh evidence — RESULTs
    must describe the workspace as it stands, never a cached phase-12 run.

    Raises:
        RuntimeError: if any test function's TEST parent cannot be
            resolved — an invalid RESULT is never written.
    """
    forge_logger.emit("INFO", "SYNC ", "Recording test results as RESULT nodes...")

    results = run_and_parse_tests(workspace)

    if not results:
        forge_logger.emit("INFO", "SYNC ", "No test results to record")
        return results

    created = 0
    skipped_aux: list[str] = []
    for tr in results:
        parent_candidates = _find_trace_targets(
            tr.function_name, tr.file_path, graph,
        )
        if not parent_candidates:
            # Auxiliary file no CASE owns (specs/03): loud skip, no RESULT.
            skipped_aux.append(tr.file_path)
            continue
        parent_id = parent_candidates[0]

        # Write-path invariant guard: the graph engine does not validate
        # parent types (only the agent-facing graph_write tools do), so
        # enforce RESULT → TEST here before the engine write.
        parent = graph.node_sync(parent_id)
        if parent is None or parent.node_type != NodeType.TEST.value:
            raise RuntimeError(
                f"RESULT for {tr.test_id!r} resolved parent {parent_id!r} "
                f"({getattr(parent, 'node_type', 'missing')}), but a RESULT's "
                "only valid parent is a TEST node — refusing to write."
            )

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

    if skipped_aux:
        unique = sorted(set(skipped_aux))
        forge_logger.emit(
            "WARN", "SYNC ",
            f"Skipped {len(unique)} auxiliary test file(s) no CASE owns "
            f"(not traceability evidence): {', '.join(unique)}",
        )
    forge_logger.emit(
        "INFO", "SYNC ",
        f"Recorded {created} RESULT node(s): "
        f"{sum(1 for r in results if r.status == 'passed')} passed, "
        f"{sum(1 for r in results if r.status == 'failed')} failed",
    )
    return results


async def heal_result_parents(graph: Any) -> int:
    """Re-parent RESULT nodes that are not children of a TEST node.

    Resumability guard for phase 13: builds recorded before the
    RESULT-parentage fix (or interrupted mid-sync) hold RESULT nodes
    parented to CASE nodes — each one an ORPHAN_NODE gap. A resumed
    phase 13 must heal that state deterministically before recording new
    evidence. Resolution reuses ``_find_trace_targets`` on the RESULT's
    own ``file_path``/``function_name`` properties; the TEST id is merged
    into ``trace_to``.

    Returns the number of RESULT nodes re-parented.

    Raises:
        RuntimeError: if a misparented RESULT's TEST node cannot be
            resolved — healing never falls back to an invalid parent.
    """
    healed = 0
    for node in list(graph.all_nodes()):
        if node.node_type != NodeType.RESULT.value:
            continue
        parent = graph.node_sync(node.parent_id) if node.parent_id else None
        if parent is not None and parent.node_type == NodeType.TEST.value:
            continue
        props = node.properties or {}
        targets = _find_trace_targets(
            props["function_name"], props["file_path"], graph,
        )
        test_id = targets[0]
        await graph.reparent_node(
            node.node_id,
            test_id,
            "workspace-sync",
            "Heal RESULT parentage: a RESULT's only valid parent is a TEST node",
        )
        if test_id not in (node.trace_to or []):
            await graph.update_node(
                node.node_id,
                content=None,
                properties=None,
                changed_by="workspace-sync",
                change_reason="Merge TEST parent into RESULT trace_to",
                trace_to=list(dict.fromkeys([test_id, *(node.trace_to or [])])),
            )
        healed += 1
        forge_logger.emit(
            "INFO", "SYNC ",
            f"  HEAL {node.node_id}: parent {node.parent_id} → {test_id}",
        )
    return healed


async def record_results_step(flow: Any, phase: int) -> StepResult:
    """Phase 13 pipeline step: heal RESULT parentage, then record results.

    Runs strictly after ``workspace_sync`` (which creates the TEST nodes
    every RESULT is parented to) — see specs/03-build-pipeline.md.
    """
    forge_logger.emit(
        "INFO", "PIPE ",
        f"Phase {phase} · step: record_results",
        phase=phase,
    )
    healed = await heal_result_parents(flow.graph)
    if healed:
        forge_logger.emit(
            "INFO", "SYNC ", f"Healed {healed} misparented RESULT node(s)",
        )
    results = await record_results(flow._workspace, flow.graph)
    forge_logger.emit(
        "INFO", "SYNC ",
        f"record_results step complete — {len(results)} result(s), "
        f"{healed} healed",
        phase=phase,
    )
    return {"step_name": "record_results", "deletions": 0}


def _result_node_id(tr: SingleTestResult) -> str:
    """Generate a stable, collision-free RESULT node ID from the test identifier."""
    # Deterministic ID so re-runs update the same node. The slug is truncated
    # to keep IDs readable; the sha256 suffix keeps long test_ids sharing a
    # 60-char prefix distinct (the graph stores with INSERT OR REPLACE, so a
    # collision would silently overwrite test evidence).
    slug = tr.test_id.replace("/", "_").replace("::", "_").replace(".", "_")
    digest = hashlib.sha256(tr.test_id.encode("utf-8")).hexdigest()[:8]
    return f"RESULT-{slug[:60]}-{digest}"
