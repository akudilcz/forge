"""Tests for backend.crew.test_parsers — JUnit/LCOV/bazel-testlog parsing."""

from __future__ import annotations

from pathlib import Path

from backend.crew.test_parsers import (
    _LcovCounters,
    extract_error_summary,
    merge_test_results,
    parse_bazel_testlogs,
    parse_junit_xml,
    parse_lcov_file,
    read_log_tail,
)
from backend.crew.result_recorder import SingleTestResult


def _result(
    test_id: str,
    file_path: str = "",
    function_name: str = "",
    status: str = "passed",
) -> SingleTestResult:
    return SingleTestResult(
        test_id=test_id,
        file_path=file_path,
        function_name=function_name,
        status=status,
    )


# ── parse_junit_xml ──────────────────────────────────────────────────────────


def test_parse_junit_xml_missing_file_returns_empty(tmp_path: Path) -> None:
    assert parse_junit_xml(tmp_path / "nope.xml") == []


def test_parse_junit_xml_malformed_xml_returns_empty(tmp_path: Path) -> None:
    """Unparseable XML is reported as no results, not an exception."""
    bad = tmp_path / "test.xml"
    bad.write_text("<testsuite><unclosed", encoding="utf-8")
    assert parse_junit_xml(bad) == []


def test_parse_junit_xml_classname_with_class_part_stops_at_class(tmp_path: Path) -> None:
    """classname 'tests.test_foo.TestBar' maps to tests/test_foo.py."""
    xml = (
        '<testsuite><testcase classname="tests.test_foo.TestBar" '
        'name="test_baz" time="0.5"/></testsuite>'
    )
    p = tmp_path / "test.xml"
    p.write_text(xml, encoding="utf-8")
    results = parse_junit_xml(p)
    assert results[0].file_path == "tests/test_foo.py"
    assert results[0].function_name == "test_baz"
    assert results[0].duration_ms == 500


def test_parse_junit_xml_bazel_target_name_without_classname(tmp_path: Path) -> None:
    """Per-target stubs (name='tests/test_foo', no classname) get a file id."""
    xml = '<testsuite><testcase name="tests/test_foo"/></testsuite>'
    p = tmp_path / "test.xml"
    p.write_text(xml, encoding="utf-8")
    results = parse_junit_xml(p)
    assert results[0].file_path == "tests/test_foo.py"
    assert results[0].function_name == ""
    assert results[0].test_id == "tests/test_foo.py"


def test_parse_junit_xml_bare_name_yields_function_only(tmp_path: Path) -> None:
    """A testcase with only a bare function name has no file path."""
    xml = '<testsuite><testcase name="test_alone"/></testsuite>'
    p = tmp_path / "test.xml"
    p.write_text(xml, encoding="utf-8")
    results = parse_junit_xml(p)
    assert results[0].file_path == ""
    assert results[0].function_name == "test_alone"


def test_parse_junit_xml_error_element_gives_error_status(tmp_path: Path) -> None:
    xml = (
        '<testsuite><testcase classname="tests.test_foo" name="test_boom">'
        '<error message="ImportError: nope"/></testcase></testsuite>'
    )
    p = tmp_path / "test.xml"
    p.write_text(xml, encoding="utf-8")
    results = parse_junit_xml(p)
    assert results[0].status == "error"
    assert "ImportError" in results[0].error_message


def test_parse_junit_xml_failure_and_skipped_statuses(tmp_path: Path) -> None:
    xml = (
        "<testsuite>"
        '<testcase classname="tests.test_foo" name="test_bad">'
        '<failure message="assert 1 == 2"/></testcase>'
        '<testcase classname="tests.test_foo" name="test_skip"><skipped/></testcase>'
        "</testsuite>"
    )
    p = tmp_path / "test.xml"
    p.write_text(xml, encoding="utf-8")
    statuses = {r.function_name: r.status for r in parse_junit_xml(p)}
    assert statuses == {"test_bad": "failed", "test_skip": "skipped"}


# ── read_log_tail ────────────────────────────────────────────────────────────


def test_read_log_tail_missing_file_returns_empty(tmp_path: Path) -> None:
    assert read_log_tail(tmp_path / "test.log") == ""


def test_read_log_tail_unreadable_path_returns_empty(tmp_path: Path) -> None:
    """A log path that raises OSError on read yields an empty tail."""
    log_dir = tmp_path / "test.log"
    log_dir.mkdir()  # reading a directory raises IsADirectoryError (OSError)
    assert read_log_tail(log_dir) == ""


def test_read_log_tail_keeps_last_50_nonempty_lines(tmp_path: Path) -> None:
    log = tmp_path / "test.log"
    log.write_text("\n".join(f"line{i}" for i in range(60)) + "\n\n", encoding="utf-8")
    tail = read_log_tail(log)
    lines = tail.splitlines()
    assert len(lines) == 50
    assert lines[-1] == "line59"


