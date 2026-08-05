"""Build environment protocol — pluggable build system diagnostics.

Defines the interface that language-specific build environments implement.
The workspace_doctor and gap_finder talk to this protocol instead of
hardcoding Python/Bazel assumptions.

To add a new language:
1. Implement BuildEnvironment for that ecosystem
2. Register it in detect_build_environment()
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DepStatus:
    """Status of a single dependency."""

    name: str
    resolved: bool
    version: str = ""
    used_by: list[str] = field(default_factory=list)
    fix_hint: str = ""


@dataclass
class BuildDiagnostic:
    """A single diagnostic finding from the build environment."""

    level: str  # "error", "warn", "info"
    category: str  # "dependency", "config", "toolchain"
    message: str
    fix_hint: str = ""
    affected_files: list[str] = field(default_factory=list)


@dataclass
class BuildHealth:
    """Complete build environment health report."""

    language: str
    build_system: str
    deps: list[DepStatus] = field(default_factory=list)
    diagnostics: list[BuildDiagnostic] = field(default_factory=list)
    build_files: dict[str, str] = field(default_factory=dict)

    @property
    def missing_deps(self) -> list[DepStatus]:
        return [d for d in self.deps if not d.resolved]

    @property
    def has_errors(self) -> bool:
        return any(d.level == "error" for d in self.diagnostics)


class BuildEnvironment(ABC):
    """Protocol for language-specific build environment diagnostics."""

    @abstractmethod
    def language(self) -> str:
        """Return the language name (e.g. 'python', 'rust', 'go')."""

    @abstractmethod
    def build_system(self) -> str:
        """Return the build system name (e.g. 'bazel', 'cargo', 'go')."""

    @abstractmethod
    def check_health(self, workspace: Path) -> BuildHealth:
        """Run full diagnostics and return a health report."""

    @abstractmethod
    def is_import_error(self, error_message: str) -> str | None:
        """If the error is a missing-dep import error, return the module name.

        Returns None if this isn't an import/dependency error.
        """

    @abstractmethod
    def fix_hint_for_missing_dep(self, module: str) -> str:
        """Return a human-readable fix hint for a missing dependency."""

    @abstractmethod
    def manifest_file(self) -> str:
        """Return the dependency manifest filename (e.g. 'requirements.txt', 'Cargo.toml')."""


class PythonBazelEnvironment(BuildEnvironment):
    """Build environment for Python projects using Bazel + requirements.txt."""

    def language(self) -> str:
        return "python"

    def build_system(self) -> str:
        return "bazel"

    def is_import_error(self, error_message: str) -> str | None:
        if "ModuleNotFoundError" not in error_message and "ImportError" not in error_message:
            return None
        import re
        match = re.search(r"No module named '([^']+)'", error_message)
        return match.group(1).split(".")[0] if match else None

    def fix_hint_for_missing_dep(self, module: str) -> str:
        return f"Add '{module}' (with version pin) to requirements.txt"

    def manifest_file(self) -> str:
        return "requirements.txt"

    def check_health(self, workspace: Path) -> BuildHealth:
        """Check Python/Bazel build health."""
        from backend.crew.bazel_gen import _parse_requirements
        from backend.crew.known_modules import STDLIB_MODULES, WORKSPACE_MODULES

        health = BuildHealth(language="python", build_system="bazel")
        reqs = _parse_requirements(workspace)
        all_imports = _collect_py_imports(workspace)

        for imp in sorted(all_imports):
            if imp in STDLIB_MODULES or imp in WORKSPACE_MODULES:
                continue
            if imp.lower().replace("-", "_") in reqs:
                health.deps.append(DepStatus(
                    name=imp, resolved=True,
                    version=reqs.get(imp.lower().replace("-", "_"), ""),
                ))
            else:
                health.deps.append(DepStatus(
                    name=imp, resolved=False,
                    fix_hint=self.fix_hint_for_missing_dep(imp),
                ))

        if health.missing_deps:
            names = ", ".join(d.name for d in health.missing_deps)
            health.diagnostics.append(BuildDiagnostic(
                level="error",
                category="dependency",
                message=f"Unresolved imports: {names}",
                fix_hint=f"Add to {self.manifest_file()} with version pins",
                affected_files=[self.manifest_file()],
            ))

        # Include BUILD file contents
        for build_path in sorted(workspace.rglob("BUILD.bazel")):
            rel = str(build_path.relative_to(workspace))
            try:
                health.build_files[rel] = build_path.read_text(encoding="utf-8")
            except OSError:
                pass

        return health


def _collect_py_imports(workspace: Path) -> set[str]:
    """Collect all top-level import names from .py files in workspace."""
    import ast
    imports: set[str] = set()
    for subdir in ("src", "tests"):
        target = workspace / subdir
        if not target.exists():
            continue
        for py_file in target.rglob("*.py"):
            if "__pycache__" in py_file.parts:
                continue
            try:
                source = py_file.read_text(encoding="utf-8")
                tree = ast.parse(source)
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


def detect_build_environment(workspace: Path) -> BuildEnvironment | None:
    """Auto-detect the build environment from workspace files.

    Returns None if no recognized build system is found.
    Extend this function when adding new language support.
    """
    # Python + Bazel
    if (workspace / "MODULE.bazel").exists() or (workspace / "WORKSPACE").exists():
        return PythonBazelEnvironment()

    # Python + requirements.txt (no bazel)
    if (workspace / "requirements.txt").exists():
        return PythonBazelEnvironment()  # Same impl works without bazel

    # Future: Rust/Cargo
    # if (workspace / "Cargo.toml").exists():
    #     return RustCargoEnvironment()

    # Future: Go
    # if (workspace / "go.mod").exists():
    #     return GoModulesEnvironment()

    return None
