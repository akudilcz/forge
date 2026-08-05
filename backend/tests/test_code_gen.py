"""Tests for Phase 12 Code Gen module — vertical-slice pipeline."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.crew.code_gen import (
    CodeGenIncompleteError,
    CodeGenResult,
    GeneratedFile,
    _enforce_coverage_gate,
    run_code_gen,
)
from backend.crew.codegen_helpers import (
    find_graph_orphans as _find_graph_orphans,
)
from backend.crew.codegen_helpers import (
    strip_markdown_fences as _strip_markdown_fences,
)
from backend.crew.naming import slugify as _slugify
from backend.crew.trace_parser import (
    LineTrace,
    parse_llr_traces,
)
from backend.crew.trace_parser import (
    find_untraced_functions as _find_untraced_functions,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_node(
    node_id: str,
    node_type: str,
    title: str = "Title",
    content: str = "Content",
    parent_id: str | None = None,
    trace_to: list[str] | None = None,
    properties: dict[str, Any] | None = None,
) -> MagicMock:
    node = MagicMock()
    node.node_id = node_id
    node.node_type = node_type
    node.title = title
    node.content = content
    node.parent_id = parent_id
    node.trace_to = trace_to or []
    node.properties = properties or {}
    return node


# ── parse_llr_traces ─────────────────────────────────────────────────────────

def test_parse_traces_single() -> None:
    """@traces decorator with one LLR ID is detected."""
    code = (
        "from backend.tracing import traces\n\n"
        '@traces("LLR-0001")\n'
        "def validate(token):\n"
        "    return bool(token)\n"
    )
    traces = parse_llr_traces(code)
    assert len(traces) == 1
    assert traces[0].llr_ids == ["LLR-0001"]
    assert traces[0].symbol == "validate"


def test_parse_traces_on_method() -> None:
    """@traces on a class method is detected."""
    code = (
        "class Foo:\n"
        '    @traces("LLR-0001")\n'
        "    def bar(self):\n"
        "        return 42\n"
    )
    traces = parse_llr_traces(code)
    assert len(traces) == 1
    assert traces[0].llr_ids == ["LLR-0001"]
    assert traces[0].symbol == "bar"
    assert traces[0].start == 3


def test_parse_traces_multiple_llrs() -> None:
    """@traces decorator with multiple LLR IDs."""
    code = (
        '@traces("LLR-0001", "LLR-0002")\n'
        "def authenticate(user, password):\n"
        "    pass\n"
    )
    traces = parse_llr_traces(code)
    assert len(traces) == 1
    assert traces[0].llr_ids == ["LLR-0001", "LLR-0002"]


def test_parse_traces_with_case() -> None:
    """@traces decorator with case= keyword extracts CASE IDs."""
    code = (
        '@traces("LLR-0003", case="CASE_LLR-0003")\n'
        "def test_plan():\n"
        "    assert True\n"
    )
    traces = parse_llr_traces(code)
    assert len(traces) == 1
    assert traces[0].llr_ids == ["LLR-0003"]
    assert traces[0].case_ids == ["CASE_LLR-0003"]


def test_parse_traces_with_case_list() -> None:
    """@traces decorator with case= as a list."""
    code = (
        '@traces("LLR-0001", case=["CASE_LLR-0001-01", "CASE_LLR-0001-02"])\n'
        "def test_multi():\n"
        "    pass\n"
    )
    traces = parse_llr_traces(code)
    assert len(traces) == 1
    assert traces[0].case_ids == ["CASE_LLR-0001-01", "CASE_LLR-0001-02"]


def test_parse_traces_no_decorator() -> None:
    """Code without @traces returns empty list."""
    code = "def foo():\n    return 1\n"
    assert parse_llr_traces(code) == []


def test_parse_traces_non_python_returns_empty() -> None:
    """Non-Python code (syntax error) returns empty list."""
    code = "function validate(token) {\n    return token.length > 0;\n}\n"
    assert parse_llr_traces(code) == []


def test_analyse_traces_counts() -> None:
    """analyse_traces correctly counts decorator-traced functions."""
    from backend.crew.trace_parser import analyse_traces
    code = (
        '@traces("LLR-0001")\n'
        "def traced():\n"
        "    pass\n\n"
        "def untraced():\n"
        "    pass\n"
    )
    result = analyse_traces(code)
    assert result.traced_functions == 1
    assert result.total_functions == 2
    assert len(result.untraced) == 1
    assert result.untraced[0].name == "untraced"


def test_analyse_traces_non_python_returns_empty() -> None:
    """analyse_traces on non-Python code returns zeroed result."""
    from backend.crew.trace_parser import analyse_traces
    result = analyse_traces("not valid python {{{")
    assert result.total_functions == 0
    assert result.traced_functions == 0


# ── @traces decorator runtime behaviour ─────────────────────────────────────

def test_traces_decorator_attaches_metadata() -> None:
    """The @traces decorator attaches _trace_llrs and _trace_cases."""
    from backend.tracing import traces

    @traces("LLR-0001", "LLR-0002", case="CASE_LLR-0001")
    def example() -> None:
        pass

    # The decorator attaches metadata dynamically, so the attributes are
    # invisible to the type checker.
    decorated = cast(Any, example)
    assert decorated._trace_llrs == ["LLR-0001", "LLR-0002"]
    assert decorated._trace_cases == ["CASE_LLR-0001"]


def test_traces_decorator_no_case() -> None:
    """The @traces decorator without case= sets empty list."""
    from backend.tracing import traces

    @traces("LLR-0005")
    def example() -> None:
        pass

    decorated = cast(Any, example)
    assert decorated._trace_llrs == ["LLR-0005"]
    assert decorated._trace_cases == []


# ── _slugify ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(("title", "expected"), [
    ("Auth Service Design", "auth_service"),
    ("Core Module", "core_module"),
    ("  Hello World  ", "hello_world"),
    ("", "unnamed"),
])
def test_slugify(title: str, expected: str) -> None:
    assert _slugify(title) == expected


# ── _strip_markdown_fences ───────────────────────────────────────────────────

def test_strip_markdown_fences() -> None:
    code = "```python\ndef foo():\n    pass\n```"
    result = _strip_markdown_fences(code)
    assert result.startswith("def foo()")
    assert "```" not in result


def test_strip_no_fences() -> None:
    code = "def foo():\n    pass\n"
    assert _strip_markdown_fences(code) == code


# ── _find_untraced_functions ─────────────────────────────────────────────────

def test_find_untraced_all_traced() -> None:
    """All public functions have @traces decorators."""
    code = (
        '@traces("LLR-0001")\n'
        "def foo():\n"
        "    return 1\n"
    )
    assert _find_untraced_functions(code) == []


def test_find_untraced_missing() -> None:
    """Public function without @traces decorator is flagged."""
    code = (
        "def foo():\n"
        "    return 1\n"
    )
    assert _find_untraced_functions(code) == ["foo"]


def test_find_untraced_includes_private() -> None:
    """Private helpers without @traces are also flagged."""
    code = (
        "def _helper():\n"
        "    return 1\n"
    )
    assert _find_untraced_functions(code) == ["_helper"]


# ── run_code_gen — vertical slice pipeline ───────────────────────────────────

@pytest.mark.asyncio
@patch("backend.crew.code_gen.find_gaps", return_value=[])
@patch("backend.crew.code_gen._close_remaining_gaps")
async def test_run_code_gen_empty_graph(mock_close_gaps: MagicMock, tmp_path: Path) -> None:
    """No DESIGN nodes → empty plan → no files generated."""
    from backend.crew.mission_agent import MissionStats
    from backend.crew.workspace_scanner import WorkspaceState
    mock_close_gaps.return_value = (WorkspaceState(), MissionStats())
    graph: Any = MagicMock()
    graph.all_nodes.return_value = []
    graph.update_node = AsyncMock()
    result = await run_code_gen(graph, tmp_path, config=MagicMock(), tool_instances=[])
    assert result.source_files == []
    assert result.test_files == []
    assert result.gaps_resolved is True


@pytest.mark.asyncio
async def test_run_code_gen_requires_config_and_tools(tmp_path: Path) -> None:
    """config and tool_instances are required — no silent defaults."""
    graph: Any = MagicMock()
    graph.all_nodes.return_value = []
    with pytest.raises(TypeError):
        await run_code_gen(graph, tmp_path)  # type: ignore[call-arg]


@pytest.mark.asyncio
@patch("backend.crew.code_gen.find_gaps", return_value=[])
@patch("backend.crew.code_gen._close_remaining_gaps")
async def test_run_code_gen_gaps_resolved_true(
    mock_close_gaps: MagicMock, tmp_path: Path
) -> None:
    """gaps_resolved is True when final scan finds zero gaps."""
    from backend.crew.mission_agent import MissionStats
    from backend.crew.workspace_scanner import WorkspaceState
    mock_close_gaps.return_value = (WorkspaceState(), MissionStats())
    graph: Any = MagicMock()
    graph.all_nodes.return_value = []
    graph.update_node = AsyncMock()

    result = await run_code_gen(graph, tmp_path, config=MagicMock(), tool_instances=[])
    assert result.gaps_resolved is True


# ── _tidy_up ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tidy_up_preserves_agent_files(tmp_path: Path) -> None:
    """Tidy-up cleans caches but does NOT delete source/test files.

    File lifecycle is the mission agent's responsibility — tidy-up
    only removes __pycache__ and .pyc files.
    """
    from backend.crew.code_gen import _tidy_up

    src = tmp_path / "src"
    src.mkdir()
    (src / "foo.py").write_text("# expected")
    (src / "other.py").write_text("# agent created this with a different name")
    cache = src / "__pycache__"
    cache.mkdir()
    (cache / "foo.cpython-312.pyc").write_text("bytecode")

    await _tidy_up(tmp_path)
    # Both source files preserved — agent decides what to keep
    assert (src / "foo.py").exists()
    assert (src / "other.py").exists()
    # Cache cleaned
    assert not cache.exists()


@pytest.mark.asyncio
async def test_tidy_up_detects_orphans(tmp_path: Path) -> None:
    """Tidy-up should detect files not matching any graph node."""
    graph: Any = MagicMock()
    graph.all_nodes.return_value = [
        _make_node("D-1", "DESIGN", "Auth Service"),
    ]

    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    (src_dir / "auth_service.py").write_text("pass\n")
    (src_dir / "old_module.py").write_text("pass\n")  # orphan

    orphans = _find_graph_orphans(tmp_path, graph)
    assert "src/old_module.py" in orphans
    assert "src/auth_service.py" not in orphans


# ── Trace validation ────────────────────────────────────────────────────────

def test_validate_trace_ids_all_valid() -> None:
    """No invalid traces when all LLR IDs exist in the graph."""
    from backend.crew.trace_auditor import _validate_trace_ids

    traces = [LineTrace(start=1, end=5, llr_ids=["LLR-0001"], symbol="foo")]
    valid_ids = {"LLR-0001", "LLR-0002"}
    assert _validate_trace_ids(traces, valid_ids) == []


def test_validate_trace_ids_unknown() -> None:
    """Unknown LLR IDs are flagged as invalid."""
    from backend.crew.trace_auditor import _validate_trace_ids

    traces = [
        LineTrace(start=1, end=5, llr_ids=["LLR-0001", "LLR-9999"], symbol="bar"),
    ]
    valid_ids = {"LLR-0001"}
    invalid = _validate_trace_ids(traces, valid_ids)
    assert len(invalid) == 1
    assert invalid[0].invalid_llr_ids == ["LLR-9999"]
    assert invalid[0].function_name == "bar"
    assert invalid[0].reason == "unknown_id"


def test_validate_trace_ids_empty_graph() -> None:
    """All traces are invalid when the graph has no LLR nodes."""
    from backend.crew.trace_auditor import _validate_trace_ids

    traces = [LineTrace(start=1, end=3, llr_ids=["LLR-0001"], symbol="baz")]
    invalid = _validate_trace_ids(traces, set())
    assert len(invalid) == 1


# ── _compute_function_coverage ────────────────────────────────────────────────

def test_compute_function_coverage_multiple_files() -> None:
    """Coverage aggregates traced/total across all source files."""
    from backend.crew.codegen_helpers import compute_function_coverage as _compute_function_coverage

    result = CodeGenResult(
        source_files=[
            GeneratedFile("D-1", "src/a.py", [], total_functions=10, traced_functions=8),
            GeneratedFile("D-2", "src/b.py", [], total_functions=5, traced_functions=3),
        ],
    )
    assert _compute_function_coverage(result) == "11/15"


def test_compute_function_coverage_empty() -> None:
    """Empty result returns '0/0'."""
    from backend.crew.codegen_helpers import compute_function_coverage as _compute_function_coverage

    result = CodeGenResult()
    assert _compute_function_coverage(result) == "0/0"


def test_compute_function_coverage_all_traced() -> None:
    """All functions traced shows N/N."""
    from backend.crew.codegen_helpers import compute_function_coverage as _compute_function_coverage

    result = CodeGenResult(
        source_files=[
            GeneratedFile("D-1", "src/a.py", [], total_functions=4, traced_functions=4),
        ],
    )
    assert _compute_function_coverage(result) == "4/4"


# ── _compute_requirement_coverage ─────────────────────────────────────────────

def test_compute_requirement_coverage_passing_tests() -> None:
    """Passing test functions covering LLRs are counted as covered."""
    from backend.crew.codegen_helpers import (
        compute_requirement_coverage as _compute_requirement_coverage,
    )

    state: Any = MagicMock()
    state.test_results = [
        MagicMock(status="passed", file_path="tests/test_foo.py", function_name="test_a"),
    ]
    state.test_files = {
        "tests/test_foo.py": MagicMock(
            traces=[LineTrace(start=1, end=5, llr_ids=["LLR-001", "LLR-002"], symbol="test_a")],
        ),
    }
    state.source_files = {
        "src/foo.py": MagicMock(
            traces=[LineTrace(start=1, end=5, llr_ids=["LLR-001", "LLR-002"], symbol="foo")],
        ),
    }

    graph: Any = MagicMock()
    graph.all_nodes.return_value = [
        _make_node("LLR-001", "LLR", "Req 1"),
        _make_node("LLR-002", "LLR", "Req 2"),
    ]

    assert _compute_requirement_coverage(state, graph) == "2/2"


def test_compute_requirement_coverage_needs_source_traces() -> None:
    """A passing traced test alone is NOT coverage — the LLR must also be
    cited by a source-file @traces (rank-1 live-run repro: 'Req 53/53'
    while 15 LLRs never reached src/)."""
    from backend.crew.codegen_helpers import (
        compute_requirement_coverage_detail as _detail,
    )

    state: Any = MagicMock()
    state.test_results = [
        MagicMock(status="passed", file_path="tests/test_foo.py", function_name="test_a"),
    ]
    state.test_files = {
        "tests/test_foo.py": MagicMock(
            traces=[LineTrace(start=1, end=5, llr_ids=["LLR-001"], symbol="test_a")],
        ),
    }
    state.source_files = {"src/foo.py": MagicMock(traces=[])}

    graph: Any = MagicMock()
    graph.all_nodes.return_value = [_make_node("LLR-001", "LLR", "Req 1")]

    detail = _detail(state, graph)
    assert detail["covered"] == set()
    assert detail["uncovered"] == ["LLR-001"]
    assert detail["unimplemented"] == ["LLR-001"]


def test_compute_requirement_coverage_failed_tests_not_counted() -> None:
    """Failed tests don't count as coverage evidence."""
    from backend.crew.codegen_helpers import (
        compute_requirement_coverage as _compute_requirement_coverage,
    )

    state: Any = MagicMock()
    state.test_results = [
        MagicMock(status="failed", file_path="tests/test_foo.py", function_name="test_a"),
    ]
    state.test_files = {
        "tests/test_foo.py": MagicMock(
            traces=[LineTrace(start=1, end=5, llr_ids=["LLR-001"], symbol="test_a")],
        ),
    }
    state.source_files = {
        "src/foo.py": MagicMock(
            traces=[LineTrace(start=1, end=5, llr_ids=["LLR-001"], symbol="foo")],
        ),
    }

    graph: Any = MagicMock()
    graph.all_nodes.return_value = [
        _make_node("LLR-001", "LLR", "Req 1"),
    ]

    assert _compute_requirement_coverage(state, graph) == "0/1"


