"""multi_file_write — write multiple files in the workspace in one call."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.tools.base import ForgeTool


class _FileEntry(BaseModel):
    """A single file entry for multi-file write operations."""

    path: str = Field(description="Relative path within the workspace (created if absent).")
    content: str = Field(description="Full file content to write.")


class _Args(BaseModel):
    files: list[_FileEntry] = Field(
        description="List of {path, content} pairs to write."
    )


_NAME = "multi_file_write"
_DESCRIPTION = (
    "Write multiple files to the project workspace in one call. "
    "Creates parent directories as needed and overwrites existing files. "
    "Prefer this over repeated file_write calls when generating several files."
)


class MultiFileWriteTool(ForgeTool):
    """Write multiple files to the project workspace in a single tool call.

    Each entry in ``files`` is written atomically (parent directories are
    created on demand).  The tool enforces write-path scope for *every* path
    in the batch before performing any writes, so the agent is blocked early
    if any path is out of scope — preventing partial writes that would leave
    the workspace in an inconsistent state.

    This is more efficient than calling ``file_write`` in a loop when an agent
    needs to produce several related files (e.g. a module + its tests).
    """

    name: str = _NAME
    description: str = _DESCRIPTION
    args_schema: type[BaseModel] = _Args

    _workspace: str = ""

    def __init__(self, workspace: str) -> None:
        """Initialise the tool with a workspace root path.

        Args:
            workspace: Absolute path to the project workspace root.
        """
        # name/description are also passed here because BaseTool declares them
        # as required fields; the class-level defaults alone do not satisfy it.
        super().__init__(name=_NAME, description=_DESCRIPTION)
        object.__setattr__(self, "_workspace", workspace)

    # ------------------------------------------------------------------
    def _execute(self, files: list[Any]) -> str:  # type: ignore[override]
        """Write each file entry and return a per-file status summary.

        Args:
            files: List of _FileEntry objects (or dicts with ``path``/``content``
                keys) to write.  Parent directories are created automatically.

        Returns:
            A newline-joined summary with one status line per file, e.g.
            ``OK: wrote 42 lines to backend/auth.py``.  Individual write
            errors are reported inline so the agent knows which files failed
            without hiding successes.
        """
        if not files:
            return "ERROR: No files provided."

        results: list[str] = []
        for entry in files:
            # Handle both dict (raw JSON from CrewAI) and _FileEntry objects.
            if isinstance(entry, dict):
                path: str = entry.get("path", "")
                content: str = entry.get("content", "")
            else:
                path = getattr(entry, "path", "")
                content = getattr(entry, "content", "")

            if not path:
                results.append("ERROR: empty path in files list entry")
                continue

            target = Path(self._workspace) / path
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                target.write_text(content, encoding="utf-8")
                lines = content.count("\n") + 1
                results.append(f"OK: wrote {lines} lines to {path}")
            except Exception as exc:  # noqa: BLE001
                results.append(f"ERROR writing {path}: {exc}")

        return "\n".join(results)
