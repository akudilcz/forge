"""Tests for backend.crew.result_recorder."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from backend.crew.result_recorder import (
    SingleTestResult,
    _find_trace_targets,
    _resolve_requirement_traces,
    _result_node_id,
)
from backend.crew.test_parsers import parse_junit_xml as _parse_junit_xml

# ── _parse_junit_xml ────────────────────────────────────────────────────────

def test_parse_junit_xml_all_passed(tmp_path: Path) -> None:
    """Parses JUnit XML with all passing tests."""
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<testsuite tests="2">\n'
        '  <testcase classname="tests.test_motion" name="test_plan" time="0.1"/>\n'
        '  <testcase classname="tests.test_motion" name="test_execute" time="0.2"/>\n'
        '</testsuite>\n'
    )
    f = tmp_path / "results.xml"
    f.write_text(xml)

    results = _parse_junit_xml(f)
    assert len(results) == 2
    assert results[0].function_name == "test_plan"
    assert results[0].status == "passed"
    assert results[1].function_name == "test_execute"
    assert results[1].status == "passed"


def test_parse_junit_xml_mixed_results(tmp_path: Path) -> None:
    """Parses XML with passed, failed, and skipped tests."""
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<testsuite tests="3">\n'
        '  <testcase classname="tests.test_a" name="test_ok" time="0.1"/>\n'
        '  <testcase classname="tests.test_a" name="test_bad" time="0.2">\n'
        '    <failure message="assert False">Traceback...</failure>\n'
        '  </testcase>\n'
        '  <testcase classname="tests.test_a" name="test_skip" time="0.0">\n'
        '    <skipped message="not applicable"/>\n'
        '  </testcase>\n'
        '</testsuite>\n'
    )
    f = tmp_path / "results.xml"
    f.write_text(xml)

    results = _parse_junit_xml(f)
    assert len(results) == 3
    assert results[0].status == "passed"
    assert results[1].status == "failed"
    assert results[2].status == "skipped"


def test_parse_junit_xml_missing_file(tmp_path: Path) -> None:
    """Returns empty list for non-existent file."""
    assert _parse_junit_xml(tmp_path / "nope.xml") == []


# ── _result_node_id ─────────────────────────────────────────────────────────

def test_result_node_id_stable() -> None:
    """Same test ID produces the same RESULT node ID."""
    tr = SingleTestResult(
        test_id="tests/test_motion.py::test_plan",
        file_path="tests/test_motion.py",
        function_name="test_plan",
        status="passed",
    )
    id1 = _result_node_id(tr)
    id2 = _result_node_id(tr)
    assert id1 == id2
    assert id1.startswith("RESULT-")


def test_result_node_id_truncated() -> None:
    """Long test IDs are truncated to keep node IDs manageable."""
    tr = SingleTestResult(
        test_id="tests/test_very_long_module_name.py::test_extremely_long_function_name_that_goes_on_forever",
        file_path="tests/test_very_long_module_name.py",
        function_name="test_extremely_long_function_name_that_goes_on_forever",
        status="passed",
    )
    node_id = _result_node_id(tr)
    # RESULT- prefix (7) + max 60 slug chars + "-" + 8 hash chars
    assert len(node_id) <= 76


def test_result_node_id_long_shared_prefix_no_collision() -> None:
    """Two long test_ids sharing a 60-char prefix produce distinct node IDs."""
    shared = "tests/test_verify_run_gallop_constant_values.py::test_constant"
    tr_a = SingleTestResult(
        test_id=shared + "_values_hold_for_ascending_input",
        file_path="tests/test_verify_run_gallop_constant_values.py",
        function_name="test_constant_values_hold_for_ascending_input",
        status="failed",
    )
    tr_b = SingleTestResult(
        test_id=shared + "_values_hold_for_descending_input",
        file_path="tests/test_verify_run_gallop_constant_values.py",
        function_name="test_constant_values_hold_for_descending_input",
        status="passed",
    )
    id_a = _result_node_id(tr_a)
    id_b = _result_node_id(tr_b)
    assert id_a != id_b
    assert len(id_a) <= 76
    assert len(id_b) <= 76


# ── _find_trace_targets ─────────────────────────────────────────────────────

def _make_node(
    node_id: str,
    node_type: str,
    trace_to: list[str] | None = None,
    properties: dict[str, Any] | None = None,
) -> MagicMock:
    """Create a mock graph node."""
    n = MagicMock()
    n.node_id = node_id
    n.node_type = node_type
    n.trace_to = trace_to or []
    n.properties = properties or {}
    return n


def test_find_trace_targets_via_test_node() -> None:
    """Finds TEST node when CASE has matching function in line_traces."""
    case = _make_node("CASE_LLR-001", "CASE_LLR", properties={
        "file_path": "tests/test_motion.py",
        "line_traces": [{"symbol": "test_plan", "start": 10, "end": 20, "llr_ids": ["LLR-001"]}],
    })
    test = _make_node("TEST-001", "TEST", trace_to=["CASE_LLR-001"])
    graph = MagicMock()
    graph.all_nodes.return_value = [case, test]

    targets = _find_trace_targets("test_plan", "tests/test_motion.py", graph)
    assert targets == ["TEST-001"]


def test_find_trace_targets_falls_back_to_case() -> None:
    """Falls back to CASE node when no TEST node exists."""
    case = _make_node("CASE_HLR-001", "CASE_HLR", properties={
        "file_path": "tests/test_motion.py",
        "line_traces": [{"symbol": "test_plan", "start": 10, "end": 20, "llr_ids": ["LLR-001"]}],
    })
    graph = MagicMock()
    graph.all_nodes.return_value = [case]

    targets = _find_trace_targets("test_plan", "tests/test_motion.py", graph)
    assert targets == ["CASE_HLR-001"]


def test_find_trace_targets_no_match() -> None:
    """Returns empty list when function isn't found in any CASE."""
    case = _make_node("CASE_LLR-001", "CASE_LLR", properties={
        "file_path": "tests/test_other.py",
        "line_traces": [{"symbol": "test_other", "start": 1, "end": 5, "llr_ids": []}],
    })
    graph = MagicMock()
    graph.all_nodes.return_value = [case]

    targets = _find_trace_targets("test_plan", "tests/test_motion.py", graph)
    assert targets == []


