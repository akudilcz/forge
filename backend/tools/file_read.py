"""file_read — read a file from the workspace."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from backend.tools.base import ForgeTool


class _Args(BaseModel):
    path: str = Field(description="Relative path to the file within the workspace.")
    encoding: str = Field(default="utf-8", description="File encoding.")
    start_line: int = Field(default=0, description="First line to return (1-indexed). 0 = from start.")
    end_line: int = Field(default=0, description="Last line to return (1-indexed, inclusive). 0 = to end.")


class FileReadTool(ForgeTool):
    """Read the full text content of a single file from the project workspace."""

    name: str = "file_read"
    description: str = (
        "Read a file from the workspace. Output always includes line numbers "
        "(e.g. '   1 | def foo():'). Use start_line/end_line to read a range. "
        "Line numbers are needed for insert_lines and file_patch."
    )
    args_schema: type[BaseModel] = _Args

    def __init__(self, workspace: str = ".") -> None:
        """Args:
            workspace: Absolute path to the project workspace root.
        """
        super().__init__()
        self._workspace = workspace

    def _execute(
        self, path: str, encoding: str = "utf-8",
        start_line: int = 0, end_line: int = 0,
    ) -> str:
        """Read and return the file at path (relative to workspace).

        When start_line/end_line are set, returns only those lines (1-indexed,
        inclusive) prefixed with line numbers. Returns an error string if the
        path does not exist or is not a file.
        """
        target = Path(self._workspace) / path
        if not target.exists():
            return f"ERROR: File not found: {path}"
        if not target.is_file():
            return f"ERROR: Path is not a file: {path}"
        try:
            content = target.read_text(encoding=encoding)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR reading {path}: {exc}"

        lines = content.splitlines()
        start = max(start_line, 1) if start_line > 0 else 1
        end = end_line if end_line > 0 else len(lines)
        selected = lines[start - 1 : end]
        return "\n".join(
            f"{start + i:4d} | {line}" for i, line in enumerate(selected)
        )
