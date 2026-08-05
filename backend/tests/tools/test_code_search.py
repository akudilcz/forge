"""Tests for CodeSearchTool."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.tools.code_search import CodeSearchTool


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "main.py").write_text("def main():\n    print('hello')\n")
    (tmp_path / "utils.py").write_text("def helper():\n    return 42\n")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "module.py").write_text("class Foo:\n    def bar(self): pass\n")
    return tmp_path


def test_search_finds_pattern_case_insensitive(workspace: Path) -> None:
    tool = CodeSearchTool(workspace=str(workspace))
    result = tool._execute(pattern="def main")
    assert "main.py" in result
    assert "def main" in result

    result = tool._execute(pattern="PRINT")
    assert "main.py" in result


def test_search_no_matches(workspace: Path) -> None:
    assert "No matches found" in CodeSearchTool(workspace=str(workspace))._execute(pattern="xyzzy_not_found")


def test_search_with_glob_filter(workspace: Path) -> None:
    result = CodeSearchTool(workspace=str(workspace))._execute(pattern="def", glob="**/utils.py")
    assert "utils.py" in result
    assert "main.py" not in result


def test_search_max_results_respected(workspace: Path) -> None:
    many_matches = "\n".join([f"# pattern line {i}" for i in range(100)])
    (workspace / "big.py").write_text(many_matches)
    result = CodeSearchTool(workspace=str(workspace))._execute(pattern="# pattern", max_results=5)
    lines = [line for line in result.split("\n") if "big.py" in line]
    assert len(lines) <= 5


def test_search_skips_excluded_dirs(workspace: Path) -> None:
    (workspace / ".git").mkdir()
    (workspace / ".git" / "config").write_text("def hidden_function(): pass")
    (workspace / "__pycache__").mkdir()
    (workspace / "__pycache__" / "cached.py").write_text("def cached(): pass")
    tool = CodeSearchTool(workspace=str(workspace))
    assert "No matches found" in tool._execute(pattern="hidden_function")
    assert "__pycache__" not in tool._execute(pattern="cached")


def test_search_finds_in_subdirectory_and_shows_count(workspace: Path) -> None:
    result = CodeSearchTool(workspace=str(workspace))._execute(pattern="class Foo")
    assert "module.py" in result
    result = CodeSearchTool(workspace=str(workspace))._execute(pattern="def")
    assert "Found" in result
    assert "match" in result


def test_invalid_glob_pattern_returns_error(workspace: Path) -> None:
    tool = CodeSearchTool(workspace=str(workspace))
    result = tool._execute(pattern="def", glob="")
    assert result.startswith("ERROR: Invalid glob pattern")


def test_unreadable_entry_skipped(workspace: Path) -> None:
    # A directory whose name matches the glob must be skipped, not crash.
    (workspace / "trap.py").mkdir()
    tool = CodeSearchTool(workspace=str(workspace))
    result = tool._execute(pattern="def main")
    assert "main.py:1" in result