def test_find_trace_targets_file_level_match() -> None:
    """Matches by file_path alone when func_name is empty (bazel stub)."""
    case = _make_node("CASE_LLR-001", "CASE_LLR", properties={
        "file_path": "tests/test_motion.py",
        "line_traces": [{"symbol": "test_plan", "start": 10, "end": 20, "llr_ids": []}],
    })
    graph = MagicMock()
    graph.all_nodes.return_value = [case]

    targets = _find_trace_targets("", "tests/test_motion.py", graph)
    assert targets == ["CASE_LLR-001"]


# ── trace_to includes CASE + requirements ─────────────────────────────────

def test_record_results_trace_to_includes_case_ids() -> None:
    """RESULT.trace_to should include CASE IDs (for frontend status resolution)
    AND HLR/LLR requirement IDs (for traceability)."""
    case = _make_node("CASE_HLR-001", "CASE_HLR",
        trace_to=["HLR-001"],
        properties={
            "file_path": "tests/test_motion.py",
            "line_traces": [{"symbol": "test_plan", "start": 10, "end": 20, "llr_ids": ["LLR-001"]}],
        })
    graph = MagicMock()
    graph.all_nodes.return_value = [case]
    graph.node_sync.side_effect = lambda nid: case if nid == "CASE_HLR-001" else None

    parent_candidates = _find_trace_targets("test_plan", "tests/test_motion.py", graph)
    req_ids = _resolve_requirement_traces(parent_candidates, graph)
    trace_to = list(dict.fromkeys(parent_candidates + req_ids))

    # Must include CASE_HLR-001 (for frontend) AND HLR-001 (for traceability)
    assert "CASE_HLR-001" in trace_to
    assert "HLR-001" in trace_to


