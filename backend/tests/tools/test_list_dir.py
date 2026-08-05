"""Tests for ListDirTool."""

import json
from pathlib import Path

import pytest

from backend.tools.list_dir import ListDirTool


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("class Main: pass")
    (tmp_path / "src" / "utils.py").write_text("def helper(): pass")
    (tmp_path / "tests").mkdir()
    (tmp_path / "README.md").write_text("# hello")
    return tmp_path


@pytest.fixture
def tool(workspace: Path) -> ListDirTool:
    return ListDirTool(workspace=str(workspace))


def test_list_dir_lists_dirs_and_files(tool: ListDirTool) -> None:
    entries = json.loads(tool._execute(path="."))
    names = [e["name"] for e in entries]
    assert "src" in names
    assert "tests" in names
    assert "README.md" in names
    # not recursive
    assert "main.py" not in names


def test_list_dir_entry_has_required_fields(tool: ListDirTool) -> None:
    for entry in json.loads(tool._execute(path=".")):
        assert {"name", "type", "path"} <= entry.keys()
        assert entry["type"] in ("file", "dir")


def test_list_dir_subdir_contents(tool: ListDirTool) -> None:
    entries = json.loads(tool._execute(path="src"))
    names = [e["name"] for e in entries]
    assert "main.py" in names
    assert "utils.py" in names


def test_list_dir_path_relative_to_workspace(tool: ListDirTool) -> None:
    entries = json.loads(tool._execute(path="."))
    src = next(e for e in entries if e["name"] == "src")
    assert src["path"] == "src"


def test_list_dir_nonexistent_returns_empty(tool: ListDirTool) -> None:
    assert json.loads(tool._execute(path="nonexistent")) == []


def test_list_dir_path_is_file_returns_error(tool: ListDirTool) -> None:
    assert tool._execute(path="README.md").startswith("ERROR")
