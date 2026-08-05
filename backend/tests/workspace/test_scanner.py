"""Tests for workspace_scanner module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.workspace.result_recorder import SingleTestResult
from backend.workspace.scanner import (
    LcovResult,
    WorkspaceState,
    _analyse_file,
    _discover_py_files,
    _run_tests_and_coverage,
    scan_files,
    scan_workspace,
)
from backend.workspace.test_reports import (
    merge_test_results as _merge_test_results,
)
from backend.workspace.test_reports import (
    parse_bazel_testlogs as _parse_bazel_testlogs,
)
from backend.workspace.test_reports import (
    parse_junit_xml as _parse_junit_xml,
)
from backend.workspace.test_reports import (
    parse_lcov_coverage as _parse_lcov_coverage,
)

# ── _discover_py_files ───────────────────────────────────────────────────────

def test_discover_py_files_excludes_init_and_pycache(tmp_path: Path) -> None:
    """Should find .py files but skip __init__.py and __pycache__."""
    (tmp_path / "foo.py").write_text("x = 1")
    (tmp_path / "__init__.py").write_text("")
    cache_dir = tmp_path / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "bar.py").write_text("")

    result = _discover_py_files(tmp_path)
    names = [p.name for p in result]
    assert "foo.py" in names
    assert "__init__.py" not in names
    assert "bar.py" not in names


def test_discover_py_files_nonexistent_dir(tmp_path: Path) -> None:
    """Should return empty list for a directory that doesn't exist."""
    assert _discover_py_files(tmp_path / "nope") == []


# ── _analyse_file ────────────────────────────────────────────────────────────

def test_analyse_file_with_traces(tmp_path: Path) -> None:
    """Should detect traced and untraced functions."""
    code = (
        '@traces("LLR-001")\n'
        "def traced():\n"
        "    pass\n"
        "\n"
        "def untraced():\n"
        "    pass\n"
    )
    f = tmp_path / "example.py"
    f.write_text(code)

    state = _analyse_file(f, "src/example.py")
    assert state.path == "src/example.py"
    assert state.total_functions == 2
    assert state.traced_functions == 1
    assert len(state.traces) == 1
    assert len(state.untraced_functions) == 1
    assert state.untraced_functions[0].name == "untraced"


def test_analyse_file_with_syntax_error(tmp_path: Path) -> None:
    """Should capture syntax errors in FileState.syntax_error."""
    code = 'def bad(\n    x = f"{foo)s}"\n'
    f = tmp_path / "broken.py"
    f.write_text(code)

    state = _analyse_file(f, "tests/broken.py")
    assert state.syntax_error != ""
    assert state.total_functions == 0


def test_analyse_file_valid_has_no_syntax_error(tmp_path: Path) -> None:
    """Valid Python should have empty syntax_error."""
    f = tmp_path / "ok.py"
    f.write_text("def hello():\n    pass\n")

    state = _analyse_file(f, "src/ok.py")
    assert state.syntax_error == ""


# ── scan_files ───────────────────────────────────────────────────────────────

