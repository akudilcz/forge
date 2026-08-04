"""list_files — list workspace files matching a glob pattern."""

from __future__ import annotations

import fnmatch
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel, Field

from backend.tools.base import ForgeTool

# Directories that should never appear in tool results.  These bloat
# the agent's context window with thousands of irrelevant paths.
_SKIP_DIRS: frozenset[str] = frozenset({
    ".venv",
    "venv",
    "__pycache__",
    ".git",
    "node_modules",
    ".pytest_cache",
    "bazel-bin",
    "bazel-out",
    "bazel-testlogs",
    "bazel-workspace",
    ".mypy_cache",
    ".ruff_cache",
})


class _Args(BaseModel):
    path: str = Field(description="Directory to search in (relative to workspace root).")
    pattern: str = Field(default="*.py", description="Glob pattern to match filenames.")


class ListFilesTool(ForgeTool):
    """Recursively list files in a workspace directory whose names match a glob pattern."""

    name: str = "list_files"
    description: str = (
        "List files in the workspace directory matching a glob pattern. "
        "Returns a JSON array of relative file paths. "
        "Use before reading files to discover what exists in the workspace."
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

    def _execute(self, path: str, pattern: str = "*.py") -> str:  # type: ignore[override]
        """Return a JSON array of relative file paths matching pattern under path."""
        import json

        root = Path(self._workspace) / path
        if not root.exists():
            return json.dumps([])
        if not root.is_dir():
            return f"ERROR: {path!r} is not a directory"

        try:
            matches = [
                str(p.relative_to(self._workspace))
                for p in sorted(_walk_filtered(root))
                if p.is_file() and fnmatch.fnmatch(p.name, pattern)
            ]
            return json.dumps(matches)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR listing {path}: {exc}"


def _walk_filtered(root: Path) -> Iterator[Path]:
    """Yield all files under *root*, skipping noisy directories."""
    for child in sorted(root.iterdir()):
        if child.is_dir():
            if child.name in _SKIP_DIRS:
                continue
            yield from _walk_filtered(child)
        else:
            yield child
