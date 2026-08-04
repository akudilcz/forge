"""batch_patch — apply multiple text replacements in a single file atomically.

Accepts a list of (old_text, new_text) pairs and applies them all at once,
validating syntax once at the end. This is far more efficient than calling
file_patch N times, which requires N separate LLM turns.

All replacements are applied to the original text (not sequentially), so
they cannot conflict with each other's changes.
"""

from __future__ import annotations

import ast
from pathlib import Path

from pydantic import BaseModel, Field

from backend.tools.base import ForgeTool


class _PatchEntry(BaseModel):
    old_text: str = Field(description="Text to find (must be unique in the file).")
    new_text: str = Field(description="Replacement text.")


class _Args(BaseModel):
    path: str = Field(description="Relative path to the file to patch.")
    patches: list[_PatchEntry] = Field(
        description="List of {old_text, new_text} pairs to apply."
    )


_NAME = "batch_patch"
_DESCRIPTION = (
    "Apply multiple text replacements in a single file at once. "
    "Pass a list of {old_text, new_text} pairs. Each old_text must "
    "be unique in the file. Syntax is validated once after all edits. "
    "Use this instead of multiple file_patch calls."
)


class BatchPatchTool(ForgeTool):
    """Apply multiple text replacements in one file in a single call.

    Each patch must have a unique old_text in the file. All patches are
    validated together, and syntax is checked once after all edits.
    More efficient than calling file_patch repeatedly.
    """

    name: str = _NAME
    description: str = _DESCRIPTION
    args_schema: type[BaseModel] = _Args

    _workspace: str = ""

    def __init__(self, workspace: str) -> None:
        # name/description are also passed here because BaseTool declares them
        # as required fields; the class-level defaults alone do not satisfy it.
        super().__init__(name=_NAME, description=_DESCRIPTION)
        object.__setattr__(self, "_workspace", workspace)

    def _execute(self, path: str, patches: list[_PatchEntry]) -> str:  # type: ignore[override]
        target = Path(self._workspace) / path
        if not target.exists():
            return f"ERROR: File not found: {path}"
        if not patches:
            return "ERROR: No patches provided."

        try:
            content = target.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return f"ERROR reading {path}: {exc}"

        # Validate all old_texts exist and are unique before applying
        errors: list[str] = []
        for i, p in enumerate(patches):
            count = content.count(p.old_text)
            if count == 0:
                errors.append(f"Patch {i+1}: old_text not found")
            elif count > 1:
                errors.append(
                    f"Patch {i+1}: old_text found {count} times (must be unique)"
                )
        if errors:
            return "ERROR:\n" + "\n".join(errors)

        # Apply all patches (each old_text is unique, so order doesn't matter)
        result = content
        applied = 0
        for p in patches:
            result = result.replace(p.old_text, p.new_text, 1)
            applied += 1

        # Syntax validation for Python files
        if path.endswith(".py"):
            try:
                ast.parse(result, filename=path)
            except SyntaxError as exc:
                line_info = f" (line {exc.lineno})" if exc.lineno else ""
                return (
                    f"ERROR: Batch edit produces invalid Python in {path}:\n"
                    f"{exc.msg}{line_info}\n"
                    "Fix the patches and retry."
                )

        try:
            target.write_text(result, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            return f"ERROR writing {path}: {exc}"

        return f"OK: applied {applied} patch(es) to {path}"
