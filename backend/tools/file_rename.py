"""file_rename — rename or move a file within the workspace."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from backend.tools.base import ForgeTool


class _Args(BaseModel):
    old_path: str = Field(description="Current relative path of the file to rename.")
    new_path: str = Field(description="New relative path (parent directories created if needed).")


_NAME = "file_rename"
_DESCRIPTION = (
    "Rename or move a file within the workspace. "
    "old_path is the current location, new_path is the destination. "
    "Parent directories are created automatically. "
    "Fails if new_path already exists (no silent overwrite)."
)


class FileRenameTool(ForgeTool):
    """Rename or move a file within the project workspace.

    Moves ``old_path`` to ``new_path``, creating parent directories as
    needed.  Both paths are relative to the workspace root.  The tool
    refuses to overwrite an existing file at ``new_path``.
    """

    name: str = _NAME
    description: str = _DESCRIPTION
    args_schema: type[BaseModel] = _Args

    _workspace: str = ""

    def __init__(self, workspace: str = ".") -> None:
        # name/description are also passed here because BaseTool declares them
        # as required fields; the class-level defaults alone do not satisfy it.
        super().__init__(name=_NAME, description=_DESCRIPTION)
        object.__setattr__(self, "_workspace", workspace)

    def _execute(self, old_path: str, new_path: str) -> str:  # type: ignore[override]
        ws = Path(self._workspace)
        source = ws / old_path
        dest = ws / new_path

        if not source.exists():
            return f"ERROR: source file not found: {old_path}"
        if not source.is_file():
            return f"ERROR: source is not a file: {old_path}"
        if dest.exists():
            return f"ERROR: destination already exists: {new_path}"

        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            source.rename(dest)
            return f"OK: renamed {old_path} → {new_path}"
        except OSError as exc:
            return f"ERROR renaming {old_path} → {new_path}: {exc}"
