"""Tests for backend.tools.file_read — file reading with line-range support."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.tools.file_read import FileReadTool


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    f = tmp_path / "sample.py"
    f.write_text("line1\nline2\nline3\nline4\nline5\n")
    return tmp_path


def _tool(workspace: Path) -> FileReadTool:
    return FileReadTool(workspace=str(workspace))


def test_read_full_file(workspace: Path) -> None:
    """Default read returns full content with line numbers."""
    result = _tool(workspace)._execute(path="sample.py")
    assert "   1 | line1" in result
    assert "   5 | line5" in result


def test_read_line_range(workspace: Path) -> None:
    """start_line/end_line returns only those lines with numbers."""
    result = _tool(workspace)._execute(path="sample.py", start_line=2, end_line=4)
    assert "   2 | line2" in result
    assert "   3 | line3" in result
    assert "   4 | line4" in result
    assert "line1" not in result
    assert "line5" not in result


def test_read_start_line_only(workspace: Path) -> None:
    """start_line without end_line reads to end of file."""
    result = _tool(workspace)._execute(path="sample.py", start_line=4)
    assert "   4 | line4" in result
    assert "   5 | line5" in result
    assert "line1" not in result


def test_read_end_line_only(workspace: Path) -> None:
    """end_line without start_line reads from top."""
    result = _tool(workspace)._execute(path="sample.py", end_line=2)
    assert "   1 | line1" in result
    assert "   2 | line2" in result
    assert "line3" not in result


def test_read_file_not_found(workspace: Path) -> None:
    """Missing file returns error."""
    result = _tool(workspace)._execute(path="nope.py")
    assert result.startswith("ERROR")