def test_compute_requirement_coverage_no_llr_nodes() -> None:
    """No LLR nodes in graph returns '0/0'."""
    from backend.crew.codegen_helpers import (
        compute_requirement_coverage as _compute_requirement_coverage,
    )

    state: Any = MagicMock()
    state.test_results = []
    state.test_files = {}
    state.source_files = {}

    graph: Any = MagicMock()
    graph.all_nodes.return_value = [
        _make_node("D-1", "DESIGN", "Something"),
    ]

    assert _compute_requirement_coverage(state, graph) == "0/0"


# ── _find_graph_orphans (additional cases) ────────────────────────────────────

def test_find_graph_orphans_test_files(tmp_path: Path) -> None:
    """Orphaned test files in tests/ are detected."""
    graph: Any = MagicMock()
    graph.all_nodes.return_value = [
        _make_node("C-1", "CASE_HLR", "Login Test"),
    ]

    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_login_test.py").write_text("pass\n")
    (tests_dir / "test_old_stuff.py").write_text("pass\n")  # orphan

    orphans = _find_graph_orphans(tmp_path, graph)
    assert "tests/test_old_stuff.py" in orphans
    assert "tests/test_login_test.py" not in orphans


def test_find_graph_orphans_init_excluded(tmp_path: Path) -> None:
    """__init__.py files are excluded from orphan detection."""
    graph: Any = MagicMock()
    graph.all_nodes.return_value = []

    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "__init__.py").write_text("")

    orphans = _find_graph_orphans(tmp_path, graph)
    assert orphans == []


