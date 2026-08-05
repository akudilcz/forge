"""Tests for batch_patch tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.tools.batch_patch import BatchPatchTool, _PatchEntry


def _tool(tmp_path: Path) -> BatchPatchTool:
    return BatchPatchTool(workspace=str(tmp_path))


def test_apply_multiple_patches(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        '@traces("HLR-0003", case="CASE_HLR-0003")\n'
        "def test_a(): pass\n"
        '@traces("HLR-0003", case="CASE_HLR-0003")\n'
        "def test_b(): pass\n"
    )
    tool = _tool(tmp_path)
    # Two patches — one per decorator (unique old_text via function name context)
    result = tool._execute(
        path="a.py",
        patches=[
            _PatchEntry(
                old_text='@traces("HLR-0003", case="CASE_HLR-0003")\ndef test_a',
                new_text='@traces("LLR-0003", case="CASE_LLR-0003")\ndef test_a',
            ),
            _PatchEntry(
                old_text='@traces("HLR-0003", case="CASE_HLR-0003")\ndef test_b',
                new_text='@traces("LLR-0003", case="CASE_LLR-0003")\ndef test_b',
            ),
        ],
    )
    assert "OK" in result
    assert "2 patch" in result
    content = (tmp_path / "a.py").read_text()
    assert "HLR-0003" not in content
    assert content.count("LLR-0003") == 4  # 2 in ID + 2 in CASE


def test_single_patch(tmp_path: Path) -> None:
    (tmp_path / "b.py").write_text("x = 1\ny = 2\n")
    tool = _tool(tmp_path)
    result = tool._execute(
        path="b.py",
        patches=[_PatchEntry(old_text="x = 1", new_text="x = 42")],
    )
    assert "OK" in result
    assert (tmp_path / "b.py").read_text() == "x = 42\ny = 2\n"


def test_old_text_not_found(tmp_path: Path) -> None:
    (tmp_path / "c.py").write_text("hello\n")
    tool = _tool(tmp_path)
    result = tool._execute(
        path="c.py",
        patches=[_PatchEntry(old_text="nonexistent", new_text="x")],
    )
    assert "ERROR" in result
    assert "not found" in result


def test_ambiguous_old_text(tmp_path: Path) -> None:
    (tmp_path / "d.py").write_text("dup\ndup\n")
    tool = _tool(tmp_path)
    result = tool._execute(
        path="d.py",
        patches=[_PatchEntry(old_text="dup", new_text="unique")],
    )
    assert "ERROR" in result
    assert "2 times" in result


def test_syntax_validation(tmp_path: Path) -> None:
    (tmp_path / "e.py").write_text("def foo(): pass\n")
    tool = _tool(tmp_path)
    result = tool._execute(
        path="e.py",
        patches=[_PatchEntry(old_text="def foo(): pass", new_text="def foo(")],
    )
    assert "ERROR" in result
    assert "invalid Python" in result
    # Original should be unchanged
    assert (tmp_path / "e.py").read_text() == "def foo(): pass\n"


def test_file_not_found(tmp_path: Path) -> None:
    tool = _tool(tmp_path)
    result = tool._execute(
        path="nope.py",
        patches=[_PatchEntry(old_text="x", new_text="y")],
    )
    assert "ERROR" in result
    assert "not found" in result


def test_empty_patches(tmp_path: Path) -> None:
    (tmp_path / "f.py").write_text("x\n")
    tool = _tool(tmp_path)
    result = tool._execute(path="f.py", patches=[])
    assert "ERROR" in result
    assert "No patches" in result


def test_non_python_file(tmp_path: Path) -> None:
    (tmp_path / "g.txt").write_text("old line\n")
    tool = _tool(tmp_path)
    result = tool._execute(
        path="g.txt",
        patches=[_PatchEntry(old_text="old line", new_text="new line")],
    )
    assert "OK" in result
    assert (tmp_path / "g.txt").read_text() == "new line\n"


def test_unreadable_target_reports_read_error(tmp_path: Path) -> None:
    (tmp_path / "dir.py").mkdir()
    tool = BatchPatchTool(workspace=str(tmp_path))
    result = tool._execute(
        path="dir.py", patches=[_PatchEntry(old_text="a", new_text="b")],
    )
    assert result.startswith("ERROR reading dir.py:")


def test_write_failure_reports_write_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "f.txt").write_text("hello world")
    tool = BatchPatchTool(workspace=str(tmp_path))

    def _boom(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", _boom)
    result = tool._execute(
        path="f.txt", patches=[_PatchEntry(old_text="hello", new_text="bye")],
    )
    assert result.startswith("ERROR writing f.txt:")
