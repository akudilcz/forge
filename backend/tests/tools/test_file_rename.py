"""Tests for backend.tools.file_rename — rename/move files in workspace."""

from __future__ import annotations

from pathlib import Path

from backend.tools.file_rename import FileRenameTool


def test_rename_file(tmp_path: Path) -> None:
    """Basic rename within the same directory."""
    (tmp_path / "old.py").write_text("pass\n")
    tool = FileRenameTool(str(tmp_path))

    result = tool._execute("old.py", "new.py")

    assert "OK" in result
    assert not (tmp_path / "old.py").exists()
    assert (tmp_path / "new.py").read_text() == "pass\n"


def test_rename_creates_parent_dirs(tmp_path: Path) -> None:
    """Destination parent directories are created automatically."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("code\n")
    tool = FileRenameTool(str(tmp_path))

    result = tool._execute("src/foo.py", "src/module/foo.py")

    assert "OK" in result
    assert (tmp_path / "src" / "module" / "foo.py").read_text() == "code\n"


def test_rename_source_not_found(tmp_path: Path) -> None:
    """Error when source file doesn't exist."""
    tool = FileRenameTool(str(tmp_path))
    result = tool._execute("nope.py", "dest.py")
    assert "ERROR" in result
    assert "not found" in result


def test_rename_refuses_overwrite(tmp_path: Path) -> None:
    """Refuses to overwrite an existing destination file."""
    (tmp_path / "a.py").write_text("original\n")
    (tmp_path / "b.py").write_text("existing\n")
    tool = FileRenameTool(str(tmp_path))

    result = tool._execute("a.py", "b.py")

    assert "ERROR" in result
    assert "already exists" in result
    # Both files should be untouched
    assert (tmp_path / "a.py").read_text() == "original\n"
    assert (tmp_path / "b.py").read_text() == "existing\n"


def test_rename_directory_rejected(tmp_path: Path) -> None:
    """Refuses to rename a directory (files only)."""
    (tmp_path / "subdir").mkdir()
    tool = FileRenameTool(str(tmp_path))

    result = tool._execute("subdir", "newdir")

    assert "ERROR" in result
    assert "not a file" in result
