"""Tests for RunTestsTool and pytest output parsing."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from backend.tools.run_tests import RunTestsTool, _parse_output

# ── _parse_output unit tests ──────────────────────────────────────────────────

@pytest.mark.parametrize(("output", "returncode", "expected"), [
    ("3 passed in 0.42s", 0, {"passed": True, "total": 3, "failures": 0, "duration_ms": 420}),
    ("2 failed, 1 passed in 1.5s", 1, {"passed": False, "total": 3, "failures": 2, "duration_ms": 1500}),
    ("ImportError: cannot import name 'foo'", 2, {"passed": False, "total": 0, "failures": 0}),
])
def test_parse_output(output: str, returncode: int, expected: dict[str, Any]) -> None:
    result = _parse_output(output, returncode=returncode, elapsed_ms=9999)
    for key, val in expected.items():
        assert result[key] == val


def test_parse_output_uses_elapsed_when_no_duration_in_output() -> None:
    result = _parse_output("1 passed", returncode=0, elapsed_ms=999)
    assert result["duration_ms"] == 999


# ── RunTestsTool integration tests ────────────────────────────────────────────

@pytest.fixture
def tool(tmp_path: Path) -> RunTestsTool:
    return RunTestsTool(workspace=str(tmp_path))


def test_run_tests_passes(tool: RunTestsTool) -> None:
    with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="1 passed in 0.05s", stderr="")):
        data = json.loads(tool._execute(path="tests/", flags="-q"))
    assert data["passed"] is True
    assert data["total"] == 1


def test_run_tests_fails(tool: RunTestsTool) -> None:
    with patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="1 failed, 2 passed in 0.20s", stderr="")):
        data = json.loads(tool._execute(path="tests/"))
    assert data["passed"] is False
    assert data["failures"] == 1


def test_run_tests_timeout_and_exception(tool: RunTestsTool) -> None:
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="pytest", timeout=5)):
        data = json.loads(tool._execute(path="tests/", timeout=5))
    assert data["passed"] is False
    assert "timed out" in data["output"]

    with patch("subprocess.run", side_effect=OSError("no such file")):
        data = json.loads(tool._execute(path="tests/"))
    assert data["passed"] is False
    assert "no such file" in data["output"]


def test_run_tests_caps_timeout(tool: RunTestsTool) -> None:
    with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="1 passed in 0.01s", stderr="")) as mock_sub:
        tool._execute(path="tests/", timeout=9999)
    assert mock_sub.call_args.kwargs["timeout"] == 300
