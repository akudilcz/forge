"""file_write — write/create a file in the workspace."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from backend.tools.base import ForgeTool
from backend.tools.write_validation import check_syntax as _check_syntax
from backend.tools.write_validation import resolve_in_workspace


class _Args(BaseModel):
    path: str = Field(description="Relative path to write (created if absent).")
    content: str = Field(description="Full file content to write.")


class FileWriteTool(ForgeTool):
    """Write (or overwrite) a single file in the project workspace, creating parent directories as needed."""

    name: str = "file_write"
    description: str = (
        "Write content to a file in the project workspace, creating it if necessary. "
        "Overwrites the entire file. "
        "Use file_patch to make surgical edits to existing files."
    )
    args_schema: type[BaseModel] = _Args

    def __init__(self, workspace: str) -> None:
        """Args:
            workspace: Absolute path to the project workspace root.
        """
        super().__init__()
        self._workspace = workspace

    def _execute(self, path: str, content: str) -> str:
        """Write content to path (relative to workspace) and return a status string.

        Raises:
            ValueError: If the resolved path escapes the workspace.
        """
        target: Path = resolve_in_workspace(self._workspace, path)

        # Validate syntax before writing Python files
        if path.endswith(".py"):
            error = _check_syntax(content, path)
            if error:
                return f"REJECTED: syntax error in {path} — {error}. Fix and retry."

        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_text(content, encoding="utf-8")
            lines = content.count("\n") + 1
            return f"OK: wrote {lines} lines to {path}"
        except Exception as exc:  # noqa: BLE001
            return f"ERROR writing {path}: {exc}"
