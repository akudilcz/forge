"""Tests for backend.tools.insert_lines — line-number insertion tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.tools.insert_lines import InsertLinesTool


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a workspace with a sample Python file."""
    f = tmp_path / "src" / "foo.py"
    f.parent.mkdir(parents=True)
    f.write_text("def foo():\n    pass\n\ndef bar():\n    return 1\n")
    return tmp_path


def _tool(workspace: Path) -> InsertLinesTool:
    return InsertLinesTool(workspace=str(workspace))


def test_insert_before_function_def(workspace: Path) -> None:
    """Insert a @traces decorator before a function definition."""
    tool = _tool(workspace)
    result = tool._execute(path="src/foo.py", after_line=0, text='@traces("LLR-0042")')
    assert result.startswith("OK")

    content = (workspace / "src" / "foo.py").read_text()
    lines = content.splitlines()
    assert lines[0] == '@traces("LLR-0042")'
    assert lines[1] == "def foo():"
    assert lines[2] == "    pass"


def test_insert_at_top(workspace: Path) -> None:
    """Insert at line 0 should prepend."""
    tool = _tool(workspace)
    result = tool._execute(path="src/foo.py", after_line=0, text="# header")
    assert result.startswith("OK")

    content = (workspace / "src" / "foo.py").read_text()
    assert content.startswith("# header\n")


def test_insert_at_end(workspace: Path) -> None:
    """Insert after the last line should append."""
    tool = _tool(workspace)
    original_lines = (workspace / "src" / "foo.py").read_text().splitlines()
    last = len(original_lines)
    result = tool._execute(path="src/foo.py", after_line=last, text="# end")
    assert result.startswith("OK")

    content = (workspace / "src" / "foo.py").read_text()
    assert content.rstrip().endswith("# end")


def test_insert_file_not_found(workspace: Path) -> None:
    """Should return an error for missing files."""
    tool = _tool(workspace)
    result = tool._execute(path="nope.py", after_line=1, text="x")
    assert result.startswith("ERROR")


def test_insert_line_out_of_range(workspace: Path) -> None:
    """Should return an error for out-of-range line numbers."""
    tool = _tool(workspace)
    result = tool._execute(path="src/foo.py", after_line=999, text="x")
    assert "out of range" in result


def test_insert_negative_line(workspace: Path) -> None:
    """Negative line numbers are invalid."""
    tool = _tool(workspace)
    result = tool._execute(path="src/foo.py", after_line=-1, text="x")
    assert "out of range" in result


def test_insert_preserves_other_lines(workspace: Path) -> None:
    """Insertion should not modify surrounding lines."""
    tool = _tool(workspace)
    original = (workspace / "src" / "foo.py").read_text().splitlines()
    tool._execute(path="src/foo.py", after_line=3, text='@traces("LLR-0001")')

    updated = (workspace / "src" / "foo.py").read_text().splitlines()
    assert updated[0] == original[0]  # def foo():
    assert updated[1] == original[1]  # pass
    assert updated[3] == '@traces("LLR-0001")'
    assert updated[4] == original[3]  # def bar():
    assert updated[5] == original[4]  # return 1


def test_concurrent_inserts_same_file(workspace: Path) -> None:
    """Parallel insert_lines on the same file should all succeed.

    Simulates what happens when the LLM fires multiple tool calls
    at once (e.g. adding @traces to 5 functions in one turn).
    """
    import concurrent.futures

    tool = _tool(workspace)
    # File has 5 lines: def foo / pass / (blank) / def bar / return 1
    # Insert decorators before both functions: after line 0 and after line 3
    # But since inserts are serialised, the second call sees the shifted file.

    calls = [
        (0, '@traces("LLR-0001")'),   # before def foo
        (3, '@traces("LLR-0002")'),   # before def bar (original line 4)
    ]

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(tool._execute, path="src/foo.py", after_line=line, text=text)
            for line, text in calls
        ]
        results = [f.result() for f in futures]

    # Both should succeed (lock serialises them)
    for r in results:
        assert r.startswith("OK"), f"Expected OK, got: {r}"

    content = (workspace / "src" / "foo.py").read_text()
    assert '@traces("LLR-0001")' in content
    assert '@traces("LLR-0002")' in content


def test_many_concurrent_inserts(workspace: Path) -> None:
    """Stress test: 8 parallel inserts on the same file."""
    import concurrent.futures

    tool = _tool(workspace)
    # Insert 8 comment lines at different positions
    calls = [(i % 5, f"# comment {i}") for i in range(8)]

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(tool._execute, path="src/foo.py", after_line=line, text=text)
            for line, text in calls
        ]
        results = [f.result() for f in futures]

    # All should succeed
    success_count = sum(1 for r in results if r.startswith("OK"))
    assert success_count == 8, (
        f"Expected 8 successes, got {success_count}. "
        f"Results: {results}"
    )

    # All 8 comments should be in the file
    content = (workspace / "src" / "foo.py").read_text()
    for i in range(8):
        assert f"# comment {i}" in content, f"Missing comment {i}"


def test_trace_reproduction_8_parallel_decorator_inserts(tmp_path: Path) -> None:
    """Reproduce the exact failure from the live trace.

    The agent fired 8 parallel insert_lines calls to add @traces
    decorators at lines 25, 30, 54, 73, 130, 172, 221, 265.
    Without the lock, only the first succeeded — the rest got
    "out of range (file has 0 lines)" because the file was being
    read/written concurrently.
    """
    import concurrent.futures

    # Create a file with 270+ lines (like test_type_annotation_compliance.py)
    lines = []
    for i in range(270):
        lines.append(f"    # line {i + 1}\n")
    lines[0] = "class TestAnnotations:\n"
    (tmp_path / "test_annotations.py").write_text("".join(lines))

    tool = InsertLinesTool(workspace=str(tmp_path))

    # These are the exact line numbers from the trace
    insert_points = [25, 30, 54, 73, 130, 172, 221, 265]
    calls = [
        (line, f'    @traces("LLR-{i:04d}")')
        for i, line in enumerate(insert_points)
    ]

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = [
            pool.submit(
                tool._execute,
                path="test_annotations.py",
                after_line=line,
                text=text,
            )
            for line, text in calls
        ]
        results = [f.result() for f in futures]

    # ALL 8 should succeed — no "out of range" errors
    for i, r in enumerate(results):
        assert r.startswith("OK"), (
            f"Insert at line {insert_points[i]} failed: {r}"
        )

    # All decorators should be in the file
    content = (tmp_path / "test_annotations.py").read_text()
    for i in range(len(insert_points)):
        assert f'@traces("LLR-{i:04d}")' in content, (
            f"Missing decorator LLR-{i:04d}"
        )


def test_concurrent_inserts_different_files(tmp_path: Path) -> None:
    """Parallel inserts on different files should not interfere."""
    import concurrent.futures

    (tmp_path / "a.py").write_text("line1\nline2\n")
    (tmp_path / "b.py").write_text("line1\nline2\n")
    tool = InsertLinesTool(workspace=str(tmp_path))

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(tool._execute, path="a.py", after_line=1, text="# a")
        f2 = pool.submit(tool._execute, path="b.py", after_line=1, text="# b")
        r1, r2 = f1.result(), f2.result()

    assert r1.startswith("OK")
    assert r2.startswith("OK")
    assert "# a" in (tmp_path / "a.py").read_text()
    assert "# b" in (tmp_path / "b.py").read_text()
    # Cross-contamination check
    assert "# b" not in (tmp_path / "a.py").read_text()
    assert "# a" not in (tmp_path / "b.py").read_text()
