"""Tests for the read_docs tool."""

from pathlib import Path

from backend.tools.read_docs import ReadDocsTool


def test_list_docs(tmp_path: Path) -> None:
    """Should list available docs when called with no filename."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "08-Design.md").write_text("# Design\nContent here")
    (docs / "07-LLR.md").write_text("# LLR\nRequirements")

    tool = ReadDocsTool(str(tmp_path))
    result = tool._execute()
    assert "07-LLR.md" in result
    assert "08-Design.md" in result
    assert "Available documentation" in result


def test_read_specific_doc(tmp_path: Path) -> None:
    """Should return file content when called with a filename."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "08-Design.md").write_text("# Design\nDetailed design spec")

    tool = ReadDocsTool(str(tmp_path))
    result = tool._execute(filename="08-Design.md")
    assert "Detailed design spec" in result


def test_read_missing_doc(tmp_path: Path) -> None:
    """Should return error with available list when file not found."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "07-LLR.md").write_text("content")

    tool = ReadDocsTool(str(tmp_path))
    result = tool._execute(filename="99-Missing.md")
    assert "ERROR" in result
    assert "07-LLR.md" in result


def test_no_docs_dir(tmp_path: Path) -> None:
    """Should handle missing docs/ directory gracefully."""
    tool = ReadDocsTool(str(tmp_path))
    result = tool._execute()
    assert "No docs/ directory" in result
