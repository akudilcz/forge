"""Evidence-integrity checks over RESULT nodes.

Deterministic, LLM-free validation that the test evidence held in the
graph is *capable* of being proof. Every gate FORGE builds above the test
runner (requirement coverage, oracle validation, mutation) reads RESULT
nodes and trusts them; a live build proved that trust unfounded when
Bazel synthesized one target-level "PASSED" testcase per target — empty
function name, zero duration — and 226/226 "passing" results described
tests that never executed.

The invariants below are the floor beneath those gates: a RESULT is only
evidence when it names the test **function** that ran, carries a status
from the known set, and hangs off a TEST node (the write path already
enforces parentage; this re-checks it so a resumed or legacy graph
reveals fake evidence instead of hiding it).

Design reference: specs/13-quality-and-convergence-guarantees.md
§Evidence integrity.
"""

from __future__ import annotations

from typing import Any

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.graph.models import GraphNode, NodeType

#: The statuses a RESULT may carry. Mirrors
#: ``backend.workspace.result_recorder.TestStatus`` — kept here so the
#: analyser never imports the recorder (and with it the codegen/bazel
#: stack); ``test_evidence_integrity`` asserts the two stay identical.
VALID_RESULT_STATUSES: frozenset[str] = frozenset(
    {"passed", "failed", "skipped", "error"}
)

#: Statuses that assert nothing failed — the shapes a vacuous "proof" takes.
NON_FAILURE_STATUSES: frozenset[str] = frozenset({"passed", "skipped"})

UNKNOWN_FILE = "<unknown file>"

_REMEDIATION = (
    "Re-run the suite so Bazel emits real per-function pytest JUnit XML "
    "(every py_test target must set main=tests/pytest_runner.py), then "
    "re-record RESULT nodes. Until then this node is not proof and must "
    "not count towards requirement coverage."
)


class EvidenceIntegrityError(RuntimeError):
    """Raised when test evidence cannot be accepted as proof."""


def _result_violations(node: GraphNode, graph: Any) -> list[str]:
    """Every way this RESULT node fails to be usable evidence."""
    props = node.properties or {}
    violations: list[str] = []

    parent = graph.node_sync(node.parent_id) if node.parent_id else None
    if parent is None or parent.node_type != NodeType.TEST.value:
        found = parent.node_type if parent is not None else "missing"
        violations.append(
            f"parent {node.parent_id!r} is {found}, but a RESULT's only "
            f"valid parent is a TEST node"
        )

    if "function_name" not in props:
        violations.append("no function_name property — the test function is unknown")
    elif not str(props["function_name"]).strip():
        violations.append(
            "empty function_name — a target-level result (Bazel's synthesized "
            "fallback XML shape), not evidence that a test function ran"
        )

    if "status" not in props:
        violations.append("no status property")
    elif props["status"] not in VALID_RESULT_STATUSES:
        violations.append(
            f"status {props['status']!r} is not one of "
            f"{sorted(VALID_RESULT_STATUSES)}"
        )
    return violations


def check_result_integrity(graph: Any) -> list[Gap]:
    """Return one INVALID_TEST_EVIDENCE gap per unusable RESULT node.

    Runs over the whole graph, so a build resumed onto a graph recorded
    before this guard existed surfaces its fake evidence immediately.
    """
    gaps: list[Gap] = []
    for node in graph.all_nodes():
        if node.node_type != NodeType.RESULT.value:
            continue
        violations = _result_violations(node, graph)
        if not violations:
            continue
        props = node.properties or {}
        file_path = str(props["file_path"]) if "file_path" in props else UNKNOWN_FILE
        gaps.append(
            Gap(
                type=GapType.INVALID_TEST_EVIDENCE,
                priority=GapPriority.MAINTENANCE,
                node_id=node.node_id,
                description=(
                    f"RESULT {node.node_id} for {file_path} is not valid test "
                    f"evidence: {'; '.join(violations)}. {_REMEDIATION}"
                ),
                context={"file_path": file_path, "violations": violations},
            )
        )
    return gaps
