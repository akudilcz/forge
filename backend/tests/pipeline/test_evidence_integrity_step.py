"""Phase-13 evidence-integrity gate step.

Behavioural reference: specs/03-build-pipeline.md §Phase 13 and
specs/13-quality-and-convergence-guarantees.md §Evidence integrity. The
step runs after RESULT recording and refuses to let vacuous or
implausible evidence be accepted as proof: it halts the phase loudly
instead of completing on fiction.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.analysis.evidence_integrity import EvidenceIntegrityError
from backend.graph.models import GraphNode, NodeType
from backend.pipeline.runner import PHASE_STEPS
from backend.pipeline.steps import evidence_integrity
from backend.workspace.result_recorder import record_results_step

REAL_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="1" time="0.221">
  <testcase classname="tests.test_motion" name="test_plan" time="0.221"/>
</testsuite></testsuites>
"""

SYNTHETIC_XML = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="tests/test_motion" tests="1" time="0">
  <testcase name="tests/test_motion" status="run" duration="0" time="0"/>
</testsuite></testsuites>
"""


def _seed_workspace(workspace: Path, xml: str, log: str) -> None:
    d = workspace / "bazel-testlogs" / "tests" / "test_motion"
    d.mkdir(parents=True)
    (d / "test.xml").write_text(xml, encoding="utf-8")
    if log:
        (d / "test.log").write_text(log, encoding="utf-8")


def _node(node_id: str, node_type: str, parent_id: str | None,
          properties: dict[str, object]) -> GraphNode:
    return GraphNode(
        node_id=node_id, node_type=node_type, title=node_id,
        content="evidence", parent_id=parent_id, properties=properties,
    )


def _flow(workspace: Path, results: list[GraphNode]) -> MagicMock:
    test_node = _node("TEST-0001", NodeType.TEST.value, "CASE-LLR-0001", {})
    nodes = [test_node, *results]
    by_id = {n.node_id: n for n in nodes}
    graph = MagicMock()
    graph.all_nodes.return_value = nodes
    graph.node_sync.side_effect = lambda nid: by_id.get(nid)
    flow = MagicMock()
    flow.graph = graph
    flow._workspace = workspace
    return flow


def _real_result() -> GraphNode:
    return _node(
        "RESULT-tests_test_motion_py_test_plan-abcd1234",
        NodeType.RESULT.value, "TEST-0001",
        {"status": "passed", "test_id": "tests/test_motion.py::test_plan",
         "file_path": "tests/test_motion.py", "function_name": "test_plan"},
    )


def _vacuous_result() -> GraphNode:
    return _node(
        "RESULT-tests_test_motion_py-deadbeef",
        NodeType.RESULT.value, "TEST-0001",
        {"status": "passed", "test_id": "tests/test_motion.py",
         "file_path": "tests/test_motion.py", "function_name": ""},
    )


async def test_step_passes_on_real_per_function_evidence(tmp_path: Path) -> None:
    _seed_workspace(tmp_path, REAL_XML, "1 passed in 0.22s\n")
    result = await evidence_integrity(_flow(tmp_path, [_real_result()]), 13)
    assert result == {"step_name": "evidence_integrity", "deletions": 0}


async def test_step_halts_on_synthesized_bazel_evidence(tmp_path: Path) -> None:
    _seed_workspace(tmp_path, SYNTHETIC_XML, "")
    with pytest.raises(EvidenceIntegrityError) as exc:
        await evidence_integrity(_flow(tmp_path, [_vacuous_result()]), 13)
    assert "tests/test_motion.py" in str(exc.value)


async def test_step_halts_on_fake_result_nodes_even_with_clean_disk_evidence(
    tmp_path: Path,
) -> None:
    """A resumed graph's fake RESULTs are caught even when the XML is fine."""
    _seed_workspace(tmp_path, REAL_XML, "1 passed in 0.22s\n")
    with pytest.raises(EvidenceIntegrityError):
        await evidence_integrity(_flow(tmp_path, [_vacuous_result()]), 13)


async def test_step_is_a_noop_when_no_tests_ran(tmp_path: Path) -> None:
    result = await evidence_integrity(_flow(tmp_path, []), 13)
    assert result["deletions"] == 0


def test_phase_13_runs_the_gate_after_recording_results() -> None:
    steps = PHASE_STEPS[13]
    assert steps[-1] is evidence_integrity
    assert steps.index(record_results_step) < steps.index(evidence_integrity)
