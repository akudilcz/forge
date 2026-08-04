"""Tests for backend.crew.gap_finder."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import pytest

from backend.crew.gap_finder import (
    Gap,
    GapKind,
    _build_error_summaries,
    _check_uncovered_requirement,
    _fallback_import_check,
    _partition_dep_errors,
    _report_dep_error_clusters,
    _report_test_failures,
    _target_path,
    find_gaps,
)
from backend.crew.naming import slugify as _slugify
from backend.crew.result_recorder import SingleTestResult
from backend.crew.trace_parser import LineTrace, UntracedFunction
from backend.crew.workspace_scanner import FileState

if TYPE_CHECKING:
    from backend.graph.engine import ProjectGraph

# ── Helpers ──────────────────────────────────────────────────────────────────


@dataclass
class FakeNode:
    node_id: str
    node_type: str
    title: str = ""
    content: str = ""
    trace_to: list[str] = field(default_factory=list)
    parent_id: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)


class FakeGraph:
    def __init__(self, nodes: list[FakeNode]) -> None:
        self._nodes = nodes

    def all_nodes(self) -> list[FakeNode]:
        return self._nodes


def _fake_graph(nodes: list[FakeNode]) -> ProjectGraph:
    """A duck-typed graph stand-in typed as the real ProjectGraph.

    ``find_gaps`` and its helpers only ever call ``all_nodes()`` on the
    graph (as their docstrings state), so the lightweight fake is enough.
    """
    return cast("ProjectGraph", FakeGraph(nodes))


def _traced_file(path: str) -> FileState:
    """A file where all functions are traced."""
    return FileState(path=path, total_functions=1, traced_functions=1)


def _untraced_file(path: str, names: list[str]) -> FileState:
    """A file with untraced functions."""
    return FileState(
        path=path,
        untraced_functions=[
            UntracedFunction(name=n, start=1, end=5, is_private=n.startswith("_"))
            for n in names
        ],
        total_functions=len(names) + 1,
        traced_functions=1,
    )


# ── _slugify tests ───────────────────────────────────────────────────────────


def test_slugify_strips_design_suffix() -> None:
    assert _slugify("Auth Manager Design") == "auth_manager"


def test_slugify_handles_empty() -> None:
    assert _slugify("") == "unnamed"


# ── _target_path tests ──────────────────────────────────────────────────────


def test_target_path_source() -> None:
    node = FakeNode(node_id="D-1", node_type="DESIGN", title="Foo Bar")
    assert _target_path(node, "source") == "src/foo_bar.py"


def test_target_path_test() -> None:
    node = FakeNode(node_id="C-1", node_type="CASE_LLR", title="Foo Bar")
    assert _target_path(node, "test") == "tests/test_foo_bar.py"


# ── GapKind priority ordering ───────────────────────────────────────────────


def test_test_env_broken_is_highest_priority() -> None:
    """TEST_ENV_BROKEN should be priority 0."""
    assert GapKind.TEST_ENV_BROKEN.value == 0


def test_gap_kind_ordering() -> None:
    """Verify the priority ordering of all gap types."""
    assert GapKind.TEST_ENV_BROKEN < GapKind.SYNTAX_ERROR
    assert GapKind.SYNTAX_ERROR < GapKind.MISSING_SOURCE
    assert GapKind.MISSING_SOURCE < GapKind.MISSING_TEST
    assert GapKind.MISSING_TEST < GapKind.FAILING_TESTS
    assert GapKind.FAILING_TESTS < GapKind.INVALID_TRACES
    assert GapKind.INVALID_TRACES < GapKind.UNTRACED_FUNCTIONS
    assert GapKind.UNTRACED_FUNCTIONS < GapKind.LOW_STRUCTURAL_COVERAGE
    assert GapKind.LOW_STRUCTURAL_COVERAGE < GapKind.UNCOVERED_REQUIREMENT


# ── find_gaps tests ─────────────────────────────────────────────────────────


def test_missing_source_gap() -> None:
    graph = _fake_graph([FakeNode("D-1", "DESIGN", title="Widget")])
    gaps = find_gaps({}, {}, [], graph)
    assert len(gaps) == 1
    assert gaps[0].kind == GapKind.MISSING_SOURCE
    assert gaps[0].node_id == "D-1"


def test_no_gap_when_source_exists() -> None:
    graph = _fake_graph([FakeNode("D-1", "DESIGN", title="Widget")])
    gaps = find_gaps(
        {"src/widget.py": _traced_file("src/widget.py")}, {}, [], graph,
    )
    assert not any(g.kind == GapKind.MISSING_SOURCE for g in gaps)


def test_missing_test_gap() -> None:
    graph = _fake_graph([FakeNode("C-1", "CASE_HLR", title="Auth")])
    gaps = find_gaps({}, {}, [], graph)
    assert len(gaps) == 1
    assert gaps[0].kind == GapKind.MISSING_TEST


def test_untraced_functions_gap() -> None:
    graph = _fake_graph([])
    gaps = find_gaps(
        {"src/foo.py": _untraced_file("src/foo.py", ["bar"])},
        {}, [], graph,
    )
    untraced = [g for g in gaps if g.kind == GapKind.UNTRACED_FUNCTIONS]
    assert len(untraced) == 1


def test_private_functions_reported_as_untraced() -> None:
    """Private functions (_ prefix) MUST be reported — no exemptions."""
    graph = _fake_graph([])
    gaps = find_gaps(
        {"src/foo.py": _untraced_file("src/foo.py", ["_helper", "_internal"])},
        {}, [], graph,
    )
    untraced = [g for g in gaps if g.kind == GapKind.UNTRACED_FUNCTIONS]
    assert len(untraced) == 1
    assert "_helper" in untraced[0].context["untraced_functions"]
    assert "_internal" in untraced[0].context["untraced_functions"]


def test_mixed_public_private_reports_all() -> None:
    """Both public and private untraced functions should appear in gaps."""
    graph = _fake_graph([])
    gaps = find_gaps(
        {"src/foo.py": _untraced_file("src/foo.py", ["public_fn", "_private_fn"])},
        {}, [], graph,
    )
    untraced = [g for g in gaps if g.kind == GapKind.UNTRACED_FUNCTIONS]
    assert len(untraced) == 1
    assert "public_fn" in untraced[0].context["untraced_functions"]
    assert "_private_fn" in untraced[0].context["untraced_functions"]


def test_no_untraced_gap_when_all_traced() -> None:
    graph = _fake_graph([])
    gaps = find_gaps(
        {"src/foo.py": _traced_file("src/foo.py")}, {}, [], graph,
    )
    assert not any(g.kind == GapKind.UNTRACED_FUNCTIONS for g in gaps)


def test_failing_tests_gap_grouped_by_file() -> None:
    graph = _fake_graph([])
    results = [
        SingleTestResult(
            test_id="tests/test_x.py::test_a", file_path="tests/test_x.py",
            function_name="test_a", status="failed",
        ),
        SingleTestResult(
            test_id="tests/test_x.py::test_b", file_path="tests/test_x.py",
            function_name="test_b", status="failed",
        ),
    ]
    gaps = find_gaps({}, {}, results, graph)
    failing = [g for g in gaps if g.kind == GapKind.FAILING_TESTS]
    # Grouped: one gap per file, not one per test
    assert len(failing) == 1
    assert failing[0].file_path == "tests/test_x.py"
    assert failing[0].context["failing_count"] == 2
    assert len(failing[0].context["test_ids"]) == 2


def test_import_error_routes_to_test_env_broken() -> None:
    """Tests with ImportError should produce TEST_ENV_BROKEN, not FAILING_TESTS."""
    graph = _fake_graph([])
    results = [
        SingleTestResult(
            test_id="tests/test_y.py::test_c", file_path="tests/test_y.py",
            function_name="test_c", status="error",
            error_message="ImportError: No module named 'src.planner'",
            error_detail="Traceback:\n  File test_y.py, line 3\nImportError: No module named 'src.planner'",
        ),
    ]
    gaps = find_gaps({}, {}, results, graph)
    env = [g for g in gaps if g.kind == GapKind.TEST_ENV_BROKEN]
    assert len(env) == 1
    assert "requirements.txt" in env[0].details
    assert env[0].context["missing_module"] == "src"

    # Should NOT appear as FAILING_TESTS
    failing = [g for g in gaps if g.kind == GapKind.FAILING_TESTS]
    assert len(failing) == 0


def test_non_import_error_status_creates_failing_gap() -> None:
    """Tests with non-import errors should still produce FAILING_TESTS."""
    graph = _fake_graph([])
    results = [
        SingleTestResult(
            test_id="tests/test_y.py::test_c", file_path="tests/test_y.py",
            function_name="test_c", status="error",
            error_message="RuntimeError: timeout exceeded",
        ),
    ]
    gaps = find_gaps({}, {}, results, graph)
    failing = [g for g in gaps if g.kind == GapKind.FAILING_TESTS]
    assert len(failing) == 1
    assert failing[0].context["failing_count"] == 1


def test_failing_tests_gap_includes_error_summaries() -> None:
    """Error summaries should be included in gap context for agent prompts."""
    graph = _fake_graph([])
    results = [
        SingleTestResult(
            test_id="tests/test_x.py::test_a", file_path="tests/test_x.py",
            function_name="test_a", status="failed",
            error_message="AssertionError: expected 42",
            error_detail="assert 1 == 42",
        ),
        SingleTestResult(
            test_id="tests/test_x.py::test_b", file_path="tests/test_x.py",
            function_name="test_b", status="error",
            error_message="RuntimeError: something broke",
        ),
    ]
    gaps = find_gaps({}, {}, results, graph)
    failing = [g for g in gaps if g.kind == GapKind.FAILING_TESTS]
    assert len(failing) == 1
    summaries = failing[0].context["error_summaries"]
    assert len(summaries) == 2
    assert "AssertionError" in summaries[0]
    assert "RuntimeError" in summaries[1]


def test_passing_tests_no_gap() -> None:
    graph = _fake_graph([])
    results = [SingleTestResult(
        test_id="tests/test_x.py::test_x", file_path="tests/test_x.py",
        function_name="test_x", status="passed",
    )]
    gaps = find_gaps({}, {}, results, graph)
    assert not any(g.kind == GapKind.FAILING_TESTS for g in gaps)


def test_test_env_broken_gap() -> None:
    graph = _fake_graph([])
    gaps = find_gaps({}, {}, [], graph, test_run_error="pytest not found")
    env = [g for g in gaps if g.kind == GapKind.TEST_ENV_BROKEN]
    assert len(env) == 1
    assert "pytest not found" in env[0].details


def test_no_env_gap_when_tests_run() -> None:
    graph = _fake_graph([])
    gaps = find_gaps({}, {}, [], graph, test_run_error="")
    assert not any(g.kind == GapKind.TEST_ENV_BROKEN for g in gaps)


# ── SYNTAX_ERROR ──────────────────────────────────────────────────────────


def test_syntax_error_gap() -> None:
    """File with a syntax error should produce a SYNTAX_ERROR gap."""
    graph = _fake_graph([])
    bad_file = FileState(
        path="tests/test_bad.py",
        syntax_error="invalid syntax (line 10): f\"{foo)s}\"",
    )
    gaps = find_gaps({}, {"tests/test_bad.py": bad_file}, [], graph)
    syntax = [g for g in gaps if g.kind == GapKind.SYNTAX_ERROR]
    assert len(syntax) == 1
    assert "invalid syntax" in syntax[0].details
    assert syntax[0].file_path == "tests/test_bad.py"


def test_no_syntax_error_gap_when_valid() -> None:
    """Files without syntax errors should not produce SYNTAX_ERROR gaps."""
    graph = _fake_graph([])
    ok_file = _traced_file("src/ok.py")
    gaps = find_gaps({"src/ok.py": ok_file}, {}, [], graph)
    assert not any(g.kind == GapKind.SYNTAX_ERROR for g in gaps)


def test_invalid_traces_gap() -> None:
    """Should detect annotations referencing non-existent LLR IDs."""
    graph = _fake_graph([FakeNode("LLR-001", "LLR", title="Real LLR")])
    bad_trace = LineTrace(
        start=1, end=5, llr_ids=["LLR-999"], symbol="foo",
    )
    src = FileState(
        path="src/foo.py", traces=[bad_trace],
        total_functions=1, traced_functions=1,
    )
    gaps = find_gaps({"src/foo.py": src}, {}, [], graph)
    invalid = [g for g in gaps if g.kind == GapKind.INVALID_TRACES]
    assert len(invalid) == 1
    assert "LLR-999" in invalid[0].context["invalid_llr_ids"]


def test_no_invalid_traces_gap_when_ids_valid() -> None:
    """Should not flag valid LLR IDs."""
    graph = _fake_graph([FakeNode("LLR-001", "LLR", title="Real LLR")])
    good_trace = LineTrace(
        start=1, end=5, llr_ids=["LLR-001"], symbol="foo",
    )
    src = FileState(
        path="src/foo.py", traces=[good_trace],
        total_functions=1, traced_functions=1,
    )
    gaps = find_gaps({"src/foo.py": src}, {}, [], graph)
    assert not any(g.kind == GapKind.INVALID_TRACES for g in gaps)


def test_gaps_sorted_by_priority() -> None:
    graph = _fake_graph([FakeNode("D-1", "DESIGN", title="Widget")])
    results = [SingleTestResult(
        test_id="tests/test_x.py::test_x", file_path="tests/test_x.py",
        function_name="test_x", status="failed",
    )]
    gaps = find_gaps(
        {"src/other.py": _untraced_file("src/other.py", ["baz"])},
        {}, results, graph,
        test_run_error="broken",
    )
    kinds = [g.kind for g in gaps]
    assert kinds == sorted(kinds, key=lambda k: k.value)


# ── LOW_STRUCTURAL_COVERAGE ──────────────────────────────────────────────────


def test_low_structural_coverage_gap() -> None:
    """Source file with < 100% coverage creates a gap."""
    graph = _fake_graph([])
    src = _traced_file("src/foo.py")
    gaps = find_gaps(
        {"src/foo.py": src}, {}, [], graph,
        coverage_by_file={"src/foo.py": 75.0},
    )
    cov = [g for g in gaps if g.kind == GapKind.LOW_STRUCTURAL_COVERAGE]
    assert len(cov) == 1
    assert cov[0].context["coverage_pct"] == 75.0


def test_no_coverage_gap_at_100() -> None:
    """Source file at 100% coverage creates no gap."""
    graph = _fake_graph([])
    src = _traced_file("src/foo.py")
    gaps = find_gaps(
        {"src/foo.py": src}, {}, [], graph,
        coverage_by_file={"src/foo.py": 100.0},
    )
    assert not any(g.kind == GapKind.LOW_STRUCTURAL_COVERAGE for g in gaps)


def test_no_coverage_gap_when_no_data() -> None:
    """No coverage data → no coverage gaps."""
    graph = _fake_graph([])
    src = _traced_file("src/foo.py")
    gaps = find_gaps({"src/foo.py": src}, {}, [], graph)
    assert not any(g.kind == GapKind.LOW_STRUCTURAL_COVERAGE for g in gaps)


# ── UNCOVERED_REQUIREMENT ────────────────────────────────────────────────────


def test_uncovered_requirement_gap() -> None:
    """LLR with no passing test evidence creates a gap."""
    graph = _fake_graph([FakeNode("LLR-001", "LLR", title="Must auth")])
    gaps = find_gaps({}, {}, [], graph)
    uncov = [g for g in gaps if g.kind == GapKind.UNCOVERED_REQUIREMENT]
    assert len(uncov) == 1
    assert uncov[0].node_id == "LLR-001"


def test_no_uncovered_requirement_when_test_passes() -> None:
    """LLR referenced in a passing test is covered."""
    graph = _fake_graph([FakeNode("LLR-001", "LLR", title="Must auth")])
    test_trace = LineTrace(start=1, end=5, llr_ids=["LLR-001"], symbol="test_auth")
    test_files = {
        "tests/test_auth.py": FileState(
            path="tests/test_auth.py", traces=[test_trace],
            total_functions=1, traced_functions=1,
        ),
    }
    results = [SingleTestResult(
        test_id="tests/test_auth.py::test_auth",
        file_path="tests/test_auth.py",
        function_name="test_auth",
        status="passed",
    )]
    gaps = find_gaps({}, test_files, results, graph)
    assert not any(g.kind == GapKind.UNCOVERED_REQUIREMENT for g in gaps)


def test_uncovered_requirement_failing_test_not_counted() -> None:
    """LLR referenced in a FAILING test is NOT covered.

    Uses _check_uncovered_requirement directly because find_gaps()
    now applies the green gate (suppressing UNCOVERED_REQUIREMENT
    when FAILING_TESTS exists).
    """
    graph = _fake_graph([FakeNode("LLR-001", "LLR", title="Must auth")])
    test_trace = LineTrace(start=1, end=5, llr_ids=["LLR-001"], symbol="test_auth")
    test_files = {
        "tests/test_auth.py": FileState(
            path="tests/test_auth.py", traces=[test_trace],
            total_functions=1, traced_functions=1,
        ),
    }
    results = [SingleTestResult(
        test_id="tests/test_auth.py::test_auth",
        file_path="tests/test_auth.py",
        function_name="test_auth",
        status="failed",
    )]
    gaps: list[Gap] = []
    _check_uncovered_requirement(gaps, test_files, results, graph)
    uncov = [g for g in gaps if g.kind == GapKind.UNCOVERED_REQUIREMENT]
    assert len(uncov) == 1


# ── No suppression — agent sees everything ──────────────────────────────────


def test_all_gap_types_visible_together() -> None:
    """All gap types are reported together — nothing is suppressed."""
    graph = _fake_graph([FakeNode("LLR-001", "LLR", title="Auth")])
    bad_file = FileState(
        path="src/bad.py", syntax_error="invalid syntax (line 5)",
    )
    gaps = find_gaps(
        {"src/bad.py": bad_file}, {}, [], graph,
        coverage_by_file={"src/bad.py": 50.0},
    )
    assert any(g.kind == GapKind.SYNTAX_ERROR for g in gaps)
    assert any(g.kind == GapKind.LOW_STRUCTURAL_COVERAGE for g in gaps)
    assert any(g.kind == GapKind.UNCOVERED_REQUIREMENT for g in gaps)


def test_env_broken_and_coverage_gaps_both_visible() -> None:
    """TEST_ENV_BROKEN and LOW_STRUCTURAL_COVERAGE coexist."""
    graph = _fake_graph([FakeNode("LLR-001", "LLR", title="Auth")])
    src = _traced_file("src/foo.py")
    gaps = find_gaps(
        {"src/foo.py": src}, {}, [], graph,
        test_run_error="pytest not found",
        coverage_by_file={"src/foo.py": 80.0},
    )
    assert any(g.kind == GapKind.TEST_ENV_BROKEN for g in gaps)
    assert any(g.kind == GapKind.LOW_STRUCTURAL_COVERAGE for g in gaps)


def test_failing_tests_and_coverage_both_visible() -> None:
    """FAILING_TESTS and LOW_STRUCTURAL_COVERAGE coexist."""
    graph = _fake_graph([])
    src = _traced_file("src/foo.py")
    results = [SingleTestResult(
        test_id="tests/test_x.py::test_x", file_path="tests/test_x.py",
        function_name="test_x", status="failed",
    )]
    gaps = find_gaps(
        {"src/foo.py": src}, {}, results, graph,
        coverage_by_file={"src/foo.py": 75.0},
    )
    assert any(g.kind == GapKind.FAILING_TESTS for g in gaps)
    assert any(g.kind == GapKind.LOW_STRUCTURAL_COVERAGE for g in gaps)


# ── LOW_BRANCH_COVERAGE ──────────────────────────────────────────────────────


def test_branch_coverage_below_100_creates_gap() -> None:
    """MC/DC < 100% should produce a LOW_BRANCH_COVERAGE gap."""
    graph = _fake_graph([])
    gaps = find_gaps({}, {}, [], graph, branch_coverage_pct=97.3)
    branch_gaps = [g for g in gaps if g.kind == GapKind.LOW_BRANCH_COVERAGE]
    assert len(branch_gaps) == 1
    assert "97.3%" in branch_gaps[0].details
    assert "DO-178C" in branch_gaps[0].details


def test_branch_coverage_100_no_gap() -> None:
    """MC/DC at exactly 100% should not produce a gap."""
    graph = _fake_graph([])
    gaps = find_gaps({}, {}, [], graph, branch_coverage_pct=100.0)
    assert not any(g.kind == GapKind.LOW_BRANCH_COVERAGE for g in gaps)


def test_branch_coverage_none_no_gap() -> None:
    """No branch data (None) should not produce a gap."""
    graph = _fake_graph([])
    gaps = find_gaps({}, {}, [], graph, branch_coverage_pct=None)
    assert not any(g.kind == GapKind.LOW_BRANCH_COVERAGE for g in gaps)


def test_branch_coverage_gap_suggests_refactoring() -> None:
    """The gap details should mention refactoring as a valid approach."""
    graph = _fake_graph([])
    gaps = find_gaps({}, {}, [], graph, branch_coverage_pct=85.0)
    branch_gaps = [g for g in gaps if g.kind == GapKind.LOW_BRANCH_COVERAGE]
    assert "refactor" in branch_gaps[0].details.lower()


# ── _fallback_import_check ────────────────────────────────────────────────────


def test_fallback_import_check_module_not_found() -> None:
    """ModuleNotFoundError with quoted module name extracts root module."""
    result = _fallback_import_check("ModuleNotFoundError: No module named 'numpy'")
    assert result == "numpy"


def test_fallback_import_check_dotted_module() -> None:
    """Dotted module path returns only the root package."""
    result = _fallback_import_check("ImportError: No module named 'scipy.linalg'")
    assert result == "scipy"


def test_fallback_import_check_non_import_error() -> None:
    """Non-import errors return None."""
    result = _fallback_import_check("AssertionError: expected 42")
    assert result is None


def test_fallback_import_check_empty_string() -> None:
    """Empty string returns None."""
    result = _fallback_import_check("")
    assert result is None


# ── _partition_dep_errors ─────────────────────────────────────────────────────


def test_partition_dep_errors_import_error_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """ImportError in message routes to dep_errors list."""
    monkeypatch.delenv("FORGE_WORKSPACE", raising=False)
    result = SingleTestResult(
        test_id="tests/test_a.py::test_x",
        file_path="tests/test_a.py",
        function_name="test_x",
        status="error",
        error_message="ModuleNotFoundError: No module named 'numpy'",
    )
    dep_errors, other = _partition_dep_errors([result])
    assert len(dep_errors) == 1
    assert dep_errors[0][1] == "numpy"
    assert len(other) == 0


def test_partition_dep_errors_non_import_goes_to_other(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-import failures go to other_failures list."""
    monkeypatch.delenv("FORGE_WORKSPACE", raising=False)
    result = SingleTestResult(
        test_id="tests/test_a.py::test_x",
        file_path="tests/test_a.py",
        function_name="test_x",
        status="failed",
        error_message="AssertionError: expected 42",
    )
    dep_errors, other = _partition_dep_errors([result])
    assert len(dep_errors) == 0
    assert len(other) == 1