# ── record_results() with cached test results ─────────────────────────────

from unittest.mock import AsyncMock, patch

import pytest

from backend.crew.result_recorder import record_results


@pytest.mark.asyncio
async def test_record_results_uses_cached_test_results() -> None:
    """When last_state has test_results, those are used (not re-run)."""
    tr = SingleTestResult(
        test_id="tests/test_motion.py::test_plan",
        file_path="tests/test_motion.py",
        function_name="test_plan",
        status="passed",
    )
    last_state = MagicMock()
    last_state.test_results = [tr]

    case = _make_node("CASE_LLR-001", "CASE_LLR",
        trace_to=["LLR-001"],
        properties={
            "file_path": "tests/test_motion.py",
            "line_traces": [{"symbol": "test_plan", "start": 10, "end": 20, "llr_ids": []}],
        })
    graph = MagicMock()
    graph.all_nodes.return_value = [case]
    graph.node_sync.side_effect = lambda nid: case if nid == "CASE_LLR-001" else None
    graph.add_node = AsyncMock()

    with patch("backend.crew.result_recorder.run_and_parse_tests") as mock_run:
        results = await record_results(workspace=MagicMock(), graph=graph, last_state=last_state)

    # Should NOT have called run_and_parse_tests
    mock_run.assert_not_called()
    assert len(results) == 1
    assert results[0].status == "passed"


@pytest.mark.asyncio
async def test_record_results_creates_result_nodes_with_correct_properties() -> None:
    """RESULT nodes are created via graph.add_node with correct properties."""
    tr = SingleTestResult(
        test_id="tests/test_motion.py::test_plan",
        file_path="tests/test_motion.py",
        function_name="test_plan",
        status="failed",
    )
    last_state = MagicMock()
    last_state.test_results = [tr]

    case = _make_node("CASE_HLR-001", "CASE_HLR",
        trace_to=["HLR-001"],
        properties={
            "file_path": "tests/test_motion.py",
            "line_traces": [{"symbol": "test_plan", "start": 10, "end": 20, "llr_ids": []}],
        })
    graph = MagicMock()
    graph.all_nodes.return_value = [case]
    graph.node_sync.side_effect = lambda nid: case if nid == "CASE_HLR-001" else None
    graph.add_node = AsyncMock()

    await record_results(workspace=MagicMock(), graph=graph, last_state=last_state)

    assert graph.add_node.call_count == 1
    created_node = graph.add_node.call_args[0][0]
    assert created_node.node_type == "RESULT"
    assert created_node.properties["status"] == "failed"
    assert created_node.properties["test_id"] == "tests/test_motion.py::test_plan"
    assert created_node.properties["file_path"] == "tests/test_motion.py"
    assert created_node.properties["function_name"] == "test_plan"
    assert created_node.node_id.startswith("RESULT-")


@pytest.mark.asyncio
async def test_record_results_trace_to_includes_parents_and_requirements() -> None:
    """RESULT node trace_to includes both CASE/TEST ids AND requirement ids."""
    tr = SingleTestResult(
        test_id="tests/test_motion.py::test_plan",
        file_path="tests/test_motion.py",
        function_name="test_plan",
        status="passed",
    )
    last_state = MagicMock()
    last_state.test_results = [tr]

    case = _make_node("CASE_LLR-007", "CASE_LLR",
        trace_to=["LLR-007", "HLR-003"],
        properties={
            "file_path": "tests/test_motion.py",
            "line_traces": [{"symbol": "test_plan", "start": 1, "end": 10, "llr_ids": []}],
        })
    test_node = _make_node("TEST-007", "TEST", trace_to=["CASE_LLR-007"])
    graph = MagicMock()
    graph.all_nodes.return_value = [case, test_node]
    # node_sync called for TEST-007 to resolve its trace_to
    graph.node_sync.side_effect = lambda nid: test_node if nid == "TEST-007" else None
    graph.add_node = AsyncMock()

    await record_results(workspace=MagicMock(), graph=graph, last_state=last_state)

    created_node = graph.add_node.call_args[0][0]
    # Parent candidates = [TEST-007], req_ids = TEST-007.trace_to = [CASE_LLR-007]
    assert "TEST-007" in created_node.trace_to
    assert "CASE_LLR-007" in created_node.trace_to


