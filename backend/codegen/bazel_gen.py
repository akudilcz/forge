"""Bazel workspace generator for Phase 12 generated code.

Creates MODULE.bazel, .bazelrc, and BUILD.bazel scaffolding for the
generated workspace.  BUILD files are always regenerated and pip deps
are auto-detected by scanning imports against requirements.txt.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from backend.codegen.known_modules import (
    STDLIB_MODULES as _STDLIB_MODULES,
)
from backend.codegen.known_modules import (
    WORKSPACE_MODULES as _INTERNAL_MODULES,
)
from backend.server.forge_logger import forge_logger


def _parse_requirements(workspace: Path) -> dict[str, str]:
    """Return a mapping of package-name → pip label from requirements.txt.

    Only packages that are NOT part of the stdlib and NOT internal
    workspace packages (src, tracing, tests) are included.
    The pip label follows the ``@pip//package`` convention used by
    rules_python.
    """
    reqs_path = workspace / "requirements.txt"
    if not reqs_path.exists():
        return {}

    mapping: dict[str, str] = {}
    for line in reqs_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # e.g. "numpy==2.3.1" → package name "numpy"
        match = re.match(r"([A-Za-z0-9_-]+)", line)
        if not match:
            continue
        pkg = match.group(1).lower().replace("-", "_")
        if pkg not in _STDLIB_MODULES:
            mapping[pkg] = f'@pip//{pkg}'
    return mapping


def _scan_imports(py_file: Path) -> set[str]:
    """Extract top-level import names from a Python file.

    Returns the set of root module names (e.g. ``numpy`` from
    ``import numpy`` or ``from numpy.linalg import norm``).
    Falls back to regex if the file has syntax errors.
    """
    source = py_file.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(py_file))
    except SyntaxError:
        return _scan_imports_regex(source)

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                names.add(node.module.split(".")[0])
    return names


def _scan_imports_regex(source: str) -> set[str]:
    """Regex fallback for files with syntax errors."""
    names: set[str] = set()
    for m in re.finditer(r"^\s*(?:from|import)\s+([A-Za-z_]\w*)", source, re.MULTILINE):
        names.add(m.group(1))
    return names


def _pip_deps_for_files(
    py_files: list[Path],
    pip_packages: dict[str, str],
) -> tuple[list[str], set[str]]:
    """Return (sorted dep labels, unresolved import names) for *py_files*."""
    all_imports: set[str] = set()
    for pf in py_files:
        all_imports |= _scan_imports(pf)

    labels: set[str] = set()
    unresolved: set[str] = set()
    for imp in all_imports:
        normalised = imp.lower().replace("-", "_")
        if normalised in pip_packages:
            labels.add(pip_packages[normalised])
        elif normalised not in _STDLIB_MODULES and normalised not in _INTERNAL_MODULES:
            unresolved.add(normalised)
    return sorted(labels), unresolved


def init_bazel_workspace(workspace: Path) -> None:
    """Create bazel config and BUILD files for the workspace.

    Called at the start of Phase 12 AND after each slice completes.
    One-time files (MODULE.bazel, .bazelrc, requirements.txt) are
    skip-if-exists.  BUILD files are always regenerated because the
    agent may have added new source or test files.  Pip deps are
    auto-detected by scanning imports against requirements.txt.
    """
    _write_module_bazel(workspace)
    _write_bazelrc(workspace)
    _write_root_build(workspace)
    _write_requirements(workspace)
    _write_conftest(workspace)
    _write_tracing_package(workspace)
    _write_src_build(workspace)
    _write_tests_build(workspace)
    _report_unresolved_deps(workspace)
    forge_logger.emit("INFO", "BZEL", "Bazel workspace initialised")


def _report_unresolved_deps(workspace: Path) -> None:
    """Warn about imports that aren't in requirements.txt."""
    pip_packages = _parse_requirements(workspace)
    all_py = list((workspace / "src").rglob("*.py")) + list((workspace / "tests").rglob("*.py"))
    _, unresolved = _pip_deps_for_files(all_py, pip_packages)
    if unresolved:
        forge_logger.emit(
            "WARN", "BZEL",
            f"Unresolved imports (not in requirements.txt): {', '.join(sorted(unresolved))}. "
            f"These will cause ModuleNotFoundError in bazel sandbox. "
            f"Fix: add them to requirements.txt with version pins.",
        )


# ── File writers ─────────────────────────────────────────────────────────────