def test_partition_dep_errors_empty() -> None:
    """Empty test results produce empty lists."""
    dep_errors, other = _partition_dep_errors([])
    assert dep_errors == []
    assert other == []


def test_partition_dep_errors_no_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """Error with no message falls through to other_failures."""
    monkeypatch.delenv("FORGE_WORKSPACE", raising=False)
    result = SingleTestResult(
        test_id="tests/test_a.py::test_x",
        file_path="tests/test_a.py",
        function_name="test_x",
        status="failed",
        error_message="",
        error_detail="",
    )
    dep_errors, other = _partition_dep_errors([result])
    assert len(dep_errors) == 0
    assert len(other) == 1


def test_partition_dep_errors_fallback_when_no_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without FORGE_WORKSPACE, fallback import check is used."""
    monkeypatch.delenv("FORGE_WORKSPACE", raising=False)
    result = SingleTestResult(
        test_id="tests/test_a.py::test_x",
        file_path="tests/test_a.py",
        function_name="test_x",
        status="error",
        error_message="ImportError: No module named 'requests'",
    )
    dep_errors, other = _partition_dep_errors([result])
    assert len(dep_errors) == 1
    assert dep_errors[0][1] == "requests"


def test_partition_dep_errors_skips_passing_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    """Passing tests are completely ignored by partition."""
    monkeypatch.delenv("FORGE_WORKSPACE", raising=False)
    result = SingleTestResult(
        test_id="tests/test_a.py::test_x",
        file_path="tests/test_a.py",
        function_name="test_x",
        status="passed",
    )
    dep_errors, other = _partition_dep_errors([result])
    assert dep_errors == []
    assert other == []


# ── _report_dep_error_clusters ────────────────────────────────────────────────


def test_report_dep_error_clusters_single_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """3 errors all missing 'numpy' collapse into 1 TEST_ENV_BROKEN gap."""
    monkeypatch.delenv("FORGE_WORKSPACE", raising=False)
    r1 = SingleTestResult(
        test_id="tests/test_a.py::test_1", file_path="tests/test_a.py",
        function_name="test_1", status="error",
    )
    r2 = SingleTestResult(
        test_id="tests/test_a.py::test_2", file_path="tests/test_a.py",
        function_name="test_2", status="error",
    )
    r3 = SingleTestResult(
        test_id="tests/test_b.py::test_3", file_path="tests/test_b.py",
        function_name="test_3", status="error",
    )
    dep_errors = [(r1, "numpy"), (r2, "numpy"), (r3, "numpy")]
    gaps: list[Gap] = []
    _report_dep_error_clusters(gaps, dep_errors)
    assert len(gaps) == 1
    assert gaps[0].kind == GapKind.TEST_ENV_BROKEN
    assert gaps[0].context["missing_module"] == "numpy"
    assert gaps[0].context["affected_count"] == 3


def test_report_dep_error_clusters_two_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    """2 different missing modules produce 2 separate gaps."""
    monkeypatch.delenv("FORGE_WORKSPACE", raising=False)
    r1 = SingleTestResult(
        test_id="tests/test_a.py::test_1", file_path="tests/test_a.py",
        function_name="test_1", status="error",
    )
    r2 = SingleTestResult(
        test_id="tests/test_b.py::test_2", file_path="tests/test_b.py",
        function_name="test_2", status="error",
    )
    dep_errors = [(r1, "numpy"), (r2, "scipy")]
    gaps: list[Gap] = []
    _report_dep_error_clusters(gaps, dep_errors)
    assert len(gaps) == 2
    modules = {g.context["missing_module"] for g in gaps}
    assert modules == {"numpy", "scipy"}


def test_report_dep_error_clusters_fix_hint_includes_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix hint should mention the missing module and manifest file."""
    monkeypatch.delenv("FORGE_WORKSPACE", raising=False)
    r1 = SingleTestResult(
        test_id="tests/test_a.py::test_1", file_path="tests/test_a.py",
        function_name="test_1", status="error",
    )
    dep_errors = [(r1, "numpy")]
    gaps: list[Gap] = []
    _report_dep_error_clusters(gaps, dep_errors)
    assert "numpy" in gaps[0].details
    assert "requirements.txt" in gaps[0].details