@pytest.mark.asyncio
async def test_record_results_returns_list_of_single_test_result() -> None:
    """Return value is the list of SingleTestResult."""
    tr1 = SingleTestResult("t1::f1", "t1", "f1", "passed")
    tr2 = SingleTestResult("t2::f2", "t2", "f2", "failed")
    last_state = MagicMock()
    last_state.test_results = [tr1, tr2]

    graph = MagicMock()
    graph.all_nodes.return_value = []
    graph.add_node = AsyncMock()

    results = await record_results(workspace=MagicMock(), graph=graph, last_state=last_state)

    assert results == [tr1, tr2]
    assert isinstance(results[0], SingleTestResult)


# ── record_results() with empty results ───────────────────────────────────

@pytest.mark.asyncio
async def test_record_results_empty_results_returns_empty_list() -> None:
    """Returns empty list and does not create nodes when no results."""
    last_state = MagicMock()
    last_state.test_results = []

    graph = MagicMock()
    graph.add_node = AsyncMock()

    results = await record_results(workspace=MagicMock(), graph=graph, last_state=last_state)

    assert results == []
    graph.add_node.assert_not_called()


# ── record_results() without last_state ───────────────────────────────────

@pytest.mark.asyncio
async def test_record_results_no_last_state_calls_run_and_parse() -> None:
    """Falls back to run_and_parse_tests when last_state is None."""
    tr = SingleTestResult("tests/t.py::test_a", "tests/t.py", "test_a", "passed")
    graph = MagicMock()
    graph.all_nodes.return_value = []
    graph.add_node = AsyncMock()

    with patch("backend.crew.result_recorder.run_and_parse_tests", return_value=[tr]) as mock_run:
        results = await record_results(workspace=MagicMock(), graph=graph, last_state=None)

    mock_run.assert_called_once()
    assert len(results) == 1
    assert results[0].test_id == "tests/t.py::test_a"


@pytest.mark.asyncio
async def test_record_results_last_state_without_test_results_attr() -> None:
    """Falls back to run_and_parse_tests when last_state lacks test_results."""
    last_state = MagicMock(spec=[])  # spec=[] means no attributes at all
    graph = MagicMock()
    graph.all_nodes.return_value = []
    graph.add_node = AsyncMock()

    with patch("backend.crew.result_recorder.run_and_parse_tests", return_value=[]) as mock_run:
        results = await record_results(workspace=MagicMock(), graph=graph, last_state=last_state)

    mock_run.assert_called_once()
    assert results == []


# ── _resolve_requirement_traces edge cases ─────────────────────────────────

def test_resolve_requirement_traces_multiple_parents_different_reqs() -> None:
    """Multiple parent candidates tracing to different requirements."""
    case1 = _make_node("CASE-A", "CASE_HLR", trace_to=["HLR-001", "HLR-002"])
    case2 = _make_node("CASE-B", "CASE_LLR", trace_to=["LLR-010"])
    graph = MagicMock()
    graph.node_sync.side_effect = lambda nid: {
        "CASE-A": case1,
        "CASE-B": case2,
    }.get(nid)

    req_ids = _resolve_requirement_traces(["CASE-A", "CASE-B"], graph)

    assert "HLR-001" in req_ids
    assert "HLR-002" in req_ids
    assert "LLR-010" in req_ids
    # Verify order preserved (CASE-A reqs first, then CASE-B reqs)
    assert req_ids.index("HLR-001") < req_ids.index("LLR-010")


