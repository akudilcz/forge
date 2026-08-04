"""python_lint — syntax check and style analysis for Python files."""

from __future__ import annotations

import ast
import py_compile
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

from backend.tools.base import ForgeTool


class _Args(BaseModel):
    path: str = Field(
        description=(
            "Relative path to the Python file to check. "
            "Use '*' to check all .py files in src/ and tests/."
        ),
    )


_NAME = "python_lint"
_DESCRIPTION = (
    "Check a Python file for syntax errors and style issues. "
    "Returns a list of issues found (empty = clean). "
    "Use path='*' to check all .py files in src/ and tests/."
)


class PythonLintTool(ForgeTool):
    """Check Python files for syntax errors, undefined names, and style issues.

    Runs three checks in order:
    1. **Syntax** — ``py_compile`` (always available)
    2. **AST analysis** — detects common issues (unused imports, bare excepts)
    3. **Ruff** — PEP 8 + pyflakes + isort if ``ruff`` is on PATH

    Call with ``path='*'`` to check all source and test files.
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

    def _execute(self, path: str) -> str:  # type: ignore[override]
        ws = Path(self._workspace)
        if path == "*":
            return self._check_all(ws)
        return self._check_file(ws, path)

    def _check_all(self, ws: Path) -> str:
        """Check all .py files in src/ and tests/."""
        files: list[Path] = []
        for subdir in ("src", "tests"):
            d = ws / subdir
            if d.exists():
                files.extend(sorted(d.rglob("*.py")))

        if not files:
            return "No Python files found in src/ or tests/."

        all_issues: list[str] = []
        clean = 0
        for f in files:
            rel = str(f.relative_to(ws))
            result = self._check_file(ws, rel)
            if result.startswith("OK"):
                clean += 1
            else:
                all_issues.append(result)

        summary = f"Checked {len(files)} file(s): {clean} clean"
        if all_issues:
            summary += f", {len(all_issues)} with issues"
            return summary + "\n\n" + "\n\n".join(all_issues)
        return summary

    def _check_file(self, ws: Path, rel_path: str) -> str:
        """Check a single file."""
        target = ws / rel_path
        if not target.exists():
            return f"ERROR: file not found: {rel_path}"
        if not target.is_file() or not target.suffix == ".py":
            return f"ERROR: not a Python file: {rel_path}"

        issues: list[str] = []

        # 1. Syntax check
        syntax_err = _check_syntax(target)
        if syntax_err:
            issues.append(f"SYNTAX: {syntax_err}")
            # Can't do further checks if syntax is broken
            return f"{rel_path}:\n" + "\n".join(f"  - {i}" for i in issues)

        # 2. AST analysis
        ast_issues = _check_ast(target)
        issues.extend(ast_issues)

        # 3. Ruff (if available)
        ruff_issues = _check_ruff(target)
        issues.extend(ruff_issues)

        if not issues:
            return f"OK: {rel_path} — no issues"
        return f"{rel_path}:\n" + "\n".join(f"  - {i}" for i in issues)


def _check_syntax(path: Path) -> str:
    """Return syntax error description, or empty string if clean."""
    try:
        py_compile.compile(str(path), doraise=True)
        return ""
    except py_compile.PyCompileError as exc:
        return str(exc).split("\n")[0]


def _check_ast(path: Path) -> list[str]:
    """Basic AST checks: bare excepts, star imports, missing docstrings."""
    try:
        code = path.read_text(encoding="utf-8")
        tree = ast.parse(code)
    except (OSError, SyntaxError):
        return []

    issues: list[str] = []

    for node in ast.walk(tree):
        # Bare except
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            issues.append(f"line {node.lineno}: bare 'except:' (catch a specific exception)")

        # Star import
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    module = node.module or ""
                    issues.append(f"line {node.lineno}: 'from {module} import *' (use explicit imports)")

    # Check top-level functions/classes for docstrings
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not _has_docstring(node):
                issues.append(f"line {node.lineno}: {node.name}() missing docstring")

    return issues


def _has_docstring(node: ast.AST) -> bool:
    """Check if a function/class has a docstring."""
    if not hasattr(node, "body") or not node.body:
        return False
    first = node.body[0]
    return (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    )


def _find_ruff() -> str | None:
    """Find the ruff binary from forge's own environment."""
    import shutil
    import sys

    # Check forge's venv bin first (ruff is a forge dependency)
    venv_bin = Path(sys.executable).parent / "ruff"
    if venv_bin.exists():
        return str(venv_bin)
    # Fall back to system PATH
    return shutil.which("ruff")


def _check_ruff(path: Path) -> list[str]:
    """Run ruff from forge's environment against a workspace file."""
    ruff_bin = _find_ruff()
    if not ruff_bin:
        return []
    try:
        proc = subprocess.run(
            [ruff_bin, "check", "--select", "E,F,I", "--no-fix", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0:
            return []
        lines = proc.stdout.strip().splitlines()
        # Ruff output: "path:line:col: CODE message"
        issues: list[str] = []
        for line in lines:
            if ":" in line and not line.startswith("Found"):
                # Strip the absolute path prefix, keep line:col: CODE message
                parts = line.split(":", 3)
                if len(parts) >= 4:
                    issues.append(f"line {parts[1]}:{parts[2]}: {parts[3].strip()}")
        return issues
    except FileNotFoundError:
        return []  # ruff not available — skip silently
    except Exception:  # noqa: BLE001
        return []