def test_report_dep_error_clusters_deduplicates_files(monkeypatch: pytest.MonkeyPatch) -> None:
    """Affected files should be deduplicated."""
    monkeypatch.delenv("FORGE_WORKSPACE", raising=False)
    r1 = SingleTestResult(
        test_id="tests/test_a.py::test_1", file_path="tests/test_a.py",
        function_name="test_1", status="error",
    )
    r2 = SingleTestResult(
        test_id="tests/test_a.py::test_2", file_path="tests/test_a.py",
        function_name="test_2", status="error",
    )
    dep_errors = [(r1, "numpy"), (r2, "numpy")]
    gaps: list[Gap] = []
    _report_dep_error_clusters(gaps, dep_errors)
    assert gaps[0].context["affected_files"] == ["tests/test_a.py"]
    assert gaps[0].context["affected_count"] == 2


# ── _report_test_failures ─────────────────────────────────────────────────────


def test_report_test_failures_same_file() -> None:
    """2 failures in same file produce 1 gap with count=2."""
    r1 = SingleTestResult(
        test_id="tests/test_x.py::test_a", file_path="tests/test_x.py",
        function_name="test_a", status="failed",
        error_message="AssertionError: 1 != 2",
    )
    r2 = SingleTestResult(
        test_id="tests/test_x.py::test_b", file_path="tests/test_x.py",
        function_name="test_b", status="failed",
        error_message="ValueError: bad input",
    )
    gaps: list[Gap] = []
    _report_test_failures(gaps, [r1, r2])
    assert len(gaps) == 1
    assert gaps[0].kind == GapKind.FAILING_TESTS
    assert gaps[0].file_path == "tests/test_x.py"
    assert gaps[0].context["failing_count"] == 2
    assert len(gaps[0].context["test_ids"]) == 2