def test_scan_files_finds_src_and_tests(tmp_path: Path) -> None:
    """Should categorise files into source_files and test_files."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "mod.py").write_text('@traces("LLR-1")\ndef hello():\n    pass\n')

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_mod.py").write_text('@traces("LLR-1")\ndef test_hello():\n    pass\n')

    source_files, test_files = scan_files(tmp_path)
    assert "src/mod.py" in source_files
    assert "tests/test_mod.py" in test_files


def test_scan_files_empty_workspace(tmp_path: Path) -> None:
    """Should return empty dicts when no src/ or tests/ exist."""
    source_files, test_files = scan_files(tmp_path)
    assert source_files == {}
    assert test_files == {}


# ── _parse_junit_xml ─────────────────────────────────────────────────────────

def test_parse_junit_xml_passed(tmp_path: Path) -> None:
    """Should parse passing tests from JUnit XML."""
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<testsuite tests="2">\n'
        '  <testcase classname="tests.test_x" name="test_a" time="0.1"/>\n'
        '  <testcase classname="tests.test_x" name="test_b" time="0.2"/>\n'
        '</testsuite>\n'
    )
    f = tmp_path / "results.xml"
    f.write_text(xml)

    results = _parse_junit_xml(f)
    assert len(results) == 2
    assert results[0].status == "passed"
    assert results[0].function_name == "test_a"
    assert results[0].file_path == "tests/test_x.py"


def test_parse_junit_xml_failures(tmp_path: Path) -> None:
    """Should detect failed tests from JUnit XML."""
    xml = (
        '<testsuite tests="1">\n'
        '  <testcase classname="tests.test_x" name="test_bad" time="0.1">\n'
        '    <failure message="assert False"/>\n'
        '  </testcase>\n'
        '</testsuite>\n'
    )
    f = tmp_path / "results.xml"
    f.write_text(xml)

    results = _parse_junit_xml(f)
    assert len(results) == 1
    assert results[0].status == "failed"


def test_parse_junit_xml_missing_file(tmp_path: Path) -> None:
    """Should return empty list for non-existent file."""
    assert _parse_junit_xml(tmp_path / "nope.xml") == []


def test_parse_junit_xml_bazel_stub_format(tmp_path: Path) -> None:
    """Should handle bazel stub XML with name-only (no classname)."""
    xml = (
        '<testsuite tests="1">\n'
        '  <testcase name="tests/test_foo" status="run" time="0.5"/>\n'
        '</testsuite>\n'
    )
    f = tmp_path / "test.xml"
    f.write_text(xml)

    results = _parse_junit_xml(f)
    assert len(results) == 1
    assert results[0].file_path == "tests/test_foo.py"
    assert results[0].function_name == ""
    assert results[0].status == "passed"
    assert results[0].test_id == "tests/test_foo.py"


# ── _run_tests_and_coverage ─────────────────────────────────────────────────

@patch("backend.workspace.scanner._run_coverage_py")
@patch("backend.workspace.scanner.init_bazel_workspace")
@patch("backend.workspace.scanner.subprocess.run")
def test_run_tests_parses_bazel_testlogs(
    mock_run: MagicMock, mock_init: MagicMock, mock_cov: MagicMock, tmp_path: Path,
) -> None:
    """Should parse results freshly written by the bazel run."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("")

    def fake_bazel(cmd: list[str], **kwargs: object) -> MagicMock:
        # BUILD files must be regenerated before bazel runs
        assert mock_init.called
        testlog_dir = tmp_path / "bazel-testlogs" / "tests" / "test_x"
        testlog_dir.mkdir(parents=True)
        (testlog_dir / "test.xml").write_text(
            '<testsuite tests="2">\n'
            '  <testcase classname="tests.test_x" name="test_a" time="0.1"/>\n'
            '  <testcase classname="tests.test_x" name="test_b" time="0.2"/>\n'
            '</testsuite>\n'
        )
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = fake_bazel
    mock_cov.return_value = LcovResult()

    results, lcov, error = _run_tests_and_coverage(tmp_path)
    assert len(results) == 2
    assert error == ""
    assert results[0].function_name == "test_a"
    mock_init.assert_called_once_with(tmp_path)
    cmd = mock_run.call_args_list[0][0][0]
    assert cmd[0] == "bazel"


