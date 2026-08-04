"""git_ops — safe git operations for agents."""

from __future__ import annotations

import subprocess

from pydantic import BaseModel, Field

from backend.tools.base import ForgeTool, ToolPermissionError

# Only these git subcommands are safe for agents
_ALLOWED_SUBCOMMANDS = {
    "init", "status", "diff", "log", "show", "add", "commit",
    "branch", "checkout", "stash", "tag",
}


class _Args(BaseModel):
    subcommand: str = Field(description=f"Git subcommand. Allowed: {sorted(_ALLOWED_SUBCOMMANDS)}")
    args: str = Field(default="", description="Additional arguments for the git subcommand.")


class GitOpsTool(ForgeTool):
    """Run a restricted set of safe git subcommands inside the project workspace.

    Destructive subcommands (push, reset, force) are blocked.  Commit messages
    are automatically prefixed with the configured commit_prefix.
    """

    name: str = "git_ops"
    description: str = (
        "Run safe git operations: init, status, diff, log, show, add, commit, "
        "branch, checkout, stash, tag. "
        "Destructive operations (push, reset, force) are not permitted."
    )
    args_schema: type[BaseModel] = _Args

    _workspace: str = ""
    _commit_prefix: str = "[forge]"

    def __init__(self, workspace: str, commit_prefix: str = "[forge]") -> None:
        """Args:
            workspace: Absolute path to the git repository root.
            commit_prefix: String prepended to all commit messages produced by agents.
        """
        super().__init__()
        object.__setattr__(self, "_workspace", workspace)
        object.__setattr__(self, "_commit_prefix", commit_prefix)

    def _execute(self, subcommand: str, args: str = "") -> str:
        """Validate subcommand against allowlist, then run git and return its output.

        Raises:
            ToolPermissionError: If subcommand is not in _ALLOWED_SUBCOMMANDS.
        """
        subcommand = subcommand.strip().lower()
        if subcommand not in _ALLOWED_SUBCOMMANDS:
            raise ToolPermissionError(
                f"Git subcommand '{subcommand}' is not permitted. "
                f"Allowed: {sorted(_ALLOWED_SUBCOMMANDS)}"
            )

        # Prefix commit messages automatically
        if subcommand == "commit" and "-m" in args:
            if self._commit_prefix not in args:
                args = args.replace('-m "', f'-m "{self._commit_prefix} ', 1)
                args = args.replace("-m '", f"-m '{self._commit_prefix} ", 1)

        cmd = f"git {subcommand} {args}".strip()
        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=self._workspace,
            )
            output = (result.stdout + result.stderr).strip()
            if result.returncode != 0:
                return f"GIT ERROR (exit {result.returncode}):\n{output}"
            return output or "OK"
        except subprocess.TimeoutExpired:
            return "ERROR: git operation timed out after 30s"
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"
