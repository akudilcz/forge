"""Tests for MultiFileWriteTool."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.tools.multi_file_write import MultiFileWriteTool, _FileEntry


def test_write_single_file(tmp_path: Path) -> None:
    tool = MultiFileWriteTool(workspace=str(tmp_path))
    result = tool._execute(files=[_FileEntry(path="foo.py", content="print('hello')")])
    assert "OK" in result
    assert "foo.py" in result
    assert (tmp_path / "foo.py").read_text() == "print('hello')"


def test_write_multiple_files_with_subdirectories(tmp_path: Path) -> None:
    tool = MultiFileWriteTool(workspace=str(tmp_path))
    files = [
        _FileEntry(path="a.py", content="# a"),
        _FileEntry(path="sub/nested/b.py", content="# b"),
    ]
    result = tool._execute(files=files)
    assert result.count("OK") == 2
    assert (tmp_path / "a.py").exists()
    assert (tmp_path / "sub" / "nested" / "b.py").exists()


def test_write_line_count_and_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "existing.txt"
    target.write_text("old content")
    tool = MultiFileWriteTool(workspace=str(tmp_path))
    result = tool._execute(files=[_FileEntry(path="existing.txt", content="line1\nline2\nline3")])
    assert "3 lines" in result
    assert target.read_text() == "line1\nline2\nline3"


def test_write_empty_files_list_returns_error(tmp_path: Path) -> None:
    tool = MultiFileWriteTool(workspace=str(tmp_path))
    result = tool._execute(files=[])
    assert "ERROR" in result


def test_write_with_dict_entries(tmp_path: Path) -> None:
    tool = MultiFileWriteTool(workspace=str(tmp_path))
    result = tool._execute(files=[{"path": "dict_file.py", "content": "# dict"}])
    assert "OK" in result
    assert (tmp_path / "dict_file.py").exists()


@pytest.mark.parametrize("entry", [
    {"path": "", "content": "# empty path"},
])
def test_write_empty_path_raises(tmp_path: Path, entry: dict[str, str]) -> None:
    tool = MultiFileWriteTool(workspace=str(tmp_path))
    with pytest.raises(ValueError, match="empty path"):
        tool._execute(files=[entry])


def test_write_mixed_dict_and_model_entries(tmp_path: Path) -> None:
    tool = MultiFileWriteTool(workspace=str(tmp_path))
    files = [
        {"path": "from_dict.py", "content": "# dict"},
        _FileEntry(path="from_model.py", content="# model"),
    ]
    result = tool._execute(files=files)
    assert result.count("OK") == 2


# ── Required keys (no silent .get defaults) ──────────────────────────────────


def test_missing_path_key_raises_and_writes_nothing(tmp_path: Path) -> None:
    tool = MultiFileWriteTool(workspace=str(tmp_path))
    with pytest.raises(ValueError, match="path"):
        tool._execute(files=[{"content": "# no path"}])
    assert list(tmp_path.iterdir()) == []


def test_missing_content_key_raises_and_writes_nothing(tmp_path: Path) -> None:
    """A missing 'content' key must raise — not silently truncate the file."""
    existing = tmp_path / "keep.py"
    existing.write_text("x = 1\n")
    tool = MultiFileWriteTool(workspace=str(tmp_path))
    with pytest.raises(ValueError, match="content"):
        tool._execute(files=[{"path": "keep.py"}])
    assert existing.read_text() == "x = 1\n"


def test_missing_key_rejects_whole_batch(tmp_path: Path) -> None:
    """One bad entry blocks the entire batch — the valid entry is not written."""
    tool = MultiFileWriteTool(workspace=str(tmp_path))
    with pytest.raises(ValueError, match="files\\[1\\]"):
        tool._execute(files=[
            {"path": "good.py", "content": "x = 1\n"},
            {"content": "# missing path"},
        ])
    assert not (tmp_path / "good.py").exists()


# ── Path scope enforcement ────────────────────────────────────────────────────


def test_path_escape_raises_and_writes_nothing(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    tool = MultiFileWriteTool(workspace=str(workspace))
    with pytest.raises(ValueError, match="outside the workspace"):
        tool._execute(files=[
            {"path": "ok.py", "content": "x = 1\n"},
            {"path": "../evil.py", "content": "x = 1\n"},
        ])
    assert not (workspace / "ok.py").exists()
    assert not (tmp_path / "evil.py").exists()


def test_absolute_path_escape_raises(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    tool = MultiFileWriteTool(workspace=str(workspace))
    outside = tmp_path / "outside.py"
    with pytest.raises(ValueError, match="outside the workspace"):
        tool._execute(files=[{"path": str(outside), "content": "x = 1\n"}])
    assert not outside.exists()


# ── Python syntax gate (batch-atomic) ────────────────────────────────────────


def test_invalid_python_rejects_whole_batch_atomically(tmp_path: Path) -> None:
    tool = MultiFileWriteTool(workspace=str(tmp_path))
    result = tool._execute(files=[
        {"path": "good.py", "content": "x = 1\n"},
        {"path": "bad.py", "content": "def broken(\n"},
    ])
    assert "REJECTED" in result
    assert "bad.py" in result
    assert not (tmp_path / "good.py").exists()
    assert not (tmp_path / "bad.py").exists()


def test_invalid_python_does_not_clobber_existing_file(tmp_path: Path) -> None:
    existing = tmp_path / "mod.py"
    existing.write_text("x = 1\n")
    tool = MultiFileWriteTool(workspace=str(tmp_path))
    result = tool._execute(files=[{"path": "mod.py", "content": "def broken(\n"}])
    assert "REJECTED" in result
    assert existing.read_text() == "x = 1\n"


def test_syntax_error_message_includes_line(tmp_path: Path) -> None:
    tool = MultiFileWriteTool(workspace=str(tmp_path))
    result = tool._execute(
        files=[{"path": "bad.py", "content": "x = 1\ndef broken(\n"}],
    )
    assert "REJECTED" in result
    assert "line" in result.lower()


def test_non_python_files_skip_syntax_check(tmp_path: Path) -> None:
    tool = MultiFileWriteTool(workspace=str(tmp_path))
    result = tool._execute(
        files=[{"path": "notes.txt", "content": "def broken(\n"}],
    )
    assert "OK" in result
    assert (tmp_path / "notes.txt").read_text() == "def broken(\n"


def test_valid_python_batch_writes_all(tmp_path: Path) -> None:
    tool = MultiFileWriteTool(workspace=str(tmp_path))
    result = tool._execute(files=[
        {"path": "src/mod.py", "content": "def f():\n    return 1\n"},
        {"path": "tests/test_mod.py", "content": "from src.mod import f\n"},
    ])
    assert result.count("OK") == 2
    assert (tmp_path / "src" / "mod.py").exists()
    assert (tmp_path / "tests" / "test_mod.py").exists()