# ── _remove_broken_files ──────────────────────────────────────────────────

def test_remove_broken_files_keeps_valid_test(tmp_path: Path) -> None:
    """A valid test file importing an existing src module is kept."""
    from backend.crew.code_gen import _remove_broken_files

    src = tmp_path / "src"
    src.mkdir()
    (src / "calculator.py").write_text("def add(a, b): return a + b\n")

    tests = tmp_path / "tests"
    tests.mkdir()
    test_file = tests / "test_calculator.py"
    test_file.write_text(
        "import calculator\n\ndef test_add():\n    assert calculator.add(1, 2) == 3\n"
    )

    _remove_broken_files(tmp_path)
    assert test_file.exists(), "Valid test file should NOT be removed"


def test_remove_broken_files_removes_syntax_error(tmp_path: Path) -> None:
    """A test file with a syntax error is removed."""
    from backend.crew.code_gen import _remove_broken_files

    src = tmp_path / "src"
    src.mkdir()
    tests = tmp_path / "tests"
    tests.mkdir()
    bad_file = tests / "test_broken.py"
    bad_file.write_text("def test_bad(\n    # missing closing paren\n")

    _remove_broken_files(tmp_path)
    assert not bad_file.exists(), "Syntax-error file should be removed"


def test_remove_broken_files_removes_absent_src_import(tmp_path: Path) -> None:
    """A test file importing a src.* module absent from src/ is removed."""
    from backend.crew.code_gen import _remove_broken_files

    src = tmp_path / "src"
    src.mkdir()
    (src / "real_module.py").write_text("x = 1\n")

    tests = tmp_path / "tests"
    tests.mkdir()
    stale_file = tests / "test_stale.py"
    stale_file.write_text(
        "from src.nonexistent_module import thing\n\ndef test_it():\n    pass\n"
    )

    _remove_broken_files(tmp_path)
    assert not stale_file.exists(), "File importing absent src.* module should be removed"