@patch("backend.workspace.scanner._run_coverage_py")
@patch("backend.workspace.scanner.init_bazel_workspace")
@patch("backend.workspace.scanner.subprocess.run")
def test_run_tests_stale_xml_after_failed_bazel_is_error(
    mock_run: MagicMock, mock_init: MagicMock, mock_cov: MagicMock, tmp_path: Path,
) -> None:
    """Nonzero bazel exit with only pre-existing XML yields an error, not results."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("")

    # Leftover green XML from a prior run — must NOT be reported as current
    testlog_dir = tmp_path / "bazel-testlogs" / "tests" / "test_x"
    testlog_dir.mkdir(parents=True)
    stale_xml = testlog_dir / "test.xml"
    stale_xml.write_text(
        '<testsuite tests="1">\n'
        '  <testcase classname="tests.test_x" name="test_old_green" time="0.1"/>\n'
        '</testsuite>\n'
    )

    mock_run.return_value = MagicMock(
        returncode=1, stdout="", stderr="ERROR: build failed: syntax error\n",
    )
    results, lcov, error = _run_tests_and_coverage(tmp_path)
    assert results == []
    assert error != ""
    assert not stale_xml.exists()
    mock_cov.assert_not_called()


def test_parse_bazel_testlogs_multiple_targets(tmp_path: Path) -> None:
    """Should aggregate results from multiple test target XML files."""
    # Create test files on disk (required for stale-result filtering)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    for name in ["test_a", "test_b"]:
        (tests_dir / f"{name}.py").write_text("def test_func(): pass\n")

    testlogs = tmp_path / "bazel-testlogs" / "tests"
    for name in ["test_a", "test_b"]:
        d = testlogs / name
        d.mkdir(parents=True)
        (d / "test.xml").write_text(
            f'<testsuite tests="1">\n'
            f'  <testcase classname="tests.{name}" name="test_func" time="0.1"/>\n'
            f'</testsuite>\n'
        )

    results = _parse_bazel_testlogs(tmp_path)
    assert len(results) == 2


def test_run_tests_no_test_files(tmp_path: Path) -> None:
    """Should return empty when no test files exist."""
    (tmp_path / "tests").mkdir()
    results, lcov, error = _run_tests_and_coverage(tmp_path)
    assert results == []
    assert lcov.line_pct is None


def test_run_tests_no_tests_dir(tmp_path: Path) -> None:
    """Should return empty when tests/ doesn't exist."""
    results, lcov, error = _run_tests_and_coverage(tmp_path)
    assert results == []
    assert lcov.line_pct is None


# ── scan_workspace ───────────────────────────────────────────────────────────

@patch("backend.workspace.scanner._run_tests_and_coverage")
async def test_scan_workspace_returns_complete_state(
    mock_tests: MagicMock, tmp_path: Path,
) -> None:
    """Should return a WorkspaceState with files, results, and coverage."""
    mock_tests.return_value = ([], LcovResult(line_pct=75.0), "")

    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text('@traces("LLR-1")\ndef main():\n    pass\n')
    (tmp_path / "tests").mkdir()

    state = await scan_workspace(tmp_path)
    assert isinstance(state, WorkspaceState)
    assert "src/app.py" in state.source_files
    assert state.coverage_pct == 75.0
    assert state.test_results == []


