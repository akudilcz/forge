"""workspace_doctor — diagnose build environment health.

Gives the mission agent visibility into the build system state:
which dependencies resolved, which imports are unresolvable, BUILD
file contents, and environment differences between coverage.py and
bazel sandbox.

This is a read-only diagnostic tool — it doesn't fix anything,
it just surfaces information the agent needs to make good decisions.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend.tools.base import ForgeTool

_NAME = "workspace_doctor"
_DESCRIPTION = (
    "Diagnose build environment health: dependency resolution, "
    "BUILD file status, bazel vs coverage.py differences. "
    "Use when tests fail with ImportError or ModuleNotFoundError, "
    "or when bazel and coverage.py results disagree."
)


class _Args(BaseModel):
    """Arguments for the workspace doctor tool."""

    check: str = Field(
        default="all",
        description=(
            "Which diagnostic to run. Options: "
            "'all' — full health check, "
            "'deps' — dependency resolution status, "
            "'build' — show BUILD file contents, "
            "'env' — environment comparison (bazel vs coverage.py)"
        ),
    )


class WorkspaceDoctorTool(ForgeTool):
    """Diagnose workspace build environment health.

    Run this when tests fail with import errors, sandbox issues,
    or when bazel and coverage.py disagree on results.
    Returns a structured JSON diagnostic report.
    """

    name: str = _NAME
    description: str = _DESCRIPTION
    args_schema: type[BaseModel] = _Args

    _workspace: str = ""

    def __init__(self, workspace: str) -> None:
        super().__init__(name=_NAME, description=_DESCRIPTION)
        object.__setattr__(self, "_workspace", workspace)

    def _execute(self, *args: Any, **kwargs: Any) -> str:
        """Dispatch entry point — forwards schema-validated args to :meth:`_diagnose`."""
        return self._diagnose(*args, **kwargs)

    def _diagnose(self, check: str = "all") -> str:
        """Run the requested diagnostics and return them as a JSON report."""
        ws = Path(self._workspace)
        report: dict[str, Any] = {}

        if check in ("all", "deps"):
            report["dependencies"] = _check_deps(ws)
        if check in ("all", "build"):
            report["build_files"] = _check_build_files(ws)
        if check in ("all", "env"):
            report["environment"] = _check_env(ws)

        return json.dumps(report, indent=2)


def _check_deps(workspace: Path) -> dict[str, Any]:
    """Check which imports are satisfied and which are missing."""
    reqs = _parse_requirements_simple(workspace)
    src_imports = _collect_imports(workspace / "src")
    test_imports = _collect_imports(workspace / "tests")
    all_imports = src_imports | test_imports

    internal = _internal_modules(workspace)
    stdlib = _stdlib_modules()

    resolved = []
    missing = []
    for imp in sorted(all_imports):
        if imp in stdlib or imp in internal:
            continue
        if imp in reqs:
            resolved.append({"module": imp, "requirement": reqs[imp]})
        else:
            # Not in requirements.txt — will fail in bazel sandbox
            used_by = []
            if imp in src_imports:
                used_by.append("src/")
            if imp in test_imports:
                used_by.append("tests/")
            missing.append({
                "module": imp,
                "used_by": used_by,
                "fix": f"Add '{imp}' (with version pin) to requirements.txt",
            })

    return {
        "resolved_count": len(resolved),
        "missing_count": len(missing),
        "resolved": resolved,
        "missing": missing,
        "requirements_file": str(workspace / "requirements.txt"),
        "note": (
            "Missing deps will cause ModuleNotFoundError in bazel sandbox "
            "but may work under coverage.py (which uses the host environment)."
            if missing else "All imports are satisfied by requirements.txt."
        ),
    }


def _check_build_files(workspace: Path) -> dict[str, Any]:
    """Read and summarize BUILD file contents."""
    build_files = {}
    for build_path in sorted(workspace.rglob("BUILD.bazel")):
        rel = str(build_path.relative_to(workspace))
        try:
            content = build_path.read_text(encoding="utf-8")
            # Extract dep lines for quick summary
            deps = re.findall(r'"(@pip//\w+)"', content)
            build_files[rel] = {
                "deps": deps,
                "content": content,
            }
        except OSError:
            build_files[rel] = {"error": "unreadable"}

    return {
        "count": len(build_files),
        "files": build_files,
    }


def _check_env(workspace: Path) -> dict[str, Any]:
    """Compare bazel sandbox vs coverage.py environments."""
    issues: list[dict[str, Any]] = []
    _check_tool_availability(issues)
    _check_build_config(workspace, issues)
    return {"checks": issues}


def _check_tool_availability(issues: list[dict[str, Any]]) -> None:
    """Check whether coverage.py and bazel are available on PATH."""
    import shutil

    coverage_bin = shutil.which("coverage")
    if coverage_bin:
        issues.append({"check": "coverage.py", "status": "available", "path": coverage_bin})
    else:
        issues.append({"check": "coverage.py", "status": "not found"})

    bazel_bin = shutil.which("bazel")
    issues.append({
        "check": "bazel",
        "status": "available" if bazel_bin else "not found",
        "path": bazel_bin or "",
    })


def _check_build_config(workspace: Path, issues: list[dict[str, Any]]) -> None:
    """Check requirements.txt and MODULE.bazel configuration."""
    reqs = workspace / "requirements.txt"
    issues.append({
        "check": "requirements.txt",
        "status": "exists" if reqs.exists() else "MISSING",
        "note": (
            "Bazel sandbox only has packages listed here. "
            "coverage.py uses host Python packages."
        ) if reqs.exists() else (
            "Without requirements.txt, bazel sandbox has NO pip packages."
        ),
    })

    module_bazel = workspace / "MODULE.bazel"
    if module_bazel.exists():
        content = module_bazel.read_text(encoding="utf-8")
        has_pip = "pip" in content
        issues.append({
            "check": "MODULE.bazel",
            "status": "ok" if has_pip else "MISSING pip setup",
            "has_pip_extension": has_pip,
        })


def _parse_requirements_simple(workspace: Path) -> dict[str, str]:
    """Parse requirements.txt into {module_name: full_line} mapping."""
    reqs_path = workspace / "requirements.txt"
    if not reqs_path.exists():
        return {}
    mapping: dict[str, str] = {}
    for line in reqs_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.match(r"([A-Za-z0-9_-]+)", line)
        if match:
            pkg = match.group(1).lower().replace("-", "_")
            mapping[pkg] = line
    return mapping


def _collect_imports(directory: Path) -> set[str]:
    """Collect all top-level import names from .py files in directory."""
    imports: set[str] = set()
    if not directory.exists():
        return imports
    for py_file in directory.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        try:
            source = py_file.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.level == 0:
                        imports.add(node.module.split(".")[0])
        except (SyntaxError, OSError):
            continue
    return imports


def _internal_modules(workspace: Path) -> set[str]:
    """Return names that are internal workspace packages."""
    internal = {"src", "tests", "tracing", "conftest"}
    # Add any directory that has an __init__.py
    for init in workspace.rglob("__init__.py"):
        if "__pycache__" not in init.parts:
            internal.add(init.parent.name)
    return internal


def _stdlib_modules() -> set[str]:
    """Return known stdlib module names."""
    return {
        "abc", "argparse", "ast", "asyncio", "base64", "bisect",
        "builtins", "collections", "contextlib", "copy", "csv",
        "dataclasses", "datetime", "decimal", "difflib", "enum",
        "errno", "fnmatch", "fractions", "functools", "gc", "glob",
        "gzip", "hashlib", "heapq", "hmac", "html", "http",
        "importlib", "inspect", "io", "itertools", "json", "logging",
        "math", "multiprocessing", "operator", "os", "pathlib",
        "pickle", "platform", "pprint", "queue", "random", "re",
        "secrets", "shutil", "signal", "socket", "sqlite3",
        "statistics", "string", "struct", "subprocess", "sys",
        "tempfile", "textwrap", "threading", "time", "timeit",
        "traceback", "types", "typing", "unittest", "urllib", "uuid",
        "warnings", "weakref", "xml", "zipfile",
    }
