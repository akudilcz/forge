"""Tests for backend.crew.build_env module."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.crew.build_env import (
    BuildDiagnostic,
    BuildHealth,
    DepStatus,
    PythonBazelEnvironment,
    _collect_py_imports,
    detect_build_environment,
)

# ── PythonBazelEnvironment.is_import_error ───────────────────────────────────

class TestIsImportError:
    """Tests for PythonBazelEnvironment.is_import_error()."""

    def setup_method(self) -> None:
        self.env = PythonBazelEnvironment()

    def test_module_not_found_error(self) -> None:
        """Should extract module name from ModuleNotFoundError."""
        msg = "ModuleNotFoundError: No module named 'requests'"
        assert self.env.is_import_error(msg) == "requests"

    def test_import_error(self) -> None:
        """Should extract module name from ImportError."""
        msg = "ImportError: No module named 'flask'"
        assert self.env.is_import_error(msg) == "flask"

    def test_non_import_error_returns_none(self) -> None:
        """Should return None for unrelated errors."""
        msg = "TypeError: unsupported operand type(s)"
        assert self.env.is_import_error(msg) is None

    def test_nested_module_returns_top_level(self) -> None:
        """Should return only the top-level module for dotted paths."""
        msg = "ModuleNotFoundError: No module named 'google.cloud.storage'"
        assert self.env.is_import_error(msg) == "google"

    def test_no_match_pattern_returns_none(self) -> None:
        """ImportError without the 'No module named' pattern returns None."""
        msg = "ImportError: cannot import name 'foo' from 'bar'"
        assert self.env.is_import_error(msg) is None

    def test_empty_string(self) -> None:
        """Empty error message returns None."""
        assert self.env.is_import_error("") is None


# ── PythonBazelEnvironment.fix_hint_for_missing_dep ──────────────────────────

class TestFixHint:
    """Tests for PythonBazelEnvironment.fix_hint_for_missing_dep()."""

    def setup_method(self) -> None:
        self.env = PythonBazelEnvironment()

    def test_returns_actionable_hint(self) -> None:
        """Hint should mention the module and requirements.txt."""
        hint = self.env.fix_hint_for_missing_dep("numpy")
        assert "numpy" in hint
        assert "requirements.txt" in hint

    def test_hint_for_different_module(self) -> None:
        """Should work with any module name."""
        hint = self.env.fix_hint_for_missing_dep("boto3")
        assert "boto3" in hint


# ── PythonBazelEnvironment.manifest_file ─────────────────────────────────────

class TestManifestFile:
    """Tests for PythonBazelEnvironment.manifest_file()."""

    def test_returns_requirements_txt(self) -> None:
        env = PythonBazelEnvironment()
        assert env.manifest_file() == "requirements.txt"


# ── PythonBazelEnvironment.language / build_system ───────────────────────────

class TestLanguageAndBuildSystem:
    """Tests for language() and build_system() methods."""

    def setup_method(self) -> None:
        self.env = PythonBazelEnvironment()

    def test_language(self) -> None:
        assert self.env.language() == "python"

    def test_build_system(self) -> None:
        assert self.env.build_system() == "bazel"


# ── PythonBazelEnvironment.check_health ──────────────────────────────────────

class TestCheckHealth:
    """Tests for PythonBazelEnvironment.check_health()."""

    def setup_method(self) -> None:
        self.env = PythonBazelEnvironment()

    @patch("backend.crew.build_env._collect_py_imports")
    @patch("backend.crew.bazel_gen._parse_requirements")
    def test_with_missing_deps(
        self,
        mock_reqs: MagicMock,
        mock_imports: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Should report unresolved imports as missing deps with diagnostics."""
        mock_reqs.return_value = {"flask": "@pip//flask"}
        mock_imports.return_value = {"flask", "requests", "numpy"}

        health = self.env.check_health(tmp_path)

        assert health.language == "python"
        assert health.build_system == "bazel"

        resolved_names = {d.name for d in health.deps if d.resolved}
        missing_names = {d.name for d in health.missing_deps}

        assert "flask" in resolved_names
        assert "requests" in missing_names
        assert "numpy" in missing_names

        # Should have an error diagnostic about unresolved imports
        assert health.has_errors
        assert any("Unresolved imports" in d.message for d in health.diagnostics)

    @patch("backend.crew.build_env._collect_py_imports")
    @patch("backend.crew.bazel_gen._parse_requirements")
    def test_all_resolved(
        self,
        mock_reqs: MagicMock,
        mock_imports: MagicMock,
        tmp_path: Path,
    ) -> None:
        """When all imports are resolved, no error diagnostics."""
        mock_reqs.return_value = {"flask": "@pip//flask", "requests": "@pip//requests"}
        mock_imports.return_value = {"flask", "requests"}

        health = self.env.check_health(tmp_path)

        assert len(health.missing_deps) == 0
        assert not health.has_errors
        assert all(d.resolved for d in health.deps)

    @patch("backend.crew.build_env._collect_py_imports")
    @patch("backend.crew.bazel_gen._parse_requirements")
    def test_empty_workspace(
        self,
        mock_reqs: MagicMock,
        mock_imports: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Empty workspace (no imports) should return clean health report."""
        mock_reqs.return_value = {}
        mock_imports.return_value = set()

        health = self.env.check_health(tmp_path)

        assert health.language == "python"
        assert len(health.deps) == 0
        assert len(health.diagnostics) == 0
        assert not health.has_errors

    @patch("backend.crew.build_env._collect_py_imports")
    @patch("backend.crew.bazel_gen._parse_requirements")
    def test_stdlib_imports_excluded(
        self,
        mock_reqs: MagicMock,
        mock_imports: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Standard library imports should be excluded from deps."""
        mock_reqs.return_value = {}
        mock_imports.return_value = {"os", "sys", "json", "pathlib"}

        health = self.env.check_health(tmp_path)

        assert len(health.deps) == 0
        assert not health.has_errors

    @patch("backend.crew.build_env._collect_py_imports")
    @patch("backend.crew.bazel_gen._parse_requirements")
    def test_internal_imports_excluded(
        self,
        mock_reqs: MagicMock,
        mock_imports: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Internal workspace imports (src, tests, etc.) should be excluded."""
        mock_reqs.return_value = {}
        mock_imports.return_value = {"src", "tests", "tracing", "conftest"}

        health = self.env.check_health(tmp_path)

        assert len(health.deps) == 0
        assert not health.has_errors

    @patch("backend.crew.build_env._collect_py_imports")
    @patch("backend.crew.bazel_gen._parse_requirements")
    def test_build_files_collected(
        self,
        mock_reqs: MagicMock,
        mock_imports: MagicMock,
        tmp_path: Path,
    ) -> None:
        """BUILD.bazel files should be read into build_files."""
        mock_reqs.return_value = {}
        mock_imports.return_value = set()

        build_dir = tmp_path / "pkg"
        build_dir.mkdir()
        build_file = build_dir / "BUILD.bazel"
        build_file.write_text('py_library(name = "pkg")')

        health = self.env.check_health(tmp_path)

        assert "pkg/BUILD.bazel" in health.build_files
        assert "py_library" in health.build_files["pkg/BUILD.bazel"]

    @patch("backend.crew.build_env._collect_py_imports")
    @patch("backend.crew.bazel_gen._parse_requirements")
    def test_hyphenated_package_normalization(
        self,
        mock_reqs: MagicMock,
        mock_imports: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Package names with hyphens should be normalized to underscores for matching."""
        mock_reqs.return_value = {"my_package": "@pip//my_package"}
        mock_imports.return_value = {"my_package"}

        health = self.env.check_health(tmp_path)

        resolved_names = {d.name for d in health.deps if d.resolved}
        assert "my_package" in resolved_names

    @patch("backend.crew.build_env._collect_py_imports")
    @patch("backend.crew.bazel_gen._parse_requirements")
    def test_missing_dep_fix_hints(
        self,
        mock_reqs: MagicMock,
        mock_imports: MagicMock,
        tmp_path: Path,
    ) -> None:
        """Missing deps should include fix hints."""
        mock_reqs.return_value = {}
        mock_imports.return_value = {"pandas"}

        health = self.env.check_health(tmp_path)

        missing = health.missing_deps
        assert len(missing) == 1
        assert missing[0].name == "pandas"
        assert "requirements.txt" in missing[0].fix_hint


# ── detect_build_environment ─────────────────────────────────────────────────

class TestDetectBuildEnvironment:
    """Tests for detect_build_environment()."""

    def test_module_bazel_present(self, tmp_path: Path) -> None:
        """Should detect PythonBazelEnvironment when MODULE.bazel exists."""
        (tmp_path / "MODULE.bazel").write_text("")
        env = detect_build_environment(tmp_path)
        assert isinstance(env, PythonBazelEnvironment)

    def test_workspace_file_present(self, tmp_path: Path) -> None:
        """Should detect PythonBazelEnvironment when WORKSPACE exists."""
        (tmp_path / "WORKSPACE").write_text("")
        env = detect_build_environment(tmp_path)
        assert isinstance(env, PythonBazelEnvironment)

    def test_requirements_txt_present(self, tmp_path: Path) -> None:
        """Should detect PythonBazelEnvironment when requirements.txt exists."""
        (tmp_path / "requirements.txt").write_text("flask==2.0\n")
        env = detect_build_environment(tmp_path)
        assert isinstance(env, PythonBazelEnvironment)

    def test_no_build_files_returns_none(self, tmp_path: Path) -> None:
        """Should return None when no recognized build files exist."""
        (tmp_path / "README.md").write_text("hello")
        env = detect_build_environment(tmp_path)
        assert env is None

    def test_empty_directory_returns_none(self, tmp_path: Path) -> None:
        """Empty directory should return None."""
        env = detect_build_environment(tmp_path)
        assert env is None

    def test_bazel_takes_priority(self, tmp_path: Path) -> None:
        """When both MODULE.bazel and requirements.txt exist, Bazel should be detected."""
        (tmp_path / "MODULE.bazel").write_text("")
        (tmp_path / "requirements.txt").write_text("flask==2.0\n")
        env = detect_build_environment(tmp_path)
        assert isinstance(env, PythonBazelEnvironment)


# ── _collect_py_imports ──────────────────────────────────────────────────────

class TestCollectPyImports:
    """Tests for _collect_py_imports()."""

    def test_finds_import_statements(self, tmp_path: Path) -> None:
        """Should find top-level names from import and from-import statements."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text(
            "import os\n"
            "import json\n"
            "from pathlib import Path\n"
            "from requests.auth import HTTPBasicAuth\n"
        )
        imports = _collect_py_imports(tmp_path)
        assert "os" in imports
        assert "json" in imports
        assert "pathlib" in imports
        assert "requests" in imports

    def test_skips_pycache(self, tmp_path: Path) -> None:
        """Should skip __pycache__ directories."""
        src = tmp_path / "src"
        cache = src / "__pycache__"
        cache.mkdir(parents=True)
        (cache / "cached.py").write_text("import secret_module\n")
        # Also a regular file to ensure we scan src/
        (src / "real.py").write_text("import os\n")

        imports = _collect_py_imports(tmp_path)
        assert "secret_module" not in imports
        assert "os" in imports

    def test_handles_syntax_errors(self, tmp_path: Path) -> None:
        """Should skip files with syntax errors without crashing."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "good.py").write_text("import json\n")
        (src / "bad.py").write_text("def foo(\n")  # syntax error

        imports = _collect_py_imports(tmp_path)
        assert "json" in imports

    def test_empty_dir(self, tmp_path: Path) -> None:
        """Empty workspace (no src/ or tests/) should return empty set."""
        imports = _collect_py_imports(tmp_path)
        assert imports == set()

    def test_scans_tests_directory(self, tmp_path: Path) -> None:
        """Should also scan tests/ directory."""
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_foo.py").write_text("import pytest\nfrom unittest.mock import patch\n")

        imports = _collect_py_imports(tmp_path)
        assert "pytest" in imports
        assert "unittest" in imports

    def test_relative_imports_ignored(self, tmp_path: Path) -> None:
        """Relative imports (from . import x) should be ignored."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "mod.py").write_text("from . import sibling\nfrom .sub import helper\n")

        imports = _collect_py_imports(tmp_path)
        assert "sibling" not in imports
        assert "sub" not in imports

    def test_dotted_import_returns_top_level(self, tmp_path: Path) -> None:
        """import a.b.c should return only 'a'."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "deep.py").write_text("import google.cloud.storage\n")

        imports = _collect_py_imports(tmp_path)
        assert "google" in imports
        # Should not have the full dotted path
        assert "google.cloud.storage" not in imports


# ── BuildHealth properties ───────────────────────────────────────────────────

class TestBuildHealth:
    """Tests for BuildHealth dataclass properties."""

    def test_missing_deps_filters_unresolved(self) -> None:
        """missing_deps should return only unresolved dependencies."""
        health = BuildHealth(
            language="python",
            build_system="bazel",
            deps=[
                DepStatus(name="flask", resolved=True),
                DepStatus(name="requests", resolved=False),
                DepStatus(name="numpy", resolved=False),
                DepStatus(name="pytest", resolved=True),
            ],
        )
        missing = health.missing_deps
        assert len(missing) == 2
        assert {d.name for d in missing} == {"requests", "numpy"}

    def test_missing_deps_empty_when_all_resolved(self) -> None:
        """missing_deps should be empty when all deps are resolved."""
        health = BuildHealth(
            language="python",
            build_system="bazel",
            deps=[DepStatus(name="flask", resolved=True)],
        )
        assert health.missing_deps == []

    def test_missing_deps_empty_no_deps(self) -> None:
        """missing_deps should be empty when there are no deps."""
        health = BuildHealth(language="python", build_system="bazel")
        assert health.missing_deps == []

    def test_has_errors_true(self) -> None:
        """has_errors should be True when there's an error-level diagnostic."""
        health = BuildHealth(
            language="python",
            build_system="bazel",
            diagnostics=[
                BuildDiagnostic(level="error", category="dependency", message="missing X"),
            ],
        )
        assert health.has_errors is True

    def test_has_errors_false_warn_only(self) -> None:
        """has_errors should be False with only warnings."""
        health = BuildHealth(
            language="python",
            build_system="bazel",
            diagnostics=[
                BuildDiagnostic(level="warn", category="config", message="optional thing"),
            ],
        )
        assert health.has_errors is False

    def test_has_errors_false_empty(self) -> None:
        """has_errors should be False with no diagnostics."""
        health = BuildHealth(language="python", build_system="bazel")
        assert health.has_errors is False

    def test_has_errors_mixed_levels(self) -> None:
        """has_errors should be True even with a mix of levels."""
        health = BuildHealth(
            language="python",
            build_system="bazel",
            diagnostics=[
                BuildDiagnostic(level="info", category="toolchain", message="using python 3.12"),
                BuildDiagnostic(level="warn", category="config", message="no lockfile"),
                BuildDiagnostic(level="error", category="dependency", message="missing X"),
            ],
        )
        assert health.has_errors is True