@patch("backend.workspace.scanner.init_bazel_workspace")
@patch("backend.workspace.scanner.subprocess.run")
def test_run_tests_nonzero_exit_no_results_is_error(
    mock_run: MagicMock, mock_init: MagicMock, tmp_path: Path,
) -> None:
    """Non-zero exit with no parsed results should return test_run_error."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("")

    mock_run.return_value = MagicMock(
        returncode=1,
        stdout="",
        stderr="ModuleNotFoundError: No module named 'src.motion_planner'\n",
    )
    results, lcov, error = _run_tests_and_coverage(tmp_path)
    assert results == []
    assert "ModuleNotFoundError" in error


# ── _run_coverage_py: no stale-LCOV fallback ─────────────────────────────────


@patch("shutil.which", return_value=None)
def test_coverage_binary_missing_raises_instead_of_stale_lcov(
    mock_which: MagicMock, tmp_path: Path,
) -> None:
    """Missing coverage binary raises loudly — never parses leftover LCOV."""
    import pytest

    from backend.workspace.scanner import _run_coverage_py

    # Leftover LCOV report from a prior run — must NOT be silently reused
    lcov_dir = tmp_path / "bazel-out" / "_coverage"
    lcov_dir.mkdir(parents=True)
    (lcov_dir / "_coverage_report.dat").write_text("SF:src/foo.py\nLF:10\nLH:8\nend_of_record\n")

    with pytest.raises(RuntimeError, match="coverage binary not found"):
        _run_coverage_py(tmp_path)


@patch("backend.workspace.scanner._run_coverage_with_progress")
@patch("backend.workspace.scanner.subprocess.run")
@patch("shutil.which", return_value="/usr/bin/coverage")
def test_coverage_lcov_not_created_raises(
    mock_which: MagicMock, mock_run: MagicMock, mock_progress: MagicMock,
    tmp_path: Path,
) -> None:
    """Failed LCOV export raises loudly — never falls back to stale on-disk LCOV."""
    import subprocess as _subprocess

    import pytest

    from backend.workspace.scanner import _run_coverage_py

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("def test_a(): pass\n")

    mock_progress.return_value = _subprocess.CompletedProcess(
        args=[], returncode=0, stdout="", stderr="",
    )
    # LCOV export subprocess "succeeds" but produces no coverage.lcov file
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    with pytest.raises(RuntimeError, match="coverage.lcov"):
        _run_coverage_py(tmp_path)


# ── _parse_lcov_coverage ──────────────────────────────────────────────────────


def test_parse_lcov_coverage_full(tmp_path: Path) -> None:
    """Should parse LCOV report and return line coverage percentage."""
    lcov_dir = tmp_path / "bazel-out" / "_coverage"
    lcov_dir.mkdir(parents=True)
    (lcov_dir / "_coverage_report.dat").write_text(
        "SF:src/foo.py\n"
        "LF:10\n"
        "LH:8\n"
        "end_of_record\n"
        "SF:src/bar.py\n"
        "LF:10\n"
        "LH:10\n"
        "end_of_record\n"
    )
    lcov = _parse_lcov_coverage(tmp_path)
    assert lcov.line_pct == 90.0
    assert "src/foo.py" in lcov.by_file
    assert lcov.by_file["src/bar.py"] == 100.0


def test_parse_lcov_coverage_with_branches(tmp_path: Path) -> None:
    """Should parse branch (MC/DC) coverage from BRF/BRH lines."""
    lcov_dir = tmp_path / "bazel-out" / "_coverage"
    lcov_dir.mkdir(parents=True)
    (lcov_dir / "_coverage_report.dat").write_text(
        "SF:src/foo.py\n"
        "LF:10\n"
        "LH:10\n"
        "BRF:8\n"
        "BRH:6\n"
        "end_of_record\n"
    )
    lcov = _parse_lcov_coverage(tmp_path)
    assert lcov.line_pct == 100.0
    assert lcov.branch_pct == 75.0


def test_parse_lcov_coverage_no_report(tmp_path: Path) -> None:
    """Should return None when no LCOV report exists."""
    lcov = _parse_lcov_coverage(tmp_path)
    assert lcov.line_pct is None
    assert lcov.branch_pct is None


def test_parse_lcov_coverage_empty_project(tmp_path: Path) -> None:
    """Should return None line_pct when total_found is 0 (no measurable code)."""
    lcov_dir = tmp_path / "bazel-out" / "_coverage"
    lcov_dir.mkdir(parents=True)
    (lcov_dir / "_coverage_report.dat").write_text("")
    lcov = _parse_lcov_coverage(tmp_path)
    # No files parsed → no coverage data
    assert lcov.line_pct is None


@patch("backend.workspace.scanner._run_tests_and_coverage")
async def test_scan_workspace_captures_error(
    mock_tests: MagicMock, tmp_path: Path,
) -> None:
    """Should capture test run errors in WorkspaceState."""
    mock_tests.return_value = ([], LcovResult(), "pytest not found")

    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()

    state = await scan_workspace(tmp_path)
    assert state.test_run_error == "pytest not found"
    assert state.coverage_pct is None


# ── _merge_test_results ──────────────────────────────────────────────────────


def _result(test_id: str, status: str, error_message: str = "") -> SingleTestResult:
    """Create a minimal SingleTestResult for merge tests."""
    return SingleTestResult(
        test_id=test_id,
        file_path=test_id.split("::")[0],
        function_name=test_id.split("::")[-1],
        status=status,
        error_message=error_message,
    )


def test_merge_coverage_failure_overrides_bazel_pass() -> None:
    """Coverage catching a failure that bazel missed should override."""
    bazel = [_result("t::a", "passed")]
    cov = [_result("t::a", "failed", "AssertionError")]
    merged = _merge_test_results(bazel, cov)
    assert len(merged) == 1
    assert merged[0].status == "failed"


def test_merge_coverage_pass_overrides_bazel_error() -> None:
    """Coverage passing a test that bazel errored (e.g. sandbox env issue) should override.

    This is the bug that caused 22 phantom FAILING_TESTS gaps: bazel sandbox
    lacked numpy so all tests errored, but coverage pytest passed them fine.
    The old merge kept the bazel 'error' result, producing unfixable gaps.
    """
    bazel = [
        _result("t::a", "error", "ModuleNotFoundError: No module named 'numpy'"),
        _result("t::b", "error", "ModuleNotFoundError: No module named 'numpy'"),
        _result("t::c", "passed"),
    ]
    cov = [
        _result("t::a", "passed"),
        _result("t::b", "passed"),
        _result("t::c", "passed"),
    ]
    merged = _merge_test_results(bazel, cov)
    by_id = {r.test_id: r for r in merged}
    assert by_id["t::a"].status == "passed"
    assert by_id["t::b"].status == "passed"
    assert by_id["t::c"].status == "passed"


def test_merge_coverage_pass_overrides_bazel_failed() -> None:
    """Bazel failure that coverage can't reproduce should trust coverage."""
    bazel = [_result("t::a", "failed", "flaky assertion")]
    cov = [_result("t::a", "passed")]
    merged = _merge_test_results(bazel, cov)
    assert merged[0].status == "passed"


