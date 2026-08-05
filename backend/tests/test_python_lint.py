"""Tests for backend.tools.python_lint — syntax and style checking."""

from pathlib import Path

import pytest

from backend.tools.python_lint import PythonLintTool


def test_clean_file(tmp_path: Path) -> None:
    """Well-formed Python file returns OK."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "good.py").write_text(
        'def greet(name: str) -> str:\n'
        '    """Say hello."""\n'
        '    return f"Hello, {name}"\n'
    )
    tool = PythonLintTool(str(tmp_path))
    result = tool._execute("src/good.py")
    assert result.startswith("OK")


def test_syntax_error(tmp_path: Path) -> None:
    """Syntax errors are detected."""
    (tmp_path / "bad.py").write_text("def foo(\n")
    tool = PythonLintTool(str(tmp_path))
    result = tool._execute("bad.py")
    assert "SYNTAX" in result


def test_bare_except(tmp_path: Path) -> None:
    """Bare except clause is flagged."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "exc.py").write_text(
        "def risky():\n"
        '    """Do something."""\n'
        "    try:\n"
        "        pass\n"
        "    except:\n"
        "        pass\n"
    )
    tool = PythonLintTool(str(tmp_path))
    result = tool._execute("src/exc.py")
    assert "bare" in result.lower()


def test_star_import(tmp_path: Path) -> None:
    """Star imports are flagged."""
    (tmp_path / "star.py").write_text("from os import *\n")
    tool = PythonLintTool(str(tmp_path))
    result = tool._execute("star.py")
    assert "import *" in result


def test_missing_docstring(tmp_path: Path) -> None:
    """Top-level function without docstring is flagged."""
    (tmp_path / "nodoc.py").write_text("def foo():\n    return 1\n")
    tool = PythonLintTool(str(tmp_path))
    result = tool._execute("nodoc.py")
    assert "docstring" in result.lower()


def test_file_not_found(tmp_path: Path) -> None:
    """Missing file returns error."""
    tool = PythonLintTool(str(tmp_path))
    result = tool._execute("nope.py")
    assert "ERROR" in result


def test_check_all(tmp_path: Path) -> None:
    """path='*' checks all files in src/ and tests/."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.py").write_text('def a():\n    """A."""\n    pass\n')
    (src / "b.py").write_text('def b():\n    """B."""\n    pass\n')
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_a.py").write_text('def test_a():\n    """T."""\n    assert True\n')

    tool = PythonLintTool(str(tmp_path))
    result = tool._execute("*")
    assert "Checked 3 file(s)" in result
    assert "clean" in result


def test_not_python_file(tmp_path: Path) -> None:
    """Non-.py file returns error."""
    (tmp_path / "readme.md").write_text("# Hi\n")
    tool = PythonLintTool(str(tmp_path))
    result = tool._execute("readme.md")
    assert "ERROR" in result


# ── check-all mode ────────────────────────────────────────────────────────────


def test_check_all_no_python_files(tmp_path: Path) -> None:
    tool = PythonLintTool(workspace=str(tmp_path))
    assert tool._execute(path="*") == "No Python files found in src/ or tests/."


def test_check_all_reports_clean_and_issue_counts(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "clean.py").write_text('"""Doc."""\n')
    (src / "bad.py").write_text("def broken(\n")
    tool = PythonLintTool(workspace=str(tmp_path))
    result = tool._execute(path="*")
    assert "Checked 2 file(s): 1 clean, 1 with issues" in result
    assert "SYNTAX" in result


def test_check_all_only_tests_dir(tmp_path: Path) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_ok.py").write_text('"""Doc."""\n')
    tool = PythonLintTool(workspace=str(tmp_path))
    assert tool._execute(path="*").startswith("Checked 1 file(s): 1 clean")


def test_not_a_python_file(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_text("hello")
    tool = PythonLintTool(workspace=str(tmp_path))
    assert tool._execute(path="notes.txt") == "ERROR: not a Python file: notes.txt"


# ── AST helpers ───────────────────────────────────────────────────────────────


def test_check_ast_unreadable_file_returns_empty(tmp_path: Path) -> None:
    from backend.tools.python_lint import _check_ast

    assert _check_ast(tmp_path / "missing.py") == []


def test_explicit_from_import_not_flagged(tmp_path: Path) -> None:
    (tmp_path / "imports.py").write_text(
        '"""Doc."""\nfrom os import path\nprint(path)\n'
    )
    tool = PythonLintTool(workspace=str(tmp_path))
    result = tool._execute(path="imports.py")
    assert "import *" not in result


def test_has_docstring_node_without_body() -> None:
    import ast

    from backend.tools.python_lint import _has_docstring

    assert _has_docstring(ast.Pass()) is False


# ── ruff integration (mocked) ─────────────────────────────────────────────────


def test_find_ruff_falls_back_to_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil
    import sys

    from backend.tools.python_lint import _find_ruff

    fake_bin = tmp_path / "venv" / "bin"
    fake_bin.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(fake_bin / "python"))
    assert _find_ruff() == shutil.which("ruff")


def test_check_ruff_skipped_when_unavailable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.tools.python_lint as pl

    monkeypatch.setattr(pl, "_find_ruff", lambda: None)
    assert pl._check_ruff(tmp_path / "any.py") == []


def test_check_ruff_parses_issue_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    from types import SimpleNamespace

    import backend.tools.python_lint as pl

    monkeypatch.setattr(pl, "_find_ruff", lambda: "/fake/ruff")
    fake = SimpleNamespace(
        returncode=1,
        stdout=(
            "file.py:3:1: F401 'os' imported but unused\n"
            "orphan-line-without-colons\n"
            "short:bit\n"
            "Found 1 error.\n"
        ),
    )
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: fake)
    issues = pl._check_ruff(tmp_path / "file.py")
    assert issues == ["line 3:1: F401 'os' imported but unused"]


def test_check_ruff_clean_exit_returns_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    from types import SimpleNamespace

    import backend.tools.python_lint as pl

    monkeypatch.setattr(pl, "_find_ruff", lambda: "/fake/ruff")
    monkeypatch.setattr(
        subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=""),
    )
    assert pl._check_ruff(tmp_path / "file.py") == []


def test_check_ruff_binary_vanished(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import backend.tools.python_lint as pl

    monkeypatch.setattr(pl, "_find_ruff", lambda: "/nonexistent/ruff-xyz")
    assert pl._check_ruff(tmp_path / "file.py") == []


def test_check_ruff_unexpected_failure_swallowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    import backend.tools.python_lint as pl

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("ruff crashed")

    monkeypatch.setattr(pl, "_find_ruff", lambda: "/fake/ruff")
    monkeypatch.setattr(subprocess, "run", _boom)
    assert pl._check_ruff(tmp_path / "file.py") == []
