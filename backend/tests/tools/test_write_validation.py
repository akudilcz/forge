"""Tests for the shared write-tool validation helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.tools.write_validation import check_syntax, resolve_in_workspace

# ── check_syntax ─────────────────────────────────────────────────────────────


def test_check_syntax_valid_code_returns_empty() -> None:
    assert check_syntax("def f():\n    return 1\n", "ok.py") == ""


def test_check_syntax_invalid_code_reports_line() -> None:
    error = check_syntax("x = 1\ndef broken(\n", "bad.py")
    assert error != ""
    assert "line" in error.lower()


# ── resolve_in_workspace ─────────────────────────────────────────────────────


def test_resolve_relative_path_inside_workspace(tmp_path: Path) -> None:
    target = resolve_in_workspace(str(tmp_path), "src/mod.py")
    assert target == (tmp_path / "src" / "mod.py").resolve()


def test_resolve_empty_path_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="empty path"):
        resolve_in_workspace(str(tmp_path), "")


def test_resolve_parent_escape_raises(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(ValueError, match="outside the workspace"):
        resolve_in_workspace(str(workspace), "../evil.py")


def test_resolve_nested_dotdot_escape_raises(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(ValueError, match="outside the workspace"):
        resolve_in_workspace(str(workspace), "src/../../evil.py")


def test_resolve_absolute_path_outside_raises(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    with pytest.raises(ValueError, match="outside the workspace"):
        resolve_in_workspace(str(workspace), str(tmp_path / "evil.py"))


def test_resolve_dotdot_that_stays_inside_is_allowed(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    target = resolve_in_workspace(str(tmp_path), "src/../ok.py")
    assert target == (tmp_path / "ok.py").resolve()
