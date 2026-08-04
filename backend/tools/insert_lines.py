"""insert_lines — insert text after a specific line number.

Supports parallel calls on the same file: a per-file lock serialises
concurrent insertions, and each call re-reads the file so it sees
the updated line count from prior insertions.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.tools.base import ForgeTool

_NAME = "insert_lines"
_DESCRIPTION = (
    "Insert text after a specific line number in a file. "
    "after_line is 1-indexed; use 0 to insert at the top. "
    "Safe to call multiple times on the same file in parallel. "
    "Ideal for adding @traces decorators before function definitions."
)

# Per-file lock to serialise concurrent inserts on the same file.
# Without this, parallel tool calls read stale line counts and
# produce "out of range" errors.
_file_locks: dict[str, threading.Lock] = {}
_lock_guard = threading.Lock()


def _get_file_lock(path: str) -> threading.Lock:
    """Return (or create) a lock for the given file path."""
    with _lock_guard:
        if path not in _file_locks:
            _file_locks[path] = threading.Lock()
        return _file_locks[path]


class _Args(BaseModel):
    path: str = Field(description="Relative path to the file.")
    after_line: int = Field(
        description="Line number to insert after (1-indexed). 0 = insert at top.",
    )
    text: str = Field(description="Text to insert as new line(s).")


class InsertLinesTool(ForgeTool):
    """Insert text after a specific line number in a workspace file.

    Safe for parallel calls on the same file — concurrent insertions
    are serialised so each sees the correct line count.
    """

    name: str = _NAME
    description: str = _DESCRIPTION
    args_schema: type[BaseModel] = _Args

    _workspace: str = ""

    def __init__(self, workspace: str = ".") -> None:
        super().__init__(name=_NAME, description=_DESCRIPTION)
        object.__setattr__(self, "_workspace", workspace)

    def _execute(self, *args: Any, **kwargs: Any) -> str:
        """Dispatch entry point — forwards schema-validated args to :meth:`_insert`."""
        return self._insert(*args, **kwargs)

    def _insert(self, path: str, after_line: int, text: str) -> str:
        """Insert text after the given line number.

        Uses a per-file lock so concurrent calls on the same file
        are serialised.  Each call re-reads the file to see any
        lines inserted by prior concurrent calls.
        """
        target = Path(self._workspace) / path
        if not target.exists():
            return f"ERROR: File not found: {path}"
        if not target.is_file():
            return f"ERROR: Path is not a file: {path}"

        abs_path = str(target.resolve())
        lock = _get_file_lock(abs_path)

        with lock:
            try:
                content = target.read_text(encoding="utf-8")
            except Exception as exc:  # noqa: BLE001
                return f"ERROR reading {path}: {exc}"

            lines = content.splitlines(keepends=True)
            if after_line < 0 or after_line > len(lines):
                return (
                    f"ERROR: after_line={after_line} out of range "
                    f"(file has {len(lines)} lines)"
                )

            new_line = text if text.endswith("\n") else text + "\n"
            lines.insert(after_line, new_line)

            try:
                target.write_text("".join(lines), encoding="utf-8")
                return f"OK: inserted after line {after_line} in {path}"
            except Exception as exc:  # noqa: BLE001
                return f"ERROR writing {path}: {exc}"
