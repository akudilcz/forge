"""list_dir — list files and folders in a workspace directory."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from backend.tools.base import ForgeTool


class _Args(BaseModel):
    path: str = Field(
        default=".",
        description="Directory to list (relative to workspace root). Defaults to workspace root.",
    )


class ListDirTool(ForgeTool):
    """List the immediate contents of a workspace directory (one level deep)."""

    name: str = "list_dir"
    description: str = (
        "List files and folders in a workspace directory (one level deep). "
        "Returns a JSON array of objects with 'name', 'type' (file|dir), and 'path' fields. "
        "Use to explore workspace structure before reading files."
    )
    args_schema: type[BaseModel] = _Args

    def __init__(self, workspace: str = ".") -> None:
        """Args:
            workspace: Absolute path to the project workspace root.
        """
        # name/description are supplied as field defaults on this subclass;
        # mypy models the pydantic base __init__ as requiring them.
        super().__init__()
        self._workspace = workspace

    def _execute(self, path: str = ".") -> str:  # type: ignore[override]
        """Return a JSON array of {name, type, path} entries for the given directory."""
        root = Path(self._workspace) / path
        if not root.exists():
            return json.dumps([])
        if not root.is_dir():
            return f"ERROR: {path!r} is not a directory"

        try:
            entries = [
                {
                    "name": p.name,
                    "type": "dir" if p.is_dir() else "file",
                    "path": str(p.relative_to(self._workspace)),
                }
                for p in sorted(root.iterdir())
            ]
            return json.dumps(entries)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR listing {path}: {exc}"