def test_report_test_failures_different_files() -> None:
    """Failures in different files produce separate gaps."""
    r1 = SingleTestResult(
        test_id="tests/test_x.py::test_a", file_path="tests/test_x.py",
        function_name="test_a", status="failed",
        error_message="AssertionError",
    )
    r2 = SingleTestResult(
        test_id="tests/test_y.py::test_b", file_path="tests/test_y.py",
        function_name="test_b", status="failed",
        error_message="RuntimeError",
    )
    gaps: list[Gap] = []
    _report_test_failures(gaps, [r1, r2])
    assert len(gaps) == 2
    paths = {g.file_path for g in gaps}
    assert paths == {"tests/test_x.py", "tests/test_y.py"}


def test_report_test_failures_includes_error_summaries() -> None:
    """Error summaries should be present in gap context."""
    r1 = SingleTestResult(
        test_id="tests/test_x.py::test_a", file_path="tests/test_x.py",
        function_name="test_a", status="failed",
        error_message="AssertionError: mismatch",
    )
    gaps: list[Gap] = []
    _report_test_failures(gaps, [r1])
    assert len(gaps[0].context["error_summaries"]) == 1
    assert "AssertionError" in gaps[0].context["error_summaries"][0]


# ── _build_error_summaries ────────────────────────────────────────────────────


