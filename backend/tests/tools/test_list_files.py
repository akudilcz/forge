"""Tests for ListFilesTool."""

import json
from pathlib import Path

import pytest

from backend.tools.list_files import ListFilesTool


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("class Main: pass")
    (tmp_path / "src" / "utils.py").write_text("def helper(): pass")
    (tmp_path / "src" / "config.json").write_text("{}")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_main.py").write_text("def test_main(): pass")
    # Add noisy directories that should be excluded
    venv = tmp_path / ".venv" / "lib" / "site-packages" / "numpy"
    venv.mkdir(parents=True)
    (venv / "__init__.py").write_text("")
    pycache = tmp_path / "src" / "__pycache__"
    pycache.mkdir()
    (pycache / "main.cpython-312.pyc").write_text("")
    return tmp_path


@pytest.fixture
def tool(workspace: Path) -> ListFilesTool:
    return ListFilesTool(workspace=str(workspace))


def test_list_files_returns_matching_py_files(tool: ListFilesTool) -> None:
    files = json.loads(tool._execute(path="src", pattern="*.py"))
    assert len(files) == 2
    assert all(f.endswith(".py") for f in files)
    assert not any(f.endswith(".json") for f in files)


def test_list_files_relative_paths_and_default_pattern(tool: ListFilesTool) -> None:
    files = json.loads(tool._execute(path="src"))
    assert all(f.startswith("src/") for f in files)
    assert len(files) == 2  # default *.py


def test_list_files_finds_test_files(tool: ListFilesTool) -> None:
    files = json.loads(tool._execute(path="tests", pattern="test_*.py"))
    assert len(files) == 1
    assert all("test_" in Path(f).name for f in files)


def test_list_files_nonexistent_directory_returns_empty(tool: ListFilesTool) -> None:
    assert json.loads(tool._execute(path="nonexistent", pattern="*.py")) == []


def test_list_files_path_is_file_returns_error(tool: ListFilesTool) -> None:
    assert tool._execute(path="src/main.py", pattern="*.py").startswith("ERROR")


def test_list_files_pattern_no_match_returns_empty(tool: ListFilesTool) -> None:
    assert json.loads(tool._execute(path="src", pattern="*.ts")) == []


def test_list_files_excludes_venv(tool: ListFilesTool) -> None:
    """Files inside .venv/ should never appear in results."""
    files = json.loads(tool._execute(path=".", pattern="*.py"))
    assert not any(".venv" in f for f in files)


def test_list_files_excludes_pycache(tool: ListFilesTool) -> None:
    """Files inside __pycache__/ should never appear in results."""
    files = json.loads(tool._execute(path=".", pattern="*"))
    assert not any("__pycache__" in f for f in files)


def test_list_files_still_finds_src_files_with_exclusions(tool: ListFilesTool) -> None:
    """Exclusions should not affect normal src/ and tests/ files."""
    files = json.loads(tool._execute(path=".", pattern="*.py"))
    names = [Path(f).name for f in files]
    assert "main.py" in names
    assert "utils.py" in names
    assert "test_main.py" in names
