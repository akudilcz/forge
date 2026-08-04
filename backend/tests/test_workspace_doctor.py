"""Tests for backend.tools.workspace_doctor — build environment diagnostics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from backend.tools.workspace_doctor import (
    WorkspaceDoctorTool,
    _check_build_config,
    _check_build_files,
    _check_deps,
    _check_env,
    _check_tool_availability,
    _collect_imports,
    _internal_modules,
    _parse_requirements_simple,
    _stdlib_modules,
)

# ── _parse_requirements_simple ──────────────────────────────────────────────


class TestParseRequirements:
    def test_parses_pinned_packages(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text(
            "numpy==1.26.0\npandas>=2.0\n"
        )
        result = _parse_requirements_simple(tmp_path)
        assert "numpy" in result
        assert "pandas" in result
        assert result["numpy"] == "numpy==1.26.0"

    def test_skips_comments_and_blanks(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text(
            "# comment\n\nnumpy==1.0\n"
        )
        result = _parse_requirements_simple(tmp_path)
        assert len(result) == 1
        assert "numpy" in result

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert _parse_requirements_simple(tmp_path) == {}

    def test_normalises_dashes_to_underscores(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("scikit-learn==1.0\n")
        result = _parse_requirements_simple(tmp_path)
        assert "scikit_learn" in result


# ── _collect_imports ────────────────────────────────────────────────────────


class TestCollectImports:
    def test_finds_import_and_from_import(self, tmp_path: Path) -> None:
        (tmp_path / "foo.py").write_text(
            "import numpy\nfrom pandas import DataFrame\n"
        )
        result = _collect_imports(tmp_path)
        assert "numpy" in result
        assert "pandas" in result

    def test_skips_pycache(self, tmp_path: Path) -> None:
        cache = tmp_path / "__pycache__"
        cache.mkdir()
        (cache / "foo.py").write_text("import secret_module\n")
        assert _collect_imports(tmp_path) == set()

    def test_handles_syntax_errors(self, tmp_path: Path) -> None:
        (tmp_path / "broken.py").write_text("def bad(\n")
        result = _collect_imports(tmp_path)
        assert result == set()

    def test_nonexistent_dir_returns_empty(self, tmp_path: Path) -> None:
        assert _collect_imports(tmp_path / "nope") == set()


# ── _internal_modules ───────────────────────────────────────────────────────


class TestInternalModules:
    def test_includes_standard_internal_names(self, tmp_path: Path) -> None:
        result = _internal_modules(tmp_path)
        assert "src" in result
        assert "tests" in result
        assert "tracing" in result

    def test_includes_packages_with_init(self, tmp_path: Path) -> None:
        pkg = tmp_path / "mylib"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        result = _internal_modules(tmp_path)
        assert "mylib" in result


# ── _check_deps ─────────────────────────────────────────────────────────────


class TestCheckDeps:
    def test_detects_missing_dependency(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("pytest==9.0\n")
        src = tmp_path / "src"
        src.mkdir()
        (src / "core.py").write_text("import numpy\n")

        result = _check_deps(tmp_path)
        assert result["missing_count"] == 1
        assert result["missing"][0]["module"] == "numpy"
        assert "requirements.txt" in result["missing"][0]["fix"]

    def test_resolved_deps_shown(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("numpy==1.26.0\n")
        src = tmp_path / "src"
        src.mkdir()
        (src / "core.py").write_text("import numpy\n")

        result = _check_deps(tmp_path)
        assert result["resolved_count"] == 1
        assert result["missing_count"] == 0

    def test_stdlib_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("")
        src = tmp_path / "src"
        src.mkdir()
        (src / "core.py").write_text("import os\nimport json\n")

        result = _check_deps(tmp_path)
        assert result["missing_count"] == 0

    def test_internal_modules_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("")
        src = tmp_path / "src"
        src.mkdir()
        (src / "core.py").write_text("from src.utils import helper\n")

        result = _check_deps(tmp_path)
        assert result["missing_count"] == 0


# ── _check_build_files ──────────────────────────────────────────────────────


class TestCheckBuildFiles:
    def test_reads_build_files(self, tmp_path: Path) -> None:
        build = tmp_path / "BUILD.bazel"
        build.write_text('py_library(name = "foo", deps = ["@pip//numpy"])\n')

        result = _check_build_files(tmp_path)
        assert result["count"] == 1
        assert "@pip//numpy" in result["files"]["BUILD.bazel"]["deps"]

    def test_no_build_files(self, tmp_path: Path) -> None:
        result = _check_build_files(tmp_path)
        assert result["count"] == 0


# ── Full tool execution ────────────────────────────────────────────────────


class TestWorkspaceDoctorTool:
    def test_all_check_returns_all_sections(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("numpy==1.26.0\n")
        src = tmp_path / "src"
        src.mkdir()
        (src / "core.py").write_text("import numpy\nimport scipy\n")

        tool = WorkspaceDoctorTool(str(tmp_path))
        raw = tool._execute(check="all")
        report = json.loads(raw)

        assert "dependencies" in report
        assert "build_files" in report
        assert "environment" in report

        # scipy should show as missing
        deps = report["dependencies"]
        missing_names = [m["module"] for m in deps["missing"]]
        assert "scipy" in missing_names

    def test_deps_only_check(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("")
        tool = WorkspaceDoctorTool(str(tmp_path))
        raw = tool._execute(check="deps")
        report = json.loads(raw)

        assert "dependencies" in report
        assert "build_files" not in report

    def test_env_only_check(self, tmp_path: Path) -> None:
        """check='env' returns environment section only, no deps or build."""
        tool = WorkspaceDoctorTool(str(tmp_path))
        raw = tool._execute(check="env")
        report = json.loads(raw)

        assert "environment" in report
        assert "dependencies" not in report
        assert "build_files" not in report

    def test_build_only_check(self, tmp_path: Path) -> None:
        """check='build' returns build_files only."""
        tool = WorkspaceDoctorTool(str(tmp_path))
        raw = tool._execute(check="build")
        report = json.loads(raw)

        assert "build_files" in report
        assert "dependencies" not in report
        assert "environment" not in report


# ── _check_env ──────────────────────────────────────────────────────────────


class TestCheckEnv:
    def test_returns_dict_with_checks_key(self, tmp_path: Path) -> None:
        result = _check_env(tmp_path)
        assert isinstance(result, dict)
        assert "checks" in result

    def test_checks_is_a_list(self, tmp_path: Path) -> None:
        result = _check_env(tmp_path)
        assert isinstance(result["checks"], list)

    def test_contains_coverage_check(self, tmp_path: Path) -> None:
        result = _check_env(tmp_path)
        check_names = [c["check"] for c in result["checks"]]
        assert "coverage.py" in check_names

    def test_contains_bazel_check(self, tmp_path: Path) -> None:
        result = _check_env(tmp_path)
        check_names = [c["check"] for c in result["checks"]]
        assert "bazel" in check_names

    def test_contains_requirements_check(self, tmp_path: Path) -> None:
        result = _check_env(tmp_path)
        check_names = [c["check"] for c in result["checks"]]
        assert "requirements.txt" in check_names

    def test_contains_module_bazel_when_present(self, tmp_path: Path) -> None:
        (tmp_path / "MODULE.bazel").write_text('pip.parse(hub_name = "pip")')
        result = _check_env(tmp_path)
        check_names = [c["check"] for c in result["checks"]]
        assert "MODULE.bazel" in check_names


# ── _check_tool_availability ────────────────────────────────────────────────


class TestCheckToolAvailability:
    def test_coverage_found_returns_available_with_path(self) -> None:
        issues: list[dict[str, Any]] = []
        with patch("shutil.which", side_effect=lambda cmd: "/usr/bin/coverage" if cmd == "coverage" else None):
            _check_tool_availability(issues)
        cov = next(c for c in issues if c["check"] == "coverage.py")
        assert cov["status"] == "available"
        assert cov["path"] == "/usr/bin/coverage"

    def test_coverage_not_found(self) -> None:
        issues: list[dict[str, Any]] = []
        with patch("shutil.which", return_value=None):
            _check_tool_availability(issues)
        cov = next(c for c in issues if c["check"] == "coverage.py")
        assert cov["status"] == "not found"
        assert "path" not in cov

    def test_bazel_found_returns_available(self) -> None:
        issues: list[dict[str, Any]] = []
        with patch("shutil.which", side_effect=lambda cmd: "/usr/bin/bazel" if cmd == "bazel" else None):
            _check_tool_availability(issues)
        baz = next(c for c in issues if c["check"] == "bazel")
        assert baz["status"] == "available"
        assert baz["path"] == "/usr/bin/bazel"

    def test_bazel_not_found(self) -> None:
        issues: list[dict[str, Any]] = []
        with patch("shutil.which", return_value=None):
            _check_tool_availability(issues)
        baz = next(c for c in issues if c["check"] == "bazel")
        assert baz["status"] == "not found"

    def test_both_tools_checked(self) -> None:
        """Always emits both a coverage.py and a bazel check entry."""
        issues: list[dict[str, Any]] = []
        with patch("shutil.which", return_value=None):
            _check_tool_availability(issues)
        check_names = [c["check"] for c in issues]
        assert "coverage.py" in check_names
        assert "bazel" in check_names
        assert len(issues) == 2


# ── _check_build_config ─────────────────────────────────────────────────────


class TestCheckBuildConfig:
    def test_requirements_exists_status(self, tmp_path: Path) -> None:
        (tmp_path / "requirements.txt").write_text("numpy==1.0\n")
        issues: list[dict[str, Any]] = []
        _check_build_config(tmp_path, issues)
        req = next(c for c in issues if c["check"] == "requirements.txt")
        assert req["status"] == "exists"
        assert "bazel sandbox" in req["note"].lower() or "bazel" in req["note"]

    def test_requirements_missing_status(self, tmp_path: Path) -> None:
        issues: list[dict[str, Any]] = []
        _check_build_config(tmp_path, issues)
        req = next(c for c in issues if c["check"] == "requirements.txt")
        assert req["status"] == "MISSING"

    def test_module_bazel_with_pip(self, tmp_path: Path) -> None:
        (tmp_path / "MODULE.bazel").write_text(
            'pip.parse(hub_name = "pip", requirements_lock = "requirements.txt")\n'
        )
        issues: list[dict[str, Any]] = []
        _check_build_config(tmp_path, issues)
        mod = next(c for c in issues if c["check"] == "MODULE.bazel")
        assert mod["status"] == "ok"
        assert mod["has_pip_extension"] is True

    def test_module_bazel_without_pip(self, tmp_path: Path) -> None:
        (tmp_path / "MODULE.bazel").write_text(
            'bazel_dep(name = "myproject", version = "1.0")\n'
        )
        issues: list[dict[str, Any]] = []
        _check_build_config(tmp_path, issues)
        mod = next(c for c in issues if c["check"] == "MODULE.bazel")
        assert mod["status"] == "MISSING pip setup"
        assert mod["has_pip_extension"] is False

    def test_module_bazel_absent_no_check_added(self, tmp_path: Path) -> None:
        """When MODULE.bazel doesn't exist, no MODULE.bazel check is added."""
        issues: list[dict[str, Any]] = []
        _check_build_config(tmp_path, issues)
        check_names = [c["check"] for c in issues]
        assert "MODULE.bazel" not in check_names

    def test_requirements_exists_note_mentions_sandbox(self, tmp_path: Path) -> None:
        """The note for an existing requirements.txt mentions sandbox/host difference."""
        (tmp_path / "requirements.txt").write_text("requests==2.31\n")
        issues: list[dict[str, Any]] = []
        _check_build_config(tmp_path, issues)
        req = next(c for c in issues if c["check"] == "requirements.txt")
        assert "sandbox" in req["note"].lower()

    def test_requirements_missing_note_mentions_no_packages(self, tmp_path: Path) -> None:
        """The note for a missing requirements.txt mentions NO pip packages."""
        issues: list[dict[str, Any]] = []
        _check_build_config(tmp_path, issues)
        req = next(c for c in issues if c["check"] == "requirements.txt")
        assert "no pip packages" in req["note"].lower()


# ── _stdlib_modules ─────────────────────────────────────────────────────────


class TestStdlibModules:
    def test_returns_a_set(self) -> None:
        result = _stdlib_modules()
        assert isinstance(result, set)

    def test_contains_known_stdlib(self) -> None:
        result = _stdlib_modules()
        assert "os" in result
        assert "sys" in result
        assert "json" in result
        assert "pathlib" in result
        assert "collections" in result

    def test_does_not_contain_third_party(self) -> None:
        result = _stdlib_modules()
        assert "numpy" not in result
        assert "pandas" not in result
        assert "requests" not in result
        assert "flask" not in result

    def test_not_empty(self) -> None:
        result = _stdlib_modules()
        assert len(result) > 20