def test_merge_both_agree_keeps_bazel_for_richer_detail() -> None:
    """When both agree (both pass or both fail), keep bazel result."""
    bazel = [_result("t::a", "passed")]
    cov = [_result("t::a", "passed")]
    merged = _merge_test_results(bazel, cov)
    assert len(merged) == 1
    assert merged[0].status == "passed"


def test_merge_new_test_from_coverage_added() -> None:
    """Tests only in coverage run (not in bazel) should be added."""
    bazel = [_result("t::a", "passed")]
    cov = [_result("t::a", "passed"), _result("t::b", "failed", "new test")]
    merged = _merge_test_results(bazel, cov)
    assert len(merged) == 2
    by_id = {r.test_id: r for r in merged}
    assert by_id["t::b"].status == "failed"


def test_merge_empty_bazel_results() -> None:
    """When bazel produces no results, coverage results used entirely."""
    cov = [_result("t::a", "passed"), _result("t::b", "failed")]
    merged = _merge_test_results([], cov)
    assert len(merged) == 2


def test_merge_empty_coverage_results() -> None:
    """When coverage produces no results, bazel results kept as-is."""
    bazel = [_result("t::a", "passed"), _result("t::b", "error")]
    merged = _merge_test_results(bazel, [])
    assert len(merged) == 2
    assert merged[1].status == "error"