def test_resolve_requirement_traces_deduplication() -> None:
    """Duplicate requirement ids across parents are deduplicated."""
    case1 = _make_node("CASE-A", "CASE_HLR", trace_to=["HLR-001", "HLR-002"])
    case2 = _make_node("CASE-B", "CASE_LLR", trace_to=["HLR-001", "LLR-010"])
    graph = MagicMock()
    graph.node_sync.side_effect = lambda nid: {
        "CASE-A": case1,
        "CASE-B": case2,
    }.get(nid)

    req_ids = _resolve_requirement_traces(["CASE-A", "CASE-B"], graph)

    assert req_ids.count("HLR-001") == 1
    assert len(req_ids) == 3


def test_resolve_requirement_traces_node_not_found_skipped() -> None:
    """Node not found in graph is skipped silently."""
    case = _make_node("CASE-A", "CASE_HLR", trace_to=["HLR-001"])
    graph = MagicMock()
    graph.node_sync.side_effect = lambda nid: case if nid == "CASE-A" else None

    req_ids = _resolve_requirement_traces(["CASE-A", "MISSING-NODE"], graph)

    assert req_ids == ["HLR-001"]


def test_resolve_requirement_traces_empty_trace_to() -> None:
    """Node exists but has empty trace_to — contributes nothing."""
    case = _make_node("CASE-A", "CASE_HLR", trace_to=[])
    graph = MagicMock()
    graph.node_sync.return_value = case

    req_ids = _resolve_requirement_traces(["CASE-A"], graph)

    assert req_ids == []


# ── _find_trace_targets: multiple CASE nodes same file ─────────────────────

def test_find_trace_targets_multiple_cases_same_file() -> None:
    """Multiple CASE nodes matching the same file are all matched (per-file)."""
    case1 = _make_node("CASE_LLR-001", "CASE_LLR", properties={
        "file_path": "tests/test_motion.py",
        "line_traces": [{"symbol": "test_plan", "start": 10, "end": 20, "llr_ids": []}],
    })
    case2 = _make_node("CASE_HLR-002", "CASE_HLR", properties={
        "file_path": "tests/test_motion.py",
        "line_traces": [{"symbol": "test_execute", "start": 30, "end": 40, "llr_ids": []}],
    })
    graph = MagicMock()
    graph.all_nodes.return_value = [case1, case2]

    # Per-file match (empty func_name): both CASEs match
    targets = _find_trace_targets("", "tests/test_motion.py", graph)
    assert "CASE_LLR-001" in targets
    assert "CASE_HLR-002" in targets
    assert len(targets) == 2


def test_find_trace_targets_multiple_cases_same_file_per_function() -> None:
    """Per-function match only returns CASE with the matching symbol."""
    case1 = _make_node("CASE_LLR-001", "CASE_LLR", properties={
        "file_path": "tests/test_motion.py",
        "line_traces": [{"symbol": "test_plan", "start": 10, "end": 20, "llr_ids": []}],
    })
    case2 = _make_node("CASE_HLR-002", "CASE_HLR", properties={
        "file_path": "tests/test_motion.py",
        "line_traces": [{"symbol": "test_execute", "start": 30, "end": 40, "llr_ids": []}],
    })
    graph = MagicMock()
    graph.all_nodes.return_value = [case1, case2]

    targets = _find_trace_targets("test_plan", "tests/test_motion.py", graph)
    assert targets == ["CASE_LLR-001"]


# ── record_results with multiple results ───────────────────────────────────