def test_remove_broken_files_keeps_stdlib_imports(tmp_path: Path) -> None:
    """Rank-7 live-run repro: datetime/random/unittest imports are valid.

    The old 23-entry hand-list omitted these stdlib modules, so valid
    passing test files were deleted on every phase-12 (re-)entry.
    """
    from backend.crew.code_gen import _remove_broken_files

    (tmp_path / "src").mkdir()
    tests = tmp_path / "tests"
    tests.mkdir()
    good = tests / "test_times.py"
    good.write_text(
        "import datetime\n"
        "import random\n"
        "import string\n"
        "import statistics\n"
        "from unittest import mock\n"
        "from contextlib import suppress\n"
        "\n"
        "def test_it():\n    assert datetime.MINYEAR == 1\n"
    )

    _remove_broken_files(tmp_path)
    assert good.exists(), "Test importing stdlib modules must NOT be deleted"


def test_remove_broken_files_keeps_unknown_third_party(tmp_path: Path) -> None:
    """Unknown third-party roots surface as gaps, never as deletions."""
    from backend.crew.code_gen import _remove_broken_files

    (tmp_path / "src").mkdir()
    tests = tmp_path / "tests"
    tests.mkdir()
    third_party = tests / "test_hyp.py"
    third_party.write_text("import hypothesis\n\ndef test_it():\n    pass\n")

    _remove_broken_files(tmp_path)
    assert third_party.exists(), "Third-party import must not trigger deletion"