# ── parse_bazel_testlogs ─────────────────────────────────────────────────────


def test_parse_bazel_testlogs_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert parse_bazel_testlogs(tmp_path) == []


def test_parse_bazel_testlogs_filters_deleted_test_files(tmp_path: Path) -> None:
    """Results for test files no longer on disk are dropped."""
    logs = tmp_path / "bazel-testlogs" / "tests" / "test_alpha"
    logs.mkdir(parents=True)
    (logs / "test.xml").write_text(
        "<testsuite>"
        '<testcase classname="tests.test_alpha" name="test_a"/>'
        '<testcase classname="tests.test_gone" name="test_b"/>'
        "</testsuite>",
        encoding="utf-8",
    )
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_alpha.py").write_text("", encoding="utf-8")

    results = parse_bazel_testlogs(tmp_path)
    assert [r.function_name for r in results] == ["test_a"]


# ── merge_test_results ───────────────────────────────────────────────────────


def test_merge_prefers_coverage_failure_over_bazel_pass() -> None:
    bazel = [_result("t::a", "tests/t.py", "a", "passed")]
    cov = [_result("t::a", "tests/t.py", "a", "failed")]
    merged = merge_test_results(bazel, cov)
    assert merged[0].status == "failed"


def test_merge_prefers_coverage_pass_over_bazel_failure() -> None:
    bazel = [_result("t::a", "tests/t.py", "a", "error")]
    cov = [_result("t::a", "tests/t.py", "a", "passed")]
    merged = merge_test_results(bazel, cov)
    assert merged[0].status == "passed"


def test_merge_drops_bazel_file_level_error_stub_when_coverage_has_results() -> None:
    """A bazel sandbox import-error stub is superseded by real coverage runs."""
    bazel = [_result("tests/t.py", "tests/t.py", "", "error")]
    cov = [_result("tests/t.py::a", "tests/t.py", "a", "passed")]
    merged = merge_test_results(bazel, cov)
    assert [r.test_id for r in merged] == ["tests/t.py::a"]


def test_merge_adds_coverage_only_results() -> None:
    merged = merge_test_results([], [_result("t::a", "tests/t.py", "a")])
    assert len(merged) == 1


# ── parse_lcov_file / _LcovCounters ──────────────────────────────────────────


def test_parse_lcov_missing_file_returns_empty_result(tmp_path: Path) -> None:
    result = parse_lcov_file(tmp_path / "cov.dat")
    assert result.line_pct is None
    assert result.by_file == {}


def test_parse_lcov_unreadable_path_returns_empty_result(tmp_path: Path) -> None:
    d = tmp_path / "cov.dat"
    d.mkdir()  # reading a directory raises OSError
    assert parse_lcov_file(d).line_pct is None


def test_parse_lcov_full_record_computes_percentages(tmp_path: Path) -> None:
    lcov = tmp_path / "cov.dat"
    lcov.write_text(
        "SF:src/a.py\n"
        "DA:1,1\n"
        "DA:2,0\n"
        "LF:2\n"
        "LH:1\n"
        "BRF:4\n"
        "BRH:3\n"
        "end_of_record\n"
        "TN:ignored\n",
        encoding="utf-8",
    )
    result = parse_lcov_file(lcov)
    assert result.line_pct == 50.0
    assert result.branch_pct == 75.0
    assert result.by_file == {"src/a.py": 50.0}
    assert result.uncovered_lines == {"src/a.py": [2]}
    assert result.missing == "src/a.py"


def test_lcov_record_without_lf_still_records_uncovered_lines() -> None:
    """A record with DA lines but no LF/LH keeps uncovered lines only."""
    counters = _LcovCounters()
    for line in ("SF:src/b.py", "DA:3,0", "end_of_record"):
        counters.process_line(line)
    assert counters.coverage_by_file == {}
    assert counters.uncovered_lines == {"src/b.py": [3]}


def test_lcov_build_result_per_file_data_without_totals_reports_100() -> None:
    """Per-file data with zero aggregate LF yields a 100% line figure."""
    counters = _LcovCounters(coverage_by_file={"src/a.py": 100.0})
    assert counters.build_result().line_pct == 100.0


# ── extract_error_summary ────────────────────────────────────────────────────


def test_extract_error_summary_collect_error_block() -> None:
    output = (
        "_____ ERROR collecting tests/test_x.py _____\n"
        "ImportError: no module named foo\n"
        "_____ other _____\n"
    )
    summary = extract_error_summary(output)
    assert "ImportError" in summary


def test_extract_error_summary_falls_back_to_tail() -> None:
    assert extract_error_summary("line1\nline2\n") == "line1\nline2"


def test_extract_error_summary_empty_output() -> None:
    assert extract_error_summary("") == "tests exited with errors (no output)"