@pytest.mark.asyncio
async def test_record_results_multiple_results_creates_multiple_nodes() -> None:
    """Each SingleTestResult creates a separate RESULT node."""
    tr1 = SingleTestResult("t/a.py::test_a", "t/a.py", "test_a", "passed")
    tr2 = SingleTestResult("t/b.py::test_b", "t/b.py", "test_b", "failed")
    last_state = MagicMock()
    last_state.test_results = [tr1, tr2]

    graph = MagicMock()
    graph.all_nodes.return_value = []
    graph.add_node = AsyncMock()

    results = await record_results(workspace=MagicMock(), graph=graph, last_state=last_state)

    assert graph.add_node.call_count == 2
    assert len(results) == 2

    node_ids = [call[0][0].node_id for call in graph.add_node.call_args_list]
    assert len(set(node_ids)) == 2  # Distinct node IDs


@pytest.mark.asyncio
async def test_record_results_parent_id_set_from_first_candidate() -> None:
    """parent_id is set to the first element of parent_candidates."""
    tr = SingleTestResult("tests/t.py::test_x", "tests/t.py", "test_x", "passed")
    last_state = MagicMock()
    last_state.test_results = [tr]

    case = _make_node("CASE_LLR-001", "CASE_LLR",
        trace_to=[],
        properties={
            "file_path": "tests/t.py",
            "line_traces": [{"symbol": "test_x", "start": 1, "end": 5, "llr_ids": []}],
        })
    graph = MagicMock()
    graph.all_nodes.return_value = [case]
    graph.node_sync.return_value = case
    graph.add_node = AsyncMock()

    await record_results(workspace=MagicMock(), graph=graph, last_state=last_state)

    created_node = graph.add_node.call_args[0][0]
    assert created_node.parent_id == "CASE_LLR-001"


# ── run_and_parse_tests: fresh evidence guarantee ──────────────────────────

from backend.crew.result_recorder import (
    purge_stale_test_artifacts,
    run_and_parse_tests,
)


def _stale_testlog(workspace: Path, target: str, xml: str) -> Path:
    """Write a bazel-testlogs test.xml simulating a leftover prior run."""
    d = workspace / "bazel-testlogs" / "tests" / target
    d.mkdir(parents=True, exist_ok=True)
    path = d / "test.xml"
    path.write_text(xml)
    return path


_PASSING_XML = (
    '<testsuite tests="1">\n'
    '  <testcase classname="tests.test_x" name="test_old" time="0.1"/>\n'
    '</testsuite>\n'
)


def test_purge_stale_test_artifacts_removes_all(tmp_path: Path) -> None:
    """Purge deletes testlogs XML, coverage.lcov, junit XML, and LCOV report."""
    stale_xml = _stale_testlog(tmp_path, "test_x", _PASSING_XML)
    (tmp_path / "coverage.lcov").write_text("SF:src/foo.py\n")
    (tmp_path / "coverage-test-results.xml").write_text("<testsuite/>")
    lcov_dir = tmp_path / "bazel-out" / "_coverage"
    lcov_dir.mkdir(parents=True)
    report = lcov_dir / "_coverage_report.dat"
    report.write_text("SF:src/foo.py\n")

    purge_stale_test_artifacts(tmp_path)

    assert not stale_xml.exists()
    assert not (tmp_path / "coverage.lcov").exists()
    assert not (tmp_path / "coverage-test-results.xml").exists()
    assert not report.exists()


def test_purge_stale_test_artifacts_empty_workspace(tmp_path: Path) -> None:
    """Purge on a workspace with no artifacts is a no-op, not an error."""
    purge_stale_test_artifacts(tmp_path)