def test_remove_broken_files_ignores_docstring_import_lines(tmp_path: Path) -> None:
    """Import-shaped lines inside docstrings are not real imports.

    The old regex scan matched them and deleted the file.
    """
    from backend.crew.code_gen import _remove_broken_files

    (tmp_path / "src").mkdir()
    tests = tmp_path / "tests"
    tests.mkdir()
    doc_file = tests / "test_doc.py"
    doc_file.write_text(
        '"""Example usage:\n\nimport src.gone_module\nfrom src.also_gone import x\n"""\n'
        "\ndef test_it():\n    pass\n"
    )

    _remove_broken_files(tmp_path)
    assert doc_file.exists(), "Docstring import lines must not trigger deletion"


# ── Trace validation ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_persist_single_file_design_node() -> None:
    """Persist builds correct props for a DESIGN node."""
    from backend.crew.code_gen import _persist_single_file

    node = _make_node("D-1", "DESIGN", "Auth", properties={})
    graph: Any = MagicMock()
    graph.node_sync.return_value = node
    graph.update_node = AsyncMock()

    gf = GeneratedFile(
        node_id="D-1",
        file_path="src/auth.py",
        line_traces=[LineTrace(start=1, end=5, llr_ids=["LLR-001"], symbol="validate")],
        total_functions=3,
        traced_functions=2,
    )

    await _persist_single_file(gf, graph)

    graph.update_node.assert_called_once()
    call_kwargs = graph.update_node.call_args
    props = call_kwargs.kwargs.get("properties") or call_kwargs[1].get("properties")
    assert props["file_path"] == "src/auth.py"
    assert props["traced_llrs"] == ["LLR-001"]
    assert props["trace_coverage"] == {"total": 3, "traced": 2}
    assert len(props["line_traces"]) == 1


@pytest.mark.asyncio
async def test_persist_single_file_case_injects_case_id() -> None:
    """CASE nodes get their node_id injected into trace case_ids."""
    from backend.crew.code_gen import _persist_single_file

    node = _make_node("C-1", "CASE_HLR", "Login Test", properties={})
    graph: Any = MagicMock()
    graph.node_sync.return_value = node
    graph.update_node = AsyncMock()

    gf = GeneratedFile(
        node_id="C-1",
        file_path="tests/test_login.py",
        line_traces=[LineTrace(start=1, end=5, llr_ids=["LLR-001"], symbol="test_login")],
        total_functions=1,
        traced_functions=1,
    )

    await _persist_single_file(gf, graph)

    call_kwargs = graph.update_node.call_args
    props = call_kwargs.kwargs.get("properties") or call_kwargs[1].get("properties")
    trace_dict = props["line_traces"][0]
    assert "C-1" in trace_dict["case_ids"]


@pytest.mark.asyncio
async def test_persist_single_file_missing_node() -> None:
    """If node not found in graph, persist is a no-op."""
    from backend.crew.code_gen import _persist_single_file

    graph: Any = MagicMock()
    graph.node_sync.return_value = None
    graph.update_node = AsyncMock()

    gf = GeneratedFile(node_id="D-GONE", file_path="src/gone.py")
    await _persist_single_file(gf, graph)

    graph.update_node.assert_not_called()