def test_build_error_summaries_with_message_and_detail() -> None:
    """Result with both error_message and error_detail includes both."""
    r = SingleTestResult(
        test_id="tests/test_x.py::test_a", file_path="tests/test_x.py",
        function_name="test_a", status="failed",
        error_message="AssertionError: expected 42",
        error_detail="assert 1 == 42\n  in test_a line 10",
    )
    summaries = _build_error_summaries([r])
    assert len(summaries) == 1
    assert "AssertionError" in summaries[0]
    assert "assert 1 == 42" in summaries[0]


def test_build_error_summaries_message_only() -> None:
    """Result with only error_message shows just the message."""
    r = SingleTestResult(
        test_id="tests/test_x.py::test_a", file_path="tests/test_x.py",
        function_name="test_a", status="failed",
        error_message="RuntimeError: something broke",
        error_detail="",
    )
    summaries = _build_error_summaries([r])
    assert len(summaries) == 1
    assert "RuntimeError: something broke" in summaries[0]
    # Should not contain traceback formatting
    assert "\n  " not in summaries[0]


def test_build_error_summaries_no_message_or_detail() -> None:
    """Result with neither message nor detail shows fallback."""
    r = SingleTestResult(
        test_id="tests/test_x.py::test_a", file_path="tests/test_x.py",
        function_name="test_a", status="failed",
        error_message="",
        error_detail="",
    )
    summaries = _build_error_summaries([r])
    assert len(summaries) == 1
    assert "(no error detail)" in summaries[0]
