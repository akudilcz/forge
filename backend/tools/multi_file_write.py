"""multi_file_write — write multiple files in the workspace in one call."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.tools.base import ForgeTool
from backend.tools.write_validation import check_syntax, resolve_in_workspace


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
    "The whole batch is validated first (workspace containment, Python "
    "syntax for .py files); any violation rejects the batch atomically. "
    "Prefer this over repeated file_write calls when generating several files."
)


class MultiFileWriteTool(ForgeTool):
    """Write multiple files to the project workspace in a single tool call.

    Every entry in ``files`` is validated *before* any write happens:
    required keys must be present (a missing ``path`` or ``content``
    raises — it is never defaulted), each resolved path must stay inside
    the workspace, and ``.py`` content must pass ``ast.parse`` — the same
    syntax gate ``file_write`` enforces.  Any violation rejects the whole
    batch atomically, so a bad entry can never truncate an existing file
    or leave the workspace half-written.

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
        """Validate the whole batch, then write, returning per-file status.

        Args:
            files: List of _FileEntry objects (or dicts with ``path``/``content``
                keys) to write.  Parent directories are created automatically.

        Returns:
            On success, a newline-joined summary with one ``OK`` line per
            file.  If any ``.py`` entry fails the syntax check, a single
            ``REJECTED`` message listing every offending file — nothing
            is written.

        Raises:
            ValueError: If an entry is missing ``path`` or ``content``,
                has an empty path, or resolves outside the workspace.
                Nothing is written in that case.
        """
        if not files:
            return "ERROR: No files provided."

        # Phase 1: validate everything before touching the filesystem.
        batch: list[tuple[str, str, Path]] = []
        for index, entry in enumerate(files):
            path, content = _normalise_entry(entry, index)
            target = resolve_in_workspace(self._workspace, path)
            batch.append((path, content, target))

        syntax_errors = [
            f"{path}: {error}"
            for path, content, _ in batch
            if path.endswith(".py") and (error := check_syntax(content, path))
        ]
        if syntax_errors:
            details = "\n".join(syntax_errors)
            return (
                "REJECTED: batch not written — syntax error(s) in Python "
                f"content:\n{details}\nFix and retry; no files were modified."
            )

        # Phase 2: write.
        results: list[str] = []
        for path, content, target in batch:
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                target.write_text(content, encoding="utf-8")
                lines = content.count("\n") + 1
                results.append(f"OK: wrote {lines} lines to {path}")
            except Exception as exc:  # noqa: BLE001
                results.append(f"ERROR writing {path}: {exc}")

        return "\n".join(results)


def _normalise_entry(entry: Any, index: int) -> tuple[str, str]:
    """Return ``(path, content)`` for a batch entry, raising on missing data.

    Args:
        entry: A dict (raw JSON from the agent) or a ``_FileEntry`` model.
        index: Position of the entry in the batch, used in error messages.

    Raises:
        ValueError: If a dict entry lacks the ``path`` or ``content`` key,
            or if the path is empty.  Keys are never defaulted — a missing
            ``content`` must not silently truncate an existing file.
    """
    if isinstance(entry, dict):
        if "path" not in entry:
            raise ValueError(f"files[{index}] is missing required key 'path'")
        if "content" not in entry:
            raise ValueError(f"files[{index}] is missing required key 'content'")
        path, content = entry["path"], entry["content"]
    else:
        path, content = entry.path, entry.content

    if not path:
        raise ValueError(f"files[{index}] has an empty path")
    return path, content