# ── _persist_traces stale cleanup ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_persist_traces_clears_stale_nodes() -> None:
    """Nodes with stale file_path not in result get trace props cleared."""
    from backend.crew.code_gen import _persist_traces

    current_node = _make_node("D-1", "DESIGN", "Active", properties={})
    stale_node = _make_node(
        "D-OLD", "DESIGN", "Old",
        properties={
            "file_path": "src/old.py",
            "line_traces": [{"llr_ids": ["LLR-X"]}],
            "trace_coverage": {"total": 1, "traced": 0},
            "keep_me": "preserved",
        },
    )

    graph: Any = MagicMock()
    graph.node_sync.return_value = current_node
    graph.all_nodes.return_value = [current_node, stale_node]
    graph.update_node = AsyncMock()

    result = CodeGenResult(
        source_files=[GeneratedFile("D-1", "src/active.py")],
    )

    await _persist_traces(result, graph)

    # Should have been called twice: once for D-1, once to clear D-OLD
    assert graph.update_node.call_count == 2

    # Find the stale cleanup call (the one for D-OLD)
    stale_call = [
        c for c in graph.update_node.call_args_list
        if c[0][0] == "D-OLD" or c.kwargs.get("node_id") == "D-OLD"
        or (len(c[0]) > 0 and c[0][0] == "D-OLD")
    ]
    assert len(stale_call) == 1
    cleaned_props = stale_call[0].kwargs.get("properties") or stale_call[0][1].get("properties")
    assert "file_path" not in cleaned_props
    assert "line_traces" not in cleaned_props
    assert cleaned_props.get("keep_me") == "preserved"


@pytest.mark.asyncio
async def test_persist_traces_skips_non_design_case() -> None:
    """Non-DESIGN/CASE nodes are never cleaned even with stale props."""
    from backend.crew.code_gen import _persist_traces

    other_node = _make_node(
        "M-1", "MODULE", "Module",
        properties={"file_path": "src/something.py", "line_traces": []},
    )

    graph: Any = MagicMock()
    graph.node_sync.return_value = None
    graph.all_nodes.return_value = [other_node]
    graph.update_node = AsyncMock()

    result = CodeGenResult()
    await _persist_traces(result, graph)

    # MODULE nodes are skipped during stale cleanup
    graph.update_node.assert_not_called()


# ── compute_value ─────────────────────────────────────────────────────────────

def test_compute_value_includes_mcdc() -> None:
    """MC/DC (branch coverage) is a gating dimension in compute_value."""
    from backend.crew.mission_agent import compute_value
    from backend.crew.workspace_scanner import FileState, WorkspaceState

    ws = WorkspaceState(
        source_files={
            "src/foo.py": FileState(
                path="src/foo.py",
                traces=[LineTrace(start=1, end=5, llr_ids=["LLR-001"], symbol="foo")],
                total_functions=1,
                traced_functions=1,
            ),
        },
        test_results=[MagicMock(status="passed")],
        coverage_pct=100.0,
        branch_coverage_pct=50.0,
    )
    graph: Any = MagicMock()
    graph.all_nodes.return_value = [_make_node("LLR-001", "LLR", "Req")]

    score = compute_value(ws, graph)
    # branch_coverage_pct=50% → mcdc_score=0.5, which is the minimum
    assert score == 0.5


def test_compute_value_all_100_returns_1() -> None:
    """All dimensions at 100% yields score 1.0."""
    from backend.crew.mission_agent import compute_value
    from backend.crew.workspace_scanner import FileState, WorkspaceState

    ws = WorkspaceState(
        source_files={
            "src/foo.py": FileState(
                path="src/foo.py",
                traces=[LineTrace(start=1, end=5, llr_ids=["LLR-001"], symbol="foo")],
                total_functions=1,
                traced_functions=1,
            ),
        },
        test_results=[MagicMock(status="passed")],
        coverage_pct=100.0,
        branch_coverage_pct=100.0,
    )
    graph: Any = MagicMock()
    graph.all_nodes.return_value = [_make_node("LLR-001", "LLR", "Req")]

    assert compute_value(ws, graph) == 1.0


# ── _persist_coverage_metrics ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_persist_coverage_metrics_happy_path() -> None:
    """DESIGN node updated with rounded coverage values."""
    from backend.crew.code_gen import _persist_coverage_metrics

    design = _make_node("D-1", "DESIGN", "Main Design", properties={"existing": "keep"})
    graph: Any = MagicMock()
    graph.all_nodes.return_value = [design]
    graph.update_node = AsyncMock()

    last_state = MagicMock()
    last_state.coverage_pct = 92.0
    last_state.branch_coverage_pct = 85.0

    await _persist_coverage_metrics(graph, last_state)

    graph.update_node.assert_called_once()
    call_args = graph.update_node.call_args[0]
    assert call_args[0] == "D-1"  # node_id
    props = call_args[2]  # properties dict
    assert props["statement_coverage"] == 92.0
    assert props["branch_coverage"] == 85.0
    assert props["existing"] == "keep"


@pytest.mark.asyncio
async def test_persist_coverage_metrics_last_state_none() -> None:
    """last_state is None → no-op, update_node never called."""
    from backend.crew.code_gen import _persist_coverage_metrics

    graph: Any = MagicMock()
    graph.update_node = AsyncMock()

    await _persist_coverage_metrics(graph, None)

    graph.update_node.assert_not_called()


@pytest.mark.asyncio
async def test_persist_coverage_metrics_no_design_nodes() -> None:
    """No DESIGN nodes in graph → no-op."""
    from backend.crew.code_gen import _persist_coverage_metrics

    graph: Any = MagicMock()
    graph.all_nodes.return_value = [
        _make_node("H-1", "HLR", "Some requirement"),
    ]
    graph.update_node = AsyncMock()

    last_state = MagicMock()
    last_state.coverage_pct = 80.0
    last_state.branch_coverage_pct = 70.0

    await _persist_coverage_metrics(graph, last_state)

    graph.update_node.assert_not_called()


