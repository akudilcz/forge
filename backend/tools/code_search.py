"""code_search — search for patterns across workspace source files."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from backend.tools.base import ForgeTool

_BINARY_SUFFIXES = {
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".bin", ".db",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot",
    ".zip", ".tar", ".gz", ".bz2",
    ".lock",  # skip lock files
}


class _Args(BaseModel):
    pattern: str = Field(description="Text or regex pattern to search for.")
    glob: str = Field(default="**/*.py", description="Glob pattern to filter files (e.g. '**/*.ts').")
    max_results: int = Field(default=50, description="Maximum number of matching lines to return.")


class CodeSearchTool(ForgeTool):
    """Case-insensitive substring search across workspace source files, with glob-based file filtering.

    Skips binary files and hidden/build directories automatically.
    """

    name: str = "code_search"
    description: str = (
        "Search for a pattern across source files in the workspace. "
        "Returns matching file paths and line numbers. "
        "Use glob to restrict to a file type (e.g. '**/*.py', '**/*.ts')."
    )
    args_schema: type[BaseModel] = _Args

    _workspace: str = ""

    def __init__(self, workspace: str) -> None:
        """Args:
            workspace: Absolute path to the project workspace root.
        """
        super().__init__()
        object.__setattr__(self, "_workspace", workspace)

    def _execute(self, pattern: str, glob: str = "**/*.py", max_results: int = 50) -> str:
        """Search files matching glob for pattern (case-insensitive substring match).

        Returns:
            A header line followed by ``file:line: content`` entries, or a
            no-match message when nothing is found.
        """
        root = Path(self._workspace)
        matches: list[str] = []

        try:
            files = list(root.glob(glob))
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: Invalid glob pattern '{glob}': {exc}"

        for file_path in files:
            if file_path.suffix in _BINARY_SUFFIXES:
                continue
            # Skip hidden/build directories
            parts = file_path.relative_to(root).parts
            if any(p.startswith(".") or p in ("node_modules", "__pycache__", "dist", "build") for p in parts):
                continue
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
                for lineno, line in enumerate(text.splitlines(), 1):
                    if pattern.lower() in line.lower():
                        rel = file_path.relative_to(root)
                        matches.append(f"{rel}:{lineno}: {line.rstrip()}")
                        if len(matches) >= max_results:
                            break
            except Exception:  # noqa: BLE001
                continue
            if len(matches) >= max_results:
                break

        if not matches:
            return f"No matches found for '{pattern}' in {glob}"
        header = f"Found {len(matches)} match(es) for '{pattern}':\n"
        return header + "\n".join(matches)
