"""shell_exec — run an allowlisted shell command in the workspace."""

from __future__ import annotations

import fnmatch
import subprocess
from typing import Any

from pydantic import BaseModel, Field

from backend.tools.base import ForgeTool, ToolPermissionError


class _Args(BaseModel):
    command: str = Field(description="Shell command to execute.")
    timeout: int = Field(default=120, description="Timeout in seconds (max 300).")


class ShellExecTool(ForgeTool):
    """Execute an allowlisted shell command in the workspace and return combined stdout/stderr.

    Commands are matched against a configurable allowlist using fnmatch patterns;
    any non-matching command raises ToolPermissionError.
    """

    name: str = "shell_exec"
    description: str = (
        "Execute an allowlisted shell command in the project workspace. "
        "Only commands matching the configured allowlist are permitted. "
        "Returns combined stdout and stderr."
    )
    args_schema: type[BaseModel] = _Args

    _workspace: str = ""
    _allowlist: list[str] = []

    def __init__(self, workspace: str, allowlist: list[str]) -> None:
        """Args:
            workspace: Absolute path to the workspace used as the subprocess cwd.
            allowlist: fnmatch patterns; commands must match at least one pattern.
        """
        super().__init__()
        object.__setattr__(self, "_workspace", workspace)
        object.__setattr__(self, "_allowlist", allowlist)

    def _is_allowed(self, command: str) -> bool:
        """Return True if command matches any pattern in the allowlist."""
        for pattern in self._allowlist:
            if fnmatch.fnmatch(command, pattern) or command.startswith(
                pattern.rstrip("*")
            ):
                return True
        return False

    def _execute(self, command: str, timeout: int = 120) -> str:  # type: ignore[override]
        """Run command in the workspace and return its output, or an error string on failure.

        Raises:
            ToolPermissionError: If command does not match the allowlist.
        """
        if not self._is_allowed(command):
            raise ToolPermissionError(
                f"Command '{command}' is not in the shell_exec allowlist."
            )
        timeout = min(timeout, 300)
        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=self._workspace,
            )
            output = result.stdout + result.stderr
            if result.returncode != 0:
                return f"EXIT {result.returncode}:\n{output}"
            return output or "OK (no output)"
        except subprocess.TimeoutExpired:
            return f"ERROR: Command timed out after {timeout}s"
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    def _run(self, *args: Any, **kwargs: Any) -> str:
        """Run command, then log structured test results from JUnit XML if available."""
        kwargs.pop("run_manager", None)
        from backend.server.forge_logger import forge_logger  # noqa: PLC0415
        try:
            command = kwargs.get("command", "")
            if _is_bazel_test(command):
                _regen_build_files(self._workspace)
            result = self._execute(**kwargs)
            failed = result.startswith("EXIT ") or result.startswith("ERROR")
            success = not failed
            if _is_test_command(command):
                _log_junit_results(self._workspace, forge_logger)
            lines = result.strip().splitlines()
            summary = lines[-1] if lines else "(no output)"
            forge_logger.tool_result(
                self.name, success, summary,
                full_output=(result if not success else None),
            )
            return result
        except Exception as exc:  # noqa: BLE001
            err = f"TOOL_ERROR: {type(exc).__name__}: {exc}"
            forge_logger.tool_result(
                self.name, False, str(exc)[:120], full_output=err,
            )
            return err


_TEST_KEYWORDS = ("pytest", "bazel test", "bazel coverage", "bazel run //tests")


def _is_test_command(command: str) -> bool:
    """Return True if the command looks like a test invocation."""
    return any(kw in command for kw in _TEST_KEYWORDS)


def _is_bazel_test(command: str) -> bool:
    """Return True if the command is a bazel test/coverage invocation."""
    return "bazel test" in command or "bazel coverage" in command


def _regen_build_files(workspace: str) -> None:
    """Regenerate BUILD files so bazel sees newly-written source/test files.

    The orchestrator owns BUILD generation (init_bazel_workspace), but
    agents write source/test files and then immediately invoke
    ``bazel test``.  Without this hook the BUILD files are stale.
    """
    from pathlib import Path  # noqa: PLC0415

    from backend.crew.bazel_gen import init_bazel_workspace  # noqa: PLC0415

    init_bazel_workspace(Path(workspace))


def _format_test_error(log_file: Any) -> str:
    """Extract error detail from the bazel test.log file.

    The test.log is the primary source for tracebacks — bazel writes
    actual test stdout/stderr there, not into the JUnit XML.
    """
    if not log_file.exists():
        return "(no test.log)"
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(unreadable test.log)"
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return "(empty test.log)"
    # Last 5 lines of output — typically the exception + message
    tail = lines[-5:] if len(lines) > 5 else lines
    return "\n    ".join(tail)


def _read_log_tail(log_file: Any, limit: int) -> str | None:
    """Return the trailing ``limit`` bytes of a test log, if readable.

    Used as obs payload for diagnostic emits — captures full stdout/stderr
    context of failed tests/builds so agents and operators can see what
    went wrong without re-running the failing command.
    """
    if not log_file.exists():
        return None
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return text[-limit:] if len(text) > limit else text


def _log_junit_results(workspace: str, logger: Any) -> None:
    """Read JUnit XML from bazel-testlogs and log structured per-test results.

    No regex parsing of stdout — reads the structured XML that bazel
    and pytest write after each test run. When the XML contains only
    bazel's generic stub error, reads the companion test.log for detail.
    """
    import xml.etree.ElementTree as ET
    from pathlib import Path

    testlogs = Path(workspace) / "bazel-testlogs" / "tests"
    if not testlogs.exists():
        return

    for xml_file in sorted(testlogs.glob("*/test.xml")):
        try:
            tree = ET.parse(xml_file)  # noqa: S314
        except ET.ParseError:
            logger.emit("WARN", "TEST ", f"Failed to parse {xml_file.name}")
            continue

        # Companion log next to test.xml — the primary source for tracebacks
        log_file = xml_file.with_name("test.log")

        for tc in tree.iter("testcase"):
            name = tc.get("name", "?")
            classname = tc.get("classname", "")
            label = f"{classname}::{name}" if classname else name

            failure = tc.find("failure")
            error = tc.find("error")
            skipped = tc.find("skipped")

            if failure is not None:
                msg = _format_test_error(log_file)
                logger.emit(
                    "WARN", "TEST ", f"FAIL {label}: {msg}",
                    test_output=_read_log_tail(log_file, 4096),
                )
            elif error is not None:
                msg = _format_test_error(log_file)
                logger.emit(
                    "ERROR", "TEST ", f"ERROR {label}: {msg}",
                    test_output=_read_log_tail(log_file, 4096),
                )
            elif skipped is not None:
                logger.emit("INFO", "TEST ", f"SKIP {label}")
            else:
                logger.emit("INFO", "TEST ", f"PASS {label}")