@pytest.mark.asyncio
async def test_persist_coverage_metrics_coverage_pct_none() -> None:
    """coverage_pct is None → statement_coverage not added to props."""
    from backend.crew.code_gen import _persist_coverage_metrics

    design = _make_node("D-1", "DESIGN", "Design", properties={})
    graph: Any = MagicMock()
    graph.all_nodes.return_value = [design]
    graph.update_node = AsyncMock()

    last_state = MagicMock()
    last_state.coverage_pct = None
    last_state.branch_coverage_pct = 85.0

    await _persist_coverage_metrics(graph, last_state)

    props = graph.update_node.call_args[0][2]
    assert "statement_coverage" not in props
    assert props["branch_coverage"] == 85.0


@pytest.mark.asyncio
async def test_persist_coverage_metrics_branch_pct_none() -> None:
    """branch_coverage_pct is None → branch_coverage not added to props."""
    from backend.crew.code_gen import _persist_coverage_metrics

    design = _make_node("D-1", "DESIGN", "Design", properties={})
    graph: Any = MagicMock()
    graph.all_nodes.return_value = [design]
    graph.update_node = AsyncMock()

    last_state = MagicMock()
    last_state.coverage_pct = 92.0
    last_state.branch_coverage_pct = None

    await _persist_coverage_metrics(graph, last_state)

    props = graph.update_node.call_args[0][2]
    assert props["statement_coverage"] == 92.0
    assert "branch_coverage" not in props


@pytest.mark.asyncio
async def test_persist_coverage_metrics_preserves_existing_props() -> None:
    """Existing properties on DESIGN node are preserved after update."""
    from backend.crew.code_gen import _persist_coverage_metrics

    design = _make_node(
        "D-1", "DESIGN", "Design",
        properties={"file_path": "src/main.py", "traced_llrs": ["LLR-001"]},
    )
    graph: Any = MagicMock()
    graph.all_nodes.return_value = [design]
    graph.update_node = AsyncMock()

    last_state = MagicMock()
    last_state.coverage_pct = 95.5
    last_state.branch_coverage_pct = 88.3

    await _persist_coverage_metrics(graph, last_state)

    props = graph.update_node.call_args[0][2]
    assert props["file_path"] == "src/main.py"
    assert props["traced_llrs"] == ["LLR-001"]
    assert props["statement_coverage"] == 95.5
    assert props["branch_coverage"] == 88.3


# ── _log_phase_statistics ─────────────────────────────────────────────────────


def test_log_phase_statistics_calls_forge_logger() -> None:
    """_log_phase_statistics emits header and detail lines via forge_logger."""
    from backend.crew.code_gen import _log_phase_statistics
    from backend.crew.mission_agent import MissionStats

    stats = MissionStats(
        total_tool_calls=42,
        total_elapsed_s=123.4,
        final_score=0.85,
        final_gap_count=3,
        stop_reason="max_iterations",
    )

    with patch("backend.crew.code_gen.forge_logger") as mock_logger:
        _log_phase_statistics(stats)

        assert mock_logger.emit.call_count == 2
        header_call = mock_logger.emit.call_args_list[0]
        assert "Phase 12 Detailed Statistics" in header_call[0][2]

        detail_call = mock_logger.emit.call_args_list[1]
        detail_msg = detail_call[0][2]
        assert "Tool calls: 42" in detail_msg
        assert "Wall time: 123.4s" in detail_msg
        assert "Final score: 85%" in detail_msg
        assert "Remaining gaps: 3" in detail_msg
        assert "Stop reason: max_iterations" in detail_msg


def test_log_phase_statistics_default_values() -> None:
    """MissionStats with default values is handled without error."""
    from backend.crew.code_gen import _log_phase_statistics
    from backend.crew.mission_agent import MissionStats

    stats = MissionStats()

    with patch("backend.crew.code_gen.forge_logger") as mock_logger:
        _log_phase_statistics(stats)

        assert mock_logger.emit.call_count == 2
        detail_call = mock_logger.emit.call_args_list[1]
        detail_msg = detail_call[0][2]
        assert "Tool calls: 0" in detail_msg
        assert "Wall time: 0.0s" in detail_msg
        assert "Final score: 0%" in detail_msg
        assert "Remaining gaps: 0" in detail_msg


# ── Coverage gate tests ──────────────────────────────────────────────────────


def _state(
    *,
    tests: list[tuple[str, str, str]] | None = None,
    coverage_pct: float | None = None,
    branch_pct: float | None = None,
    test_files: dict[str, Any] | None = None,
    source_files: dict[str, Any] | None = None,
) -> SimpleNamespace:
    """Build a minimal WorkspaceState-like object for gate testing."""
    results = []
    for status, path, fn in tests or []:
        results.append(SimpleNamespace(status=status, file_path=path, function_name=fn))
    return SimpleNamespace(
        test_results=results,
        test_files=test_files or {},
        source_files=source_files or {},
        coverage_pct=coverage_pct,
        branch_coverage_pct=branch_pct,
    )


