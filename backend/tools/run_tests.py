"""run_tests — run the project test suite."""

from __future__ import annotations

import json
import re
import subprocess
import time

from pydantic import BaseModel, Field

from backend.tools.base import ForgeTool


class _Args(BaseModel):
    path: str = Field(default="tests/", description="Test file or directory to run.")
    flags: str = Field(default="-x -q", description="Additional pytest flags.")
    timeout: int = Field(default=180, description="Timeout in seconds.")


def _parse_output(output: str, returncode: int, elapsed_ms: int) -> dict:
    """Extract structured test metrics from pytest stdout/stderr."""
    total = 0
    failures = 0

    # "N failed, M passed in Xs" or "N failed in Xs"
    failed_match = re.search(r"(\d+) failed(?:, (\d+) passed)?", output)
    if failed_match:
        failures = int(failed_match.group(1))
        passed_count = int(failed_match.group(2) or 0)
        total = failures + passed_count
    else:
        # "N passed in Xs" (all passing)
        passed_match = re.search(r"(\d+) passed", output)
        if passed_match:
            total = int(passed_match.group(1))
            failures = 0

    # Prefer duration reported by pytest over wall-clock
    dur_match = re.search(r"\bin (\d+\.?\d*)s\b", output)
    if dur_match:
        elapsed_ms = int(float(dur_match.group(1)) * 1000)

    return {
        "passed": returncode == 0,
        "total": total,
        "failures": failures,
        "duration_ms": elapsed_ms,
        "output": output,
    }


class RunTestsTool(ForgeTool):
    """Run the project's pytest test suite and return structured results as JSON."""

    name: str = "run_tests"
    description: str = (
        "Run the project's pytest test suite. "
        "Returns structured JSON with passed (bool), total (int), failures (int), "
        "duration_ms (int), and output (str). "
        "Use path to restrict to a specific file or directory."
    )
    args_schema: type[BaseModel] = _Args

    _workspace: str = ""
    _python: str = "python"

    def __init__(self, workspace: str, python_path: str = "python") -> None:
        """Args:
            workspace: Absolute path to the project workspace (pytest cwd).
            python_path: Python executable used to invoke pytest.
        """
        super().__init__()
        object.__setattr__(self, "_workspace", workspace)
        object.__setattr__(self, "_python", python_path)

    def _execute(self, path: str = "tests/", flags: str = "-x -q", timeout: int = 180) -> str:
        """Run pytest on path with flags and return a JSON metrics dict.

        The returned JSON always has keys: passed, total, failures, duration_ms, output.
        Timeout is capped at 300 s to prevent runaway test processes.
        """
        timeout = min(timeout, 300)
        # Add per-test timeout if not already specified in flags
        if "--timeout" not in flags:
            flags = f"{flags} --timeout=10"
        cmd = f"{self._python} -m pytest {path} {flags}"
        start = time.monotonic()
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self._workspace,
            )
            elapsed_ms = int((time.monotonic() - start) * 1000)
            output = result.stdout + result.stderr
            metrics = _parse_output(output, result.returncode, elapsed_ms)
            return json.dumps(metrics)
        except subprocess.TimeoutExpired:
            return json.dumps({
                "passed": False,
                "total": 0,
                "failures": 0,
                "duration_ms": timeout * 1000,
                "output": f"ERROR: Tests timed out after {timeout}s",
            })
        except Exception as exc:  # noqa: BLE001
            return json.dumps({
                "passed": False,
                "total": 0,
                "failures": 0,
                "duration_ms": 0,
                "output": f"ERROR: {exc}",
            })
