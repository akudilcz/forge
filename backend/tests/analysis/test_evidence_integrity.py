"""RESULT-node evidence-integrity checks.

Behavioural reference: specs/13-quality-and-convergence-guarantees.md
§Evidence integrity. A RESULT node is only proof when it names the test
*function* that ran, carries a known status, and hangs off a TEST node.
Bazel's synthesized fallback XML produces one target-level "PASSED"
testcase with no function name — evidence of that shape must surface a
loud gap, never be counted as requirement coverage.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.analysis.evidence_integrity import (
    VALID_RESULT_STATUSES,
    check_result_integrity,
)
from backend.analysis.gap_analyser import GapAnalyser
from backend.analysis.gaps import GapType
from backend.graph.models import GraphNode, NodeType


def _node(
    node_id: str,
    node_type: str,
    parent_id: str | None,
    properties: dict[str, object],
) -> GraphNode:
    return GraphNode(
        node_id=node_id,
        node_type=node_type,
        title=node_id,
        content="evidence",
        parent_id=parent_id,
        properties=properties,
    )


def _graph(nodes: list[GraphNode]) -> MagicMock:
    by_id = {n.node_id: n for n in nodes}
    graph = MagicMock()
    graph.all_nodes.return_value = nodes
    graph.node_sync.side_effect = lambda nid: by_id.get(nid)
    graph.children_sync.return_value = []
    return graph


def _test_parent() -> GraphNode:
    return _node("TEST-0001", NodeType.TEST.value, "CASE-LLR-0001", {})


def _result(**props: object) -> GraphNode:
    base: dict[str, object] = {
        "status": "passed",
        "test_id": "tests/test_motion.py::test_plan",
        "file_path": "tests/test_motion.py",
        "function_name": "test_plan",
    }
    base.update(props)
    return _node("RESULT-tests_test_motion_py_test_plan-abcd1234",
                 NodeType.RESULT.value, "TEST-0001", base)


# ── Happy path: real per-function evidence ──────────────────────────────────


def test_per_function_result_under_test_parent_is_valid_evidence() -> None:
    graph = _graph([_test_parent(), _result()])
    assert check_result_integrity(graph) == []


def test_recorded_failure_is_valid_evidence_too() -> None:
    """Integrity is about the *shape* of evidence, not its verdict."""
    graph = _graph([_test_parent(), _result(status="failed")])
    assert check_result_integrity(graph) == []


# ── Vacuous evidence: bazel's synthesized target-level XML ──────────────────


def test_empty_function_name_raises_a_gap_naming_the_file() -> None:
    graph = _graph([_test_parent(), _result(function_name="",
                                            test_id="tests/test_motion.py")])
    gaps = check_result_integrity(graph)
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.type == GapType.INVALID_TEST_EVIDENCE
    assert "tests/test_motion.py" in gap.description
    assert gap.context["file_path"] == "tests/test_motion.py"
    assert "function" in gap.description.lower()
    # Remediation must be actionable, not a bare complaint.
    assert "re-run" in gap.description.lower()


def test_whitespace_function_name_is_not_a_function_name() -> None:
    graph = _graph([_test_parent(), _result(function_name="   ")])
    assert [g.type for g in check_result_integrity(graph)] == [
        GapType.INVALID_TEST_EVIDENCE
    ]


def test_missing_function_name_property_is_a_gap() -> None:
    result = _result()
    del result.properties["function_name"]
    graph = _graph([_test_parent(), result])
    gaps = check_result_integrity(graph)
    assert len(gaps) == 1
    assert "function" in gaps[0].description.lower()


# ── Status-set violations ───────────────────────────────────────────────────


@pytest.mark.parametrize("status", ["PASSED", "pass", "ok", ""])
def test_unknown_status_is_a_gap(status: str) -> None:
    graph = _graph([_test_parent(), _result(status=status)])
    gaps = check_result_integrity(graph)
    assert len(gaps) == 1
    assert "status" in gaps[0].description.lower()


def test_missing_status_property_is_a_gap() -> None:
    result = _result()
    del result.properties["status"]
    graph = _graph([_test_parent(), result])
    assert len(check_result_integrity(graph)) == 1


def test_valid_status_set_matches_the_recorder_statuses() -> None:
    """The analyser's status set must mirror the parser's TestStatus enum."""
    from backend.workspace.result_recorder import TestStatus

    assert VALID_RESULT_STATUSES == frozenset(s.value for s in TestStatus)


# ── Parentage ───────────────────────────────────────────────────────────────


def test_result_parented_to_a_case_is_a_gap() -> None:
    case = _node("CASE-LLR-0001", NodeType.CASE_LLR.value, "SUITE-0001", {})
    result = _result()
    result.parent_id = "CASE-LLR-0001"
    gaps = check_result_integrity(_graph([case, result]))
    assert len(gaps) == 1
    assert "TEST" in gaps[0].description


def test_result_with_missing_parent_is_a_gap() -> None:
    gaps = check_result_integrity(_graph([_result()]))
    assert len(gaps) == 1
    assert "parent" in gaps[0].description.lower()


# ── Analyser wiring: a legacy/resumed graph reveals fake evidence ───────────


def test_gap_analyser_surfaces_invalid_result_evidence() -> None:
    """A resumed graph full of target-level RESULTs must not look clean."""
    fake = [
        _node(
            f"RESULT-tests_test_{i}_py-0000000{i}",
            NodeType.RESULT.value,
            "TEST-0001",
            {
                "status": "passed",
                "test_id": f"tests/test_{i}.py",
                "file_path": f"tests/test_{i}.py",
                "function_name": "",
            },
        )
        for i in range(3)
    ]
    gaps = GapAnalyser().analyse(_graph([_test_parent(), *fake]))
    evidence_gaps = [g for g in gaps if g.type == GapType.INVALID_TEST_EVIDENCE]
    assert len(evidence_gaps) == 3
    assert {g.context["file_path"] for g in evidence_gaps} == {
        "tests/test_0.py", "tests/test_1.py", "tests/test_2.py",
    }


def test_gap_analyser_clean_on_real_evidence() -> None:
    gaps = GapAnalyser().analyse(_graph([_test_parent(), _result()]))
    assert [g for g in gaps if g.type == GapType.INVALID_TEST_EVIDENCE] == []


def test_phase_13_cannot_complete_with_invalid_evidence() -> None:
    """The auditor blocks phase 13 while fake RESULTs exist."""
    from backend.analysis.phase_auditor import PHASE_COMPLETION_CRITERIA, PhaseAuditor

    assert GapType.INVALID_TEST_EVIDENCE in PHASE_COMPLETION_CRITERIA[13]
    graph = _graph([_test_parent(), _result(function_name="")])
    audit = PhaseAuditor().audit(13, graph)
    assert not audit.is_complete
    assert GapType.INVALID_TEST_EVIDENCE in audit.blocking_gap_types
