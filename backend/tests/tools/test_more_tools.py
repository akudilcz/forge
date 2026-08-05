"""Tests for file_patch, git_ops, shell_exec, send_message, run_tests."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.tools.base import ToolPermissionError
from backend.tools.file_patch import FilePatchTool
from backend.tools.git_ops import GitOpsTool
from backend.tools.run_tests import RunTestsTool
from backend.tools.send_message import SendMessageTool
from backend.tools.shell_exec import ShellExecTool

# ── FilePatchTool ─────────────────────────────────────────────────────────────

def test_file_patch_success(tmp_path: Path) -> None:
    f = tmp_path / "hello.py"
    f.write_text("def old(): pass\n")
    tool = FilePatchTool(workspace=str(tmp_path))
    result = tool._execute(path="hello.py", old_text="def old(): pass", new_text="def new(): pass")
    assert "OK" in result
    assert f.read_text() == "def new(): pass\n"


@pytest.mark.parametrize(("path", "content", "old_text", "expected_msg"), [
    ("missing.py", None, "x", "missing.py"),
    ("code.py", "hello world", "NOT PRESENT", "not found"),
    ("code.py", "x = 1\nx = 1\n", "x = 1", "2 occurrences"),
])
def test_file_patch_errors(
    tmp_path: Path, path: str, content: str | None, old_text: str, expected_msg: str
) -> None:
    if content is not None:
        (tmp_path / path).write_text(content)
    tool = FilePatchTool(workspace=str(tmp_path))
    result = tool._execute(path=path, old_text=old_text, new_text="y")
    assert "ERROR" in result
    assert expected_msg in result


# ── GitOpsTool ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("subcommand", ["push", "reset"])
def test_git_ops_disallowed_subcommand(tmp_path: Path, subcommand: str) -> None:
    with pytest.raises(ToolPermissionError):
        GitOpsTool(workspace=str(tmp_path))._execute(subcommand=subcommand)


def test_git_ops_commit_prefix_added_not_doubled(tmp_path: Path) -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        tool = GitOpsTool(workspace=str(tmp_path), commit_prefix="[forge]")

        tool._execute(subcommand="commit", args='-m "initial commit"')
        assert "[forge]" in mock_run.call_args[0][0]

        tool._execute(subcommand="commit", args='-m "[forge] already prefixed"')
        assert mock_run.call_args[0][0].count("[forge]") == 1


def test_git_ops_timeout(tmp_path: Path) -> None:
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git status", 30)):
        result = GitOpsTool(workspace=str(tmp_path))._execute(subcommand="status")
        assert "timed out" in result


def test_git_ops_nonzero_exit(tmp_path: Path) -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error msg")
        assert "GIT ERROR" in GitOpsTool(workspace=str(tmp_path))._execute(subcommand="status")


def test_git_ops_empty_output_returns_ok(tmp_path: Path) -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        assert GitOpsTool(workspace=str(tmp_path))._execute(subcommand="status") == "OK"


# ── ShellExecTool ─────────────────────────────────────────────────────────────

def test_shell_exec_allowed_command(tmp_path: Path) -> None:
    tool = ShellExecTool(workspace=str(tmp_path), allowlist=["echo *"])
    assert "hello" in tool._execute(command="echo hello")


def test_shell_exec_blocked_command(tmp_path: Path) -> None:
    with pytest.raises(ToolPermissionError):
        ShellExecTool(workspace=str(tmp_path), allowlist=["echo *"])._execute(command="rm -rf /")


def test_shell_exec_nonzero_exit(tmp_path: Path) -> None:
    tool = ShellExecTool(workspace=str(tmp_path), allowlist=["false*", "false"])
    assert "EXIT" in tool._execute(command="false")


def test_shell_exec_timeout(tmp_path: Path) -> None:
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("sleep", 1)):
        result = ShellExecTool(workspace=str(tmp_path), allowlist=["sleep*"])._execute(command="sleep 100", timeout=1)
        assert "timed out" in result


def test_shell_exec_timeout_capped_at_300(tmp_path: Path) -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        ShellExecTool(workspace=str(tmp_path), allowlist=["echo *"])._execute(command="echo hi", timeout=9999)
        assert mock_run.call_args[1]["timeout"] <= 300


def test_shell_exec_empty_output_returns_ok(tmp_path: Path) -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        assert ShellExecTool(workspace=str(tmp_path), allowlist=["true*"])._execute(command="true") == "OK (no output)"


# ── SendMessageTool ───────────────────────────────────────────────────────────

def test_send_message_no_bus() -> None:
    assert "ERROR" in SendMessageTool()._execute(to_agent="agent1", subject="hi", body="hello")


def test_send_message_with_bus() -> None:
    from unittest.mock import AsyncMock
    bus = MagicMock()
    bus.emit = AsyncMock()
    tool = SendMessageTool()
    tool.bind_bus(bus)
    result = tool._execute(to_agent="agent1", subject="hi", body="hello")
    assert "OK" in result
    assert "agent1" in result
    assert "normal" in result


def test_send_message_blocker_priority() -> None:
    from unittest.mock import AsyncMock
    bus = MagicMock()
    bus.emit = AsyncMock()
    tool = SendMessageTool()
    tool.bind_bus(bus)
    result = tool._execute(to_agent="agent2", subject="blocked", body="reason", priority="blocker")
    assert "OK" in result
    assert "blocker" in result


# ── RunTestsTool ──────────────────────────────────────────────────────────────

def test_run_tests_passing(tmp_path: Path) -> None:
    import json
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="5 passed in 0.10s", stderr="")
        data = json.loads(RunTestsTool(workspace=str(tmp_path))._execute(path="tests/"))
        assert data["passed"] is True
        assert data["total"] == 5


def test_run_tests_failing(tmp_path: Path) -> None:
    import json
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="1 failed in 0.05s", stderr="")
        data = json.loads(RunTestsTool(workspace=str(tmp_path))._execute(path="tests/"))
        assert data["passed"] is False
        assert data["failures"] == 1


def test_run_tests_timeout(tmp_path: Path) -> None:
    import json
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("pytest", 180)):
        data = json.loads(RunTestsTool(workspace=str(tmp_path))._execute())
        assert data["passed"] is False
        assert "timed out" in data["output"]


def test_run_tests_custom_flags(tmp_path: Path) -> None:
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        RunTestsTool(workspace=str(tmp_path))._execute(path="tests/", flags="-v --tb=short")
        cmd = mock_run.call_args[0][0]
        assert "-v" in cmd
        assert "--tb=short" in cmd
