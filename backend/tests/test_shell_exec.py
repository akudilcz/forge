"""Behavioural tests for ShellExecTool error paths (backend/tools/shell_exec.py)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.tools.base import ToolPermissionError
from backend.tools.shell_exec import ShellExecTool, _format_test_error, _read_log_tail


def _tool(tmp_path: Path) -> ShellExecTool:
    return ShellExecTool(workspace=str(tmp_path), allowlist=["echo*", "false*"])


def test_allowlisted_command_runs(tmp_path: Path) -> None:
    assert _tool(tmp_path)._execute(command="echo hi").strip() == "hi"


def test_disallowed_command_raises(tmp_path: Path) -> None:
    with pytest.raises(ToolPermissionError):
        _tool(tmp_path)._execute(command="rm -rf /")


def test_nonzero_exit_reported(tmp_path: Path) -> None:
    assert _tool(tmp_path)._execute(command="false").startswith("EXIT 1:")


def test_unexpected_subprocess_failure_wrapped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> None:
        raise OSError("spawn failed")

    monkeypatch.setattr(subprocess, "run", _boom)
    assert _tool(tmp_path)._execute(command="echo hi") == "ERROR: spawn failed"


# ── log helpers ──────────────────────────────────────────────────────────────


def test_read_log_tail_missing_file(tmp_path: Path) -> None:
    assert _read_log_tail(tmp_path / "nope.log", 100) is None


def test_read_log_tail_truncates(tmp_path: Path) -> None:
    log = tmp_path / "test.log"
    log.write_text("abcdefghij")
    assert _read_log_tail(log, 4) == "ghij"
    assert _read_log_tail(log, 100) == "abcdefghij"


def test_read_log_tail_unreadable_returns_none() -> None:
    stub = SimpleNamespace(
        exists=lambda: True,
        read_text=lambda **kwargs: (_ for _ in ()).throw(OSError("denied")),
    )
    assert _read_log_tail(stub, 100) is None


def test_format_test_error_variants(tmp_path: Path) -> None:
    missing = tmp_path / "nope.log"
    assert _format_test_error(missing) == "(no test.log)"

    empty = tmp_path / "empty.log"
    empty.write_text("\n\n")
    assert _format_test_error(empty) == "(empty test.log)"

    log = tmp_path / "test.log"
    log.write_text("\n".join(f"line{i}" for i in range(10)))
    tail = _format_test_error(log)
    assert "line9" in tail
    assert "line4" not in tail

    stub = SimpleNamespace(
        exists=lambda: True,
        read_text=lambda **kwargs: (_ for _ in ()).throw(OSError("denied")),
    )
    assert _format_test_error(stub) == "(unreadable test.log)"