@patch("backend.crew.result_recorder.init_bazel_workspace")
@patch("backend.crew.result_recorder.subprocess.run")
def test_run_and_parse_tests_stale_xml_after_failed_bazel_raises(
    mock_run: MagicMock, mock_init: MagicMock, tmp_path: Path,
) -> None:
    """Nonzero bazel exit with only pre-existing XML raises — never stale results."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("def test_old(): pass\n")
    _stale_testlog(tmp_path, "test_x", _PASSING_XML)

    mock_run.return_value = MagicMock(
        returncode=1, stdout="", stderr="ERROR: build failed: syntax error\n",
    )
    with pytest.raises(RuntimeError, match="bazel test failed"):
        run_and_parse_tests(tmp_path)


@patch("backend.crew.result_recorder.init_bazel_workspace")
@patch("backend.crew.result_recorder.subprocess.run")
def test_run_and_parse_tests_regenerates_build_and_parses_fresh(
    mock_run: MagicMock, mock_init: MagicMock, tmp_path: Path,
) -> None:
    """init_bazel_workspace runs before bazel, stale XML is purged, fresh XML parsed."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("def test_fresh(): pass\n")
    _stale_testlog(tmp_path, "test_x", _PASSING_XML)

    def fake_bazel(cmd: list[str], **kwargs: Any) -> MagicMock:
        assert mock_init.called, "BUILD files must be regenerated before bazel runs"
        assert not (
            tmp_path / "bazel-testlogs" / "tests" / "test_x" / "test.xml"
        ).exists(), "stale XML must be purged before bazel runs"
        _stale_testlog(
            tmp_path, "test_x",
            '<testsuite tests="1">\n'
            '  <testcase classname="tests.test_x" name="test_fresh" time="0.1"/>\n'
            '</testsuite>\n',
        )
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = fake_bazel
    results = run_and_parse_tests(tmp_path)

    mock_init.assert_called_once_with(tmp_path)
    assert [r.function_name for r in results] == ["test_fresh"]


@patch("backend.crew.result_recorder.init_bazel_workspace")
@patch("backend.crew.result_recorder.subprocess.run")
def test_run_and_parse_tests_nonzero_exit_with_fresh_results_returns_them(
    mock_run: MagicMock, mock_init: MagicMock, tmp_path: Path,
) -> None:
    """Bazel exit 3 (tests failed) with freshly written XML returns those results."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("def test_bad(): assert False\n")

    def fake_bazel(cmd: list[str], **kwargs: Any) -> MagicMock:
        _stale_testlog(
            tmp_path, "test_x",
            '<testsuite tests="1">\n'
            '  <testcase classname="tests.test_x" name="test_bad" time="0.1">\n'
            '    <failure message="assert False"/>\n'
            '  </testcase>\n'
            '</testsuite>\n',
        )
        return MagicMock(returncode=3, stdout="", stderr="")

    mock_run.side_effect = fake_bazel
    results = run_and_parse_tests(tmp_path)
    assert len(results) == 1
    assert results[0].status == "failed"


@patch("backend.crew.result_recorder.init_bazel_workspace")
@patch("backend.crew.result_recorder.subprocess.run")
def test_run_and_parse_tests_no_test_files_returns_empty(
    mock_run: MagicMock, mock_init: MagicMock, tmp_path: Path,
) -> None:
    """No test files: returns [] without touching bazel."""
    (tmp_path / "tests").mkdir()
    assert run_and_parse_tests(tmp_path) == []
    mock_run.assert_not_called()
    mock_init.assert_not_called()


@pytest.mark.asyncio
async def test_record_results_no_parent_candidates_sets_parent_none() -> None:
    """parent_id is None when no CASE/TEST candidates match."""
    tr = SingleTestResult("tests/orphan.py::test_orphan", "tests/orphan.py", "test_orphan", "passed")
    last_state = MagicMock()
    last_state.test_results = [tr]

    graph = MagicMock()
    graph.all_nodes.return_value = []
    graph.add_node = AsyncMock()

    await record_results(workspace=MagicMock(), graph=graph, last_state=last_state)

    created_node = graph.add_node.call_args[0][0]
    assert created_node.parent_id is None
    assert created_node.trace_to == []
