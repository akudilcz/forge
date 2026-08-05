"""Shared validation for workspace write tools.

``file_write``, ``multi_file_write`` and ``file_patch`` must enforce
identical guarantees (design/22 "Tool Set"): the resolved target path
stays inside the workspace, and Python content passes ``ast.parse``
before anything is persisted.  Centralising the checks here keeps the
three write paths symmetric — content one tool rejects cannot be landed
through another.
"""

from __future__ import annotations

import ast
from pathlib import Path


def check_syntax(code: str, filename: str) -> str:
    """Return an error string (with line info) if *code* fails ``ast.parse``.

    Args:
        code: Python source to validate.
        filename: Name used in the parse error message.

    Returns:
        Empty string when the code parses; otherwise a message containing
        the parser error, the line number, and the offending text.
    """
    try:
        ast.parse(code, filename=filename)
        return ""
    except SyntaxError as exc:
        line_info = f"line {exc.lineno}" if exc.lineno else "unknown line"
        if exc.text:
            return f"{exc.msg} at {line_info}: {exc.text.strip()}"
        return f"{exc.msg} at {line_info}"


def resolve_in_workspace(workspace: str, path: str) -> Path:
    """Resolve *path* against *workspace*, enforcing containment.

    Args:
        workspace: Absolute path to the workspace root.
        path: Workspace-relative path supplied by the agent.

    Returns:
        The fully resolved target path inside the workspace.

    Raises:
        ValueError: If *path* is empty, or if the resolved target lies
            outside the workspace (e.g. via ``..`` segments or an
            absolute path).
    """
    if not path:
        raise ValueError("empty path")
    root = Path(workspace).resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise ValueError(
            f"path '{path}' resolves outside the workspace: {target}"
        )
    return target