def test_merge_drops_bazel_file_stubs_when_coverage_has_results() -> None:
    """Bazel file-level error stubs are dropped when coverage has per-function results.

    This handles the numpy-in-bazel-sandbox scenario: bazel reports 27 file-level
    errors, but coverage.py shows all functions passing. The file-level stubs
    should be dropped since coverage provides authoritative function-level results.
    """
    # Bazel: file-level error stubs (no function_name)
    bazel_stub = SingleTestResult(
        test_id="tests/test_foo.py",
        file_path="tests/test_foo.py",
        function_name="",  # file-level stub
        status="error",
        error_message="ModuleNotFoundError: No module named 'numpy'",
    )
    # Coverage: per-function results for the same file
    cov_a = SingleTestResult(
        test_id="tests/test_foo.py::test_a",
        file_path="tests/test_foo.py",
        function_name="test_a",
        status="passed",
    )
    cov_b = SingleTestResult(
        test_id="tests/test_foo.py::test_b",
        file_path="tests/test_foo.py",
        function_name="test_b",
        status="passed",
    )
    merged = _merge_test_results([bazel_stub], [cov_a, cov_b])

    # The file-level error stub should be gone
    assert len(merged) == 2
    statuses = {r.status for r in merged}
    assert statuses == {"passed"}
    # No error/failed results
    assert not any(r.status in ("error", "failed") for r in merged)


# ── _parse_lcov_file (direct) ──────────────────────────────────────────────

from backend.workspace.test_reports import extract_error_summary as _extract_error_summary
from backend.workspace.test_reports import parse_lcov_file as _parse_lcov_file
from backend.workspace.test_reports import read_log_tail as _read_log_tail


def test_parse_lcov_file_tracks_uncovered_lines(tmp_path: Path) -> None:
    """DA lines with hit_count=0 should appear in uncovered_lines."""
    lcov = tmp_path / "cov.lcov"
    lcov.write_text(
        "SF:src/foo.py\n"
        "DA:10,1\n"
        "DA:20,0\n"
        "DA:30,0\n"
        "LF:3\n"
        "LH:1\n"
        "end_of_record\n"
    )
    result = _parse_lcov_file(lcov)
    assert result.uncovered_lines["src/foo.py"] == [20, 30]
    assert result.line_pct is not None
    assert result.line_pct < 100.0


def test_parse_lcov_file_empty_file(tmp_path: Path) -> None:
    """Empty LCOV file should return None coverage."""
    lcov = tmp_path / "empty.lcov"
    lcov.write_text("")
    result = _parse_lcov_file(lcov)
    assert result.line_pct is None


def test_parse_lcov_file_nonexistent(tmp_path: Path) -> None:
    """Missing file should return empty result."""
    result = _parse_lcov_file(tmp_path / "nope.lcov")
    assert result.line_pct is None


# ── _extract_error_summary ──────────────────────────────────────────────────


def test_extract_error_summary_with_error_block() -> None:
    """Should capture ERROR collecting block."""
    output = (
        "______ ERROR collecting tests/test_foo.py ______\n"
        "ImportError: No module named 'numpy'\n"
        "______ ERROR collecting tests/test_bar.py ______\n"
        "ImportError: No module named 'scipy'\n"
    )
    result = _extract_error_summary(output)
    assert "ImportError" in result


def test_extract_error_summary_fallback_to_tail() -> None:
    """With no ERROR blocks, should return last lines."""
    output = "line1\nline2\nline3\nline4\nline5\n"
    result = _extract_error_summary(output)
    assert "line5" in result


def test_extract_error_summary_empty_output() -> None:
    """Empty output returns default message."""
    result = _extract_error_summary("")
    assert "no output" in result


# ── _read_log_tail ──────────────────────────────────────────────────────────


def test_read_log_tail_existing_file(tmp_path: Path) -> None:
    """Should return last 50 non-empty lines."""
    log = tmp_path / "test.log"
    lines = [f"line {i}" for i in range(100)]
    log.write_text("\n".join(lines))
    result = _read_log_tail(log)
    assert "line 99" in result
    assert "line 49" not in result


def test_read_log_tail_nonexistent(tmp_path: Path) -> None:
    """Missing file returns empty string."""
    assert _read_log_tail(tmp_path / "nope.log") == ""
