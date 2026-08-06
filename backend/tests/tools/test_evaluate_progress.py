"""Tests for backend.tools.evaluate_progress module."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from backend.tools.evaluate_progress import EvaluateProgressTool
from backend.workspace.result_recorder import SingleTestResult
from backend.workspace.scanner import FileState
from backend.workspace.test_reports import LcovResult

# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_graph(llr_ids: list[str] | None = None) -> MagicMock:
    """Return a mock ProjectGraph with optional LLR nodes."""
    graph = MagicMock()
    nodes = []
    for nid in (llr_ids or []):
        n = SimpleNamespace(node_id=nid, node_type="LLR")
        nodes.append(n)
    graph.all_nodes.return_value = nodes
    return graph


def _make_test_result(
    test_id: str = "tests/test_a.py::test_x",
    status: str = "passed",
) -> SingleTestResult:
    return SingleTestResult(
        test_id=test_id,
        file_path="tests/test_a.py",
        function_name="test_x",
        status=status,
    )


def _make_lcov(
    line_pct: float | None = 85.0,
    branch_pct: float | None = 70.0,
) -> LcovResult:
    return LcovResult(
        line_pct=line_pct,
        branch_pct=branch_pct,
        missing="",
        by_file={"src/foo.py": 85.0},
        uncovered_lines={"src/foo.py": [10, 20]},
    )


def _source_file_state(
    path: str = "src/foo.py",
    total_functions: int = 2,
    traced_functions: int = 2,
) -> FileState:
    return FileState(
        path=path,
        total_functions=total_functions,
        traced_functions=traced_functions,
    )


# ── _execute happy path ─────────────────────────────────────────────────────

@patch("backend.tools.evaluate_progress.run_async")
def test_execute_happy_path(mock_run_async: MagicMock) -> None:
    """_execute delegates to run_async and returns its result."""
    expected = json.dumps({"score": 0.7, "gap_count": 0})
    mock_run_async.return_value = expected

    tool = EvaluateProgressTool(workspace="/tmp/ws", graph=MagicMock())
    result = tool._execute(message="did stuff")

    assert result == expected
    mock_run_async.assert_called_once()


# ── _async_evaluate with no gaps ─────────────────────────────────────────────

@pytest.mark.asyncio
@patch("backend.workspace.scanner.scan_files")
@patch("backend.workspace.scanner._run_tests_and_coverage")
@patch("backend.codegen.gap_finder.find_gaps", return_value=[])
@patch("backend.codegen.mission_agent.compute_value", return_value=1.0)
@patch("backend.codegen.mission_agent._score_breakdown", return_value="Tests: 1/1 pass")
@patch("backend.codegen.mission_agent.format_gaps", return_value="No gaps remaining — all requirements satisfied.")
@patch("backend.server.forge_logger.forge_logger")
async def test_async_evaluate_no_gaps(
    _mock_logger: MagicMock,
    mock_format_gaps: MagicMock,
    mock_breakdown: MagicMock,
    mock_compute: MagicMock,
    mock_find_gaps: MagicMock,
    mock_run_tests: MagicMock,
    mock_scan: MagicMock,
) -> None:
    """When no gaps, all_gaps_closed should be True."""
    mock_scan.return_value = (
        {"src/foo.py": _source_file_state()},
        {"tests/test_foo.py": _source_file_state("tests/test_foo.py")},
    )
    mock_run_tests.return_value = (
        [_make_test_result()],
        _make_lcov(),
        "",
    )

    graph = _make_graph(["LLR-001"])
    tool = EvaluateProgressTool(workspace="/tmp/ws", graph=graph)
    raw = await tool._async_evaluate("check")
    result = json.loads(raw)

    assert result["all_gaps_closed"] is True
    assert result["gap_count"] == 0
    assert result["score"] == 1.0
    assert result["score_pct"] == "100%"
    assert "test_summary" in result
    assert result["test_summary"]["total"] == 1
    assert result["test_summary"]["passed"] == 1
    assert result["test_summary"]["failed"] == 0
    assert "coverage" in result
    assert result["coverage"]["statement_pct"] == 85.0
    assert result["coverage"]["branch_pct"] == 70.0
    assert "breakdown" in result
    assert "gaps" in result


# ── _async_evaluate with gaps ────────────────────────────────────────────────

@pytest.mark.asyncio
@patch("backend.workspace.scanner.scan_files")
@patch("backend.workspace.scanner._run_tests_and_coverage")
@patch("backend.codegen.gap_finder.find_gaps")
@patch("backend.codegen.mission_agent.compute_value", return_value=0.5)
@patch("backend.codegen.mission_agent._score_breakdown", return_value="Tests: 1/2 pass")
@patch("backend.codegen.mission_agent.format_gaps", return_value="### FAILING_TESTS (1)\n- test_a")
@patch("backend.server.forge_logger.forge_logger")
async def test_async_evaluate_with_gaps(
    _mock_logger: MagicMock,
    mock_format_gaps: MagicMock,
    mock_breakdown: MagicMock,
    mock_compute: MagicMock,
    mock_find_gaps: MagicMock,
    mock_run_tests: MagicMock,
    mock_scan: MagicMock,
) -> None:
    """When gaps exist, all_gaps_closed should be False and gap_count > 0."""
    from backend.codegen.gap_finder import Gap, GapKind

    mock_scan.return_value = (
        {"src/foo.py": _source_file_state()},
        {"tests/test_foo.py": _source_file_state("tests/test_foo.py")},
    )
    mock_run_tests.return_value = (
        [_make_test_result(status="passed"), _make_test_result(test_id="tests/test_a.py::test_y", status="failed")],
        _make_lcov(),
        "",
    )
    mock_find_gaps.return_value = [
        Gap(kind=GapKind.FAILING_TESTS, node_id="", file_path="tests/test_a.py",
            details="test_y failed"),
        Gap(kind=GapKind.UNCOVERED_REQUIREMENT, node_id="LLR-001", file_path="",
            details="No passing traced test"),
    ]

    graph = _make_graph(["LLR-001"])
    tool = EvaluateProgressTool(workspace="/tmp/ws", graph=graph)
    raw = await tool._async_evaluate("check")
    result = json.loads(raw)

    assert result["all_gaps_closed"] is False
    assert result["gap_count"] == 2
    assert result["score"] == 0.5
    assert result["test_summary"]["passed"] == 1
    assert result["test_summary"]["failed"] == 1


# ── _async_evaluate with None coverage ───────────────────────────────────────

@pytest.mark.asyncio
@patch("backend.workspace.scanner.scan_files")
@patch("backend.workspace.scanner._run_tests_and_coverage")
@patch("backend.codegen.gap_finder.find_gaps", return_value=[])
@patch("backend.codegen.mission_agent.compute_value", return_value=0.0)
@patch("backend.codegen.mission_agent._score_breakdown", return_value="Tests: 0/0")
@patch("backend.codegen.mission_agent.format_gaps", return_value="No gaps")
@patch("backend.server.forge_logger.forge_logger")
async def test_async_evaluate_none_coverage(
    _mock_logger: MagicMock,
    _mock_format: MagicMock,
    _mock_breakdown: MagicMock,
    _mock_compute: MagicMock,
    _mock_find: MagicMock,
    mock_run_tests: MagicMock,
    mock_scan: MagicMock,
) -> None:
    """When coverage is None (no coverage data), output should still be valid JSON."""
    mock_scan.return_value = (
        {"src/foo.py": _source_file_state()},
        {},
    )
    mock_run_tests.return_value = (
        [],
        LcovResult(),  # all None/empty
        "",
    )

    tool = EvaluateProgressTool(workspace="/tmp/ws", graph=_make_graph())
    raw = await tool._async_evaluate("")
    result = json.loads(raw)

    assert result["coverage"]["statement_pct"] is None
    assert result["coverage"]["branch_pct"] is None
    assert result["test_summary"]["total"] == 0
    assert "score" in result


# ── _async_evaluate with empty workspace ─────────────────────────────────────

@pytest.mark.asyncio
@patch("backend.workspace.scanner.scan_files")
@patch("backend.workspace.scanner._run_tests_and_coverage")
@patch("backend.codegen.gap_finder.find_gaps", return_value=[])
@patch("backend.codegen.mission_agent.compute_value", return_value=0.0)
@patch("backend.codegen.mission_agent._score_breakdown", return_value="Tests: 0/0")
@patch("backend.codegen.mission_agent.format_gaps", return_value="No gaps")
@patch("backend.server.forge_logger.forge_logger")
async def test_async_evaluate_empty_workspace(
    _mock_logger: MagicMock,
    _mock_format: MagicMock,
    _mock_breakdown: MagicMock,
    _mock_compute: MagicMock,
    _mock_find: MagicMock,
    mock_run_tests: MagicMock,
    mock_scan: MagicMock,
) -> None:
    """Empty workspace (no source/test files) should produce valid output."""
    mock_scan.return_value = ({}, {})
    mock_run_tests.return_value = ([], LcovResult(), "")

    tool = EvaluateProgressTool(workspace="/tmp/ws", graph=_make_graph())
    raw = await tool._async_evaluate("")
    result = json.loads(raw)

    assert result["gap_count"] == 0
    assert result["all_gaps_closed"] is True
    assert result["test_summary"]["total"] == 0
    assert result["test_summary"]["passed"] == 0
    assert result["test_summary"]["failed"] == 0
    assert result["coverage"]["statement_pct"] is None


# ── Error handling if scan_files raises ──────────────────────────────────────

@pytest.mark.asyncio
@patch("backend.workspace.scanner.scan_files")
@patch("backend.server.forge_logger.forge_logger")
async def test_async_evaluate_scan_files_error(
    _mock_logger: MagicMock,
    mock_scan: MagicMock,
) -> None:
    """If scan_files raises, the exception should propagate."""
    mock_scan.side_effect = OSError("workspace gone")

    tool = EvaluateProgressTool(workspace="/tmp/ws", graph=_make_graph())
    with pytest.raises(OSError, match="workspace gone"):
        await tool._async_evaluate("")


# ── JSON output has all expected keys ────────────────────────────────────────

@pytest.mark.asyncio
@patch("backend.workspace.scanner.scan_files")
@patch("backend.workspace.scanner._run_tests_and_coverage")
@patch("backend.codegen.gap_finder.find_gaps", return_value=[])
@patch("backend.codegen.mission_agent.compute_value", return_value=0.85)
@patch("backend.codegen.mission_agent._score_breakdown", return_value="ok")
@patch("backend.codegen.mission_agent.format_gaps", return_value="No gaps")
@patch("backend.server.forge_logger.forge_logger")
async def test_json_output_has_all_expected_keys(
    _mock_logger: MagicMock,
    _mock_format: MagicMock,
    _mock_breakdown: MagicMock,
    _mock_compute: MagicMock,
    _mock_find: MagicMock,
    mock_run_tests: MagicMock,
    mock_scan: MagicMock,
) -> None:
    """Output JSON must contain all required top-level keys."""
    mock_scan.return_value = (
        {"src/a.py": _source_file_state("src/a.py")},
        {},
    )
    mock_run_tests.return_value = (
        [_make_test_result()],
        _make_lcov(line_pct=90.0, branch_pct=80.0),
        "",
    )

    tool = EvaluateProgressTool(workspace="/tmp/ws", graph=_make_graph())
    raw = await tool._async_evaluate("")
    result = json.loads(raw)

    expected_keys = {"score", "score_pct", "gap_count", "all_gaps_closed",
                     "breakdown", "gaps", "test_summary", "coverage"}
    assert set(result.keys()) == expected_keys


# ── _execute via _run (LangChain entry point) ───────────────────────────────

@patch("backend.tools.evaluate_progress.run_async")
@patch("backend.server.forge_logger.forge_logger")
def test_execute_via_run(
    _mock_logger: MagicMock,
    mock_run_async: MagicMock,
) -> None:
    """_run delegates to _execute which calls run_async."""
    payload = json.dumps({"score": 0.5, "gap_count": 1})
    mock_run_async.return_value = payload

    tool = EvaluateProgressTool(workspace="/tmp/ws", graph=MagicMock())
    result = tool._run(message="test")

    assert result == payload


# ── Score rounding ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
@patch("backend.workspace.scanner.scan_files")
@patch("backend.workspace.scanner._run_tests_and_coverage")
@patch("backend.codegen.gap_finder.find_gaps", return_value=[])
@patch("backend.codegen.mission_agent.compute_value", return_value=0.33333333)
@patch("backend.codegen.mission_agent._score_breakdown", return_value="ok")
@patch("backend.codegen.mission_agent.format_gaps", return_value="No gaps")
@patch("backend.server.forge_logger.forge_logger")
async def test_score_is_rounded_to_3_decimals(
    _mock_logger: MagicMock,
    _mock_format: MagicMock,
    _mock_breakdown: MagicMock,
    _mock_compute: MagicMock,
    _mock_find: MagicMock,
    mock_run_tests: MagicMock,
    mock_scan: MagicMock,
) -> None:
    """Score should be rounded to 3 decimal places."""
    mock_scan.return_value = ({}, {})
    mock_run_tests.return_value = ([], LcovResult(), "")

    tool = EvaluateProgressTool(workspace="/tmp/ws", graph=_make_graph())
    raw = await tool._async_evaluate("")
    result = json.loads(raw)

    assert result["score"] == 0.333


# ── Test summary counts failed and error statuses ───────────────────────────

@pytest.mark.asyncio
@patch("backend.workspace.scanner.scan_files")
@patch("backend.workspace.scanner._run_tests_and_coverage")
@patch("backend.codegen.gap_finder.find_gaps", return_value=[])
@patch("backend.codegen.mission_agent.compute_value", return_value=0.0)
@patch("backend.codegen.mission_agent._score_breakdown", return_value="ok")
@patch("backend.codegen.mission_agent.format_gaps", return_value="gaps")
@patch("backend.server.forge_logger.forge_logger")
async def test_test_summary_counts_errors_as_failed(
    _mock_logger: MagicMock,
    _mock_format: MagicMock,
    _mock_breakdown: MagicMock,
    _mock_compute: MagicMock,
    _mock_find: MagicMock,
    mock_run_tests: MagicMock,
    mock_scan: MagicMock,
) -> None:
    """Both 'failed' and 'error' statuses count toward the failed total."""
    mock_scan.return_value = ({}, {})
    tests = [
        _make_test_result(test_id="t1", status="passed"),
        _make_test_result(test_id="t2", status="failed"),
        _make_test_result(test_id="t3", status="error"),
        _make_test_result(test_id="t4", status="skipped"),
    ]
    mock_run_tests.return_value = (tests, LcovResult(), "")

    tool = EvaluateProgressTool(workspace="/tmp/ws", graph=_make_graph())
    raw = await tool._async_evaluate("")
    result = json.loads(raw)

    assert result["test_summary"]["total"] == 4
    assert result["test_summary"]["passed"] == 1
    assert result["test_summary"]["failed"] == 2  # failed + error


# ── Tool init stores workspace and graph ─────────────────────────────────────

def test_tool_init_stores_attributes() -> None:
    """Constructor should store workspace and graph via object.__setattr__."""
    graph = MagicMock()
    tool = EvaluateProgressTool(workspace="/some/path", graph=graph)
    assert tool._workspace == "/some/path"
    assert tool._graph is graph


# ── Tool metadata ────────────────────────────────────────────────────────────

def test_tool_name_and_description() -> None:
    """Tool should have expected name and a non-empty description."""
    tool = EvaluateProgressTool(workspace="/tmp", graph=MagicMock())
    assert tool.name == "evaluate_progress"
    assert len(tool.description) > 10