def _graph_with_llrs(ids: list[str]) -> Any:
    """Return a MagicMock standing in for ProjectGraph (typed Any — it is a mock)."""
    g = MagicMock()
    nodes = []
    for nid in ids:
        n = MagicMock()
        n.node_id = nid
        n.node_type = "LLR"
        nodes.append(n)
    g.all_nodes.return_value = nodes
    return g


def _result_with_source() -> CodeGenResult:
    gf = GeneratedFile(
        node_id="CODE-0001", file_path="src/mod.py",
        traced_functions=1, total_functions=1, line_traces=[],
    )
    return CodeGenResult(source_files=[gf], test_files=[])


def _src_files_tracing(llr_ids: list[str]) -> dict[str, Any]:
    """A source-file map whose single file carries @traces for *llr_ids*."""
    trace = LineTrace(start=1, end=1, symbol="mod_fn", llr_ids=llr_ids)
    return {"src/mod.py": SimpleNamespace(traces=[trace])}


def test_coverage_gate_raises_when_llrs_uncovered() -> None:
    # 2 LLRs in graph, tests only trace one of them.
    trace = LineTrace(start=1, end=1, symbol="test_one", llr_ids=["LLR-0001"])
    tf = SimpleNamespace(traces=[trace])
    state = _state(
        tests=[("passed", "tests/test_one.py", "test_one")],
        coverage_pct=100.0, branch_pct=100.0,
        test_files={"tests/test_one.py": tf},
        source_files=_src_files_tracing(["LLR-0001", "LLR-0002"]),
    )
    graph = _graph_with_llrs(["LLR-0001", "LLR-0002"])
    with pytest.raises(CodeGenIncompleteError, match="uncovered:"):
        _enforce_coverage_gate(state, graph, _result_with_source())


def test_coverage_gate_raises_when_llr_has_no_source_traces() -> None:
    """Rank-1 live-run repro: passing traced test but no implementing code.

    Every LLR has a passing test citing it, yet no source function
    carries @traces for it — the gate must fail loudly instead of
    reporting 'Req N/N'.
    """
    trace = LineTrace(start=1, end=1, symbol="test_one", llr_ids=["LLR-0001"])
    tf = SimpleNamespace(traces=[trace])
    state = _state(
        tests=[("passed", "tests/test_one.py", "test_one")],
        coverage_pct=100.0, branch_pct=100.0,
        test_files={"tests/test_one.py": tf},
        source_files={"src/mod.py": SimpleNamespace(traces=[])},
    )
    graph = _graph_with_llrs(["LLR-0001"])
    with pytest.raises(CodeGenIncompleteError, match="no implementing source"):
        _enforce_coverage_gate(state, graph, _result_with_source())


def test_coverage_gate_raises_when_stmt_below_100() -> None:
    state = _state(
        tests=[("passed", "tests/t.py", "t")],
        coverage_pct=80.0, branch_pct=100.0,
    )
    with pytest.raises(CodeGenIncompleteError, match="statement coverage"):
        _enforce_coverage_gate(state, _graph_with_llrs([]), _result_with_source())


def test_coverage_gate_raises_when_branch_below_100() -> None:
    state = _state(
        tests=[("passed", "tests/t.py", "t")],
        coverage_pct=100.0, branch_pct=60.0,
    )
    with pytest.raises(CodeGenIncompleteError, match="branch/MC-DC"):
        _enforce_coverage_gate(state, _graph_with_llrs([]), _result_with_source())


def test_coverage_gate_raises_when_any_test_failed() -> None:
    state = _state(
        tests=[("passed", "tests/t.py", "a"), ("failed", "tests/t.py", "b")],
        coverage_pct=100.0, branch_pct=100.0,
    )
    with pytest.raises(CodeGenIncompleteError, match="1 test.*failed"):
        _enforce_coverage_gate(state, _graph_with_llrs([]), _result_with_source())


def test_coverage_gate_passes_when_everything_100() -> None:
    trace1 = LineTrace(start=1, end=1, symbol="test_one", llr_ids=["LLR-0001"])
    trace2 = LineTrace(start=2, end=2, symbol="test_two", llr_ids=["LLR-0002"])
    tf = SimpleNamespace(traces=[trace1, trace2])
    state = _state(
        tests=[
            ("passed", "tests/t.py", "test_one"),
            ("passed", "tests/t.py", "test_two"),
        ],
        coverage_pct=100.0, branch_pct=100.0,
        test_files={"tests/t.py": tf},
        source_files=_src_files_tracing(["LLR-0001", "LLR-0002"]),
    )
    graph = _graph_with_llrs(["LLR-0001", "LLR-0002"])
    # No exception.
    _enforce_coverage_gate(state, graph, _result_with_source())


def test_coverage_gate_skips_when_graph_empty() -> None:
    # Empty graph + empty test results → trivially OK (used in unit tests
    # of run_code_gen that mock out the pipeline).
    state = _state(tests=[], coverage_pct=None, branch_pct=None)
    _enforce_coverage_gate(state, _graph_with_llrs([]), CodeGenResult())
