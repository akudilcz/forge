"""read_docs — read rendered documentation from the workspace docs/ directory.

Provides Phase 12 codegen agents with on-demand access to rendered
documentation from Phase 11, rather than front-loading all docs into
the initial prompt.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.tools.base import ForgeTool


class _Args(BaseModel):
    filename: str = Field(
        default="",
        description=(
            "Name of the doc file to read (e.g. '08-Design.md'). "
            "Leave empty to list all available docs."
        ),
    )


class ReadDocsTool(ForgeTool):
    """Read rendered documentation from the workspace docs/ directory.

    Call with no arguments to list available docs.
    Call with a filename to read its full content.
    """

    name: str = "read_docs"
    description: str = (
        "Read rendered project documentation from docs/. "
        "Call with no filename to list available docs. "
        "Call with a filename (e.g. '08-Design.md') to read its content. "
        "Use this to understand requirements, architecture, or test specs."
    )
    args_schema: type[BaseModel] = _Args

    _workspace: str = "."

    def __init__(self, workspace: str = ".") -> None:
        super().__init__()
        object.__setattr__(self, "_workspace", workspace)

    def _execute(self, **kwargs: Any) -> str:
        """Forward the LLM's validated ``_Args`` fields to :meth:`_read`."""
        return self._read(**kwargs)

    def _read(self, filename: str = "") -> str:
        docs_dir = Path(self._workspace) / "docs"
        if not docs_dir.is_dir():
            return "No docs/ directory found in workspace."

        if not filename:
            return self._list_docs(docs_dir)

        target = docs_dir / filename
        if not target.is_file():
            available = self._list_docs(docs_dir)
            return f"ERROR: File not found: docs/{filename}\n\n{available}"

        try:
            return target.read_text(encoding="utf-8")
        except OSError as exc:
            return f"ERROR reading docs/{filename}: {exc}"

    def _list_docs(self, docs_dir: Path) -> str:
        files = sorted(
            p for p in docs_dir.iterdir()
            if p.is_file() and p.suffix == ".md"
        )
        if not files:
            return "No documentation files found in docs/."
        lines = ["Available documentation:"]
        for f in files:
            size_kb = f.stat().st_size / 1024
            lines.append(f"  - {f.name} ({size_kb:.1f} KB)")
        return "\n".join(lines)