def _write_module_bazel(workspace: Path) -> None:
    """Write MODULE.bazel with rules_python, pip, and coverage tool.

    Regenerates if the existing file is missing pip setup (stale from
    a previous version that didn't configure pip).
    """
    path = workspace / "MODULE.bazel"
    if path.exists() and "pip" in path.read_text(encoding="utf-8"):
        return
    path.write_text(
        'module(name = "generated_project", version = "0.1.0")\n'
        '\n'
        'bazel_dep(name = "rules_python", version = "1.7.0")\n'
        '\n'
        'python = use_extension(\n'
        '    "@rules_python//python/extensions:python.bzl", "python",\n'
        ')\n'
        'python.toolchain(\n'
        '    python_version = "3.12",\n'
        '    configure_coverage_tool = True,\n'
        ')\n'
        '\n'
        'pip = use_extension(\n'
        '    "@rules_python//python/extensions:pip.bzl", "pip",\n'
        ')\n'
        'pip.parse(\n'
        '    hub_name = "pip",\n'
        '    python_version = "3.12",\n'
        '    requirements_lock = "//:requirements.txt",\n'
        ')\n'
        'use_repo(pip, "pip")\n',
        encoding="utf-8",
    )
    _write_coverage_tool(workspace)


def _write_coverage_tool(workspace: Path) -> None:
    """Create a coverage_tool wrapper that uses the host's coverage.py.

    Bazel's built-in Python coverage under-reports class method bodies.
    Pointing ``PYTHON_COVERAGE`` at a known-good coverage.py binary
    (from the host venv) fixes this.
    """
    import shutil  # noqa: PLC0415

    host_coverage = shutil.which("coverage")
    if not host_coverage:
        return

    # Resolve to the actual coverage package directory
    cov_rc = workspace / ".coveragerc"
    if not cov_rc.exists():
        cov_rc.write_text(
            "[run]\n"
            "branch = true\n"
            "relative_files = true\n",
            encoding="utf-8",
        )

    # Write a wrapper script that bazel calls for coverage
    tool_dir = workspace / "tools"
    tool_dir.mkdir(exist_ok=True)
    wrapper = tool_dir / "coverage.sh"
    wrapper.write_text(
        "#!/bin/bash\n"
        f'exec "{host_coverage}" "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)


def _write_bazelrc(workspace: Path) -> None:
    """Write .bazelrc with sensible defaults."""
    path = workspace / ".bazelrc"
    if path.exists():
        return
    # Point PYTHON_COVERAGE at the host's coverage.py to bypass
    # bazel's under-reporting of class method bodies.
    cov_tool = ""
    wrapper = workspace / "tools" / "coverage.sh"
    if wrapper.exists():
        cov_tool = (
            "\n"
            "# Use host coverage.py for accurate instrumentation\n"
            f'coverage --test_env=PYTHON_COVERAGE={wrapper}\n'
        )

    path.write_text(
        "# Test output\n"
        "test --test_output=errors\n"
        "test --verbose_failures\n"
        "test --nocache_test_results\n"
        "\n"
        "# Coverage — instrument src/ and tracing/ (not just tests/)\n"
        "coverage --combined_report=lcov\n"
        "coverage --nocache_test_results\n"
        'coverage --instrumentation_filter="//src[/:],//tracing[/:]"\n'
        f"{cov_tool}",
        encoding="utf-8",
    )


def _write_root_build(workspace: Path) -> None:
    """Write root BUILD.bazel."""
    path = workspace / "BUILD.bazel"
    if path.exists():
        return
    path.write_text(
        '# Root BUILD file\n'
        'exports_files(["requirements.txt"])\n',
        encoding="utf-8",
    )


def _write_requirements(workspace: Path) -> None:
    """Write a minimal requirements.txt if none exists.

    Code generation agents add project-specific deps during code generation.
    """
    path = workspace / "requirements.txt"
    if path.exists():
        return
    path.write_text(
        "# Resolved lock file for Python 3.12 — pytest + coverage\n"
        "# Regenerate: uv pip compile requirements.in --python-version 3.12\n"
        "coverage==7.13.5\n"
        "iniconfig==2.3.0\n"
        "packaging==26.0\n"
        "pluggy==1.6.0\n"
        "pygments==2.19.2\n"
        "pytest==9.0.2\n",
        encoding="utf-8",
    )


def _write_conftest(workspace: Path) -> None:
    """Write tests/conftest.py that directs JUnit XML to bazel's output.

    Bazel sets XML_OUTPUT_FILE for each test target. This conftest hooks
    pytest to write per-function JUnit XML there, so bazel-testlogs
    contains granular results instead of one-per-target stubs.
    """
    tests_dir = workspace / "tests"
    if not tests_dir.exists():
        return
    path = tests_dir / "conftest.py"
    if path.exists():
        return
    path.write_text(
        "import os\n"
        "\n"
        "\n"
        "def pytest_configure(config):\n"
        '    xml_path = os.environ.get("XML_OUTPUT_FILE")\n'
        "    if xml_path and not config.option.xmlpath:\n"
        "        config.option.xmlpath = xml_path\n",
        encoding="utf-8",
    )


def _write_tracing_package(workspace: Path) -> None:
    """Create the tracing/ package and its BUILD.bazel if not present.

    The ``@traces`` decorator lives in ``tracing/`` at the workspace root.
    Both source and test files import it, so it needs a Bazel target.
    """
    tracing_dir = workspace / "tracing"
    if not tracing_dir.exists():
        return
    build = tracing_dir / "BUILD.bazel"
    if build.exists():
        return
    build.write_text(
        'load("@rules_python//python:defs.bzl", "py_library")\n'
        "\n"
        "py_library(\n"
        '    name = "tracing",\n'
        '    srcs = glob(["**/*.py"]),\n'
        '    visibility = ["//visibility:public"],\n'
        ")\n",
        encoding="utf-8",
    )


def _write_src_build(workspace: Path) -> None:
    """Write src/BUILD.bazel — always regenerated.

    Uses a single glob-based ``py_library`` target named ``"src"``.
    Pip deps are auto-detected by scanning imports in src/*.py files.
    """
    src_dir = workspace / "src"
    if not src_dir.exists():
        return
    build = src_dir / "BUILD.bazel"

    has_tracing = (workspace / "tracing" / "__init__.py").exists()
    pip_packages = _parse_requirements(workspace)
    src_files = list(src_dir.glob("**/*.py"))
    pip_labels, _ = _pip_deps_for_files(src_files, pip_packages)

    dep_entries: list[str] = []
    if has_tracing:
        dep_entries.append('        "//tracing"')
    for label in pip_labels:
        dep_entries.append(f'        "{label}"')

    if dep_entries:
        deps_block = "    deps = [\n" + ",\n".join(dep_entries) + ",\n    ],\n"
    else:
        deps_block = ""

    build.write_text(
        'load("@rules_python//python:defs.bzl", "py_library")\n'
        "\n"
        "py_library(\n"
        '    name = "src",\n'
        '    srcs = glob(["**/*.py"]),\n'
        f"{deps_block}"
        '    visibility = ["//visibility:public"],\n'
        ")\n",
        encoding="utf-8",
    )


def _write_tests_build(workspace: Path) -> None:
    """Write tests/BUILD.bazel — always regenerated from disk state.

    One ``py_test`` per ``test_*.py`` file found on disk.  Dependencies
    always include ``//src`` and ``@pip//pytest``.  Additional pip deps
    are auto-detected by scanning each test file's imports against
    requirements.txt.
    """
    tests_dir = workspace / "tests"
    if not tests_dir.exists():
        return
    build = tests_dir / "BUILD.bazel"

    test_files = sorted(tests_dir.glob("test_*.py"))
    if not test_files:
        return

    has_conftest = (tests_dir / "conftest.py").exists()
    has_tracing = (workspace / "tracing" / "__init__.py").exists()
    pip_packages = _parse_requirements(workspace)
    lines = ['load("@rules_python//python:defs.bzl", "py_test")\n']

    for tf in test_files:
        srcs = [tf.name]
        if has_conftest:
            srcs.append("conftest.py")
        srcs_str = ", ".join(f'"{s}"' for s in srcs)

        deps = ['        "//src"']
        if has_tracing:
            deps.append('        "//tracing"')
        deps.append('        "@pip//pytest"')

        pip_labels, _ = _pip_deps_for_files([tf], pip_packages)
        for label in pip_labels:
            # pytest is already added above
            if label != "@pip//pytest":
                deps.append(f'        "{label}"')

        deps_str = ",\n".join(deps)

        lines.append(
            f"\npy_test(\n"
            f'    name = "{tf.stem}",\n'
            f"    srcs = [{srcs_str}],\n"
            f"    timeout = \"short\",\n"
            f"    deps = [\n"
            f"{deps_str},\n"
            f"    ],\n"
            f")\n"
        )

    build.write_text("".join(lines), encoding="utf-8")
