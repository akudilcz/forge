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
def test_write_empty_path_returns_error(tmp_path: Path, entry: dict[str, str]) -> None:
    tool = MultiFileWriteTool(workspace=str(tmp_path))
    result = tool._execute(files=[entry])
    assert "ERROR" in result
    assert "empty path" in result


def test_write_mixed_dict_and_model_entries(tmp_path: Path) -> None:
    tool = MultiFileWriteTool(workspace=str(tmp_path))
    files = [
        {"path": "from_dict.py", "content": "# dict"},
        _FileEntry(path="from_model.py", content="# model"),
    ]
    result = tool._execute(files=files)
    assert result.count("OK") == 2


# ── Path scope enforcement ────────────────────────────────────────────────────


