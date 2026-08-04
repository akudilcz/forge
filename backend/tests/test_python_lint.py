"""Tests for backend.tools.python_lint — syntax and style checking."""

from pathlib import Path

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
