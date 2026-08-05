"""Tests for backend.crew.bazel_gen — bazel workspace generation."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.crew.bazel_gen import (
    _parse_requirements,
    _pip_deps_for_files,
    _report_unresolved_deps,
    _scan_imports,
    init_bazel_workspace,
)
from backend.crew.known_modules import WORKSPACE_MODULES as _INTERNAL_MODULES


def test_init_creates_module_bazel(tmp_path: Path) -> None:
    init_bazel_workspace(tmp_path)
    module = tmp_path / "MODULE.bazel"
    assert module.exists()
    text = module.read_text()
    assert "rules_python" in text
    assert "generated_project" in text


def test_init_creates_bazelrc(tmp_path: Path) -> None:
    init_bazel_workspace(tmp_path)
    assert (tmp_path / ".bazelrc").exists()


def test_init_creates_root_build(tmp_path: Path) -> None:
    init_bazel_workspace(tmp_path)
    assert (tmp_path / "BUILD.bazel").exists()


def test_init_idempotent(tmp_path: Path) -> None:
    init_bazel_workspace(tmp_path)
    text1 = (tmp_path / "MODULE.bazel").read_text()
    init_bazel_workspace(tmp_path)
    text2 = (tmp_path / "MODULE.bazel").read_text()
    assert text1 == text2


def test_init_creates_src_build(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "foo.py").write_text("def bar(): pass\n")

    init_bazel_workspace(tmp_path)

    build = src / "BUILD.bazel"
    assert build.exists()
    text = build.read_text()
    assert "py_library" in text
    assert '"src"' in text


def test_init_creates_tests_build(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_foo.py").write_text("from src.foo import bar\n")

    init_bazel_workspace(tmp_path)

    build = tests / "BUILD.bazel"
    assert build.exists()
    text = build.read_text()
    assert "py_test" in text
    assert "test_foo" in text
    assert '"//src"' in text


def test_requirements_includes_pytest(tmp_path: Path) -> None:
    init_bazel_workspace(tmp_path)

    reqs = (tmp_path / "requirements.txt").read_text()
    assert "pytest" in reqs


def test_regenerates_src_build_on_rerun(tmp_path: Path) -> None:
    """src/BUILD.bazel should be regenerated on each run (agent may add files)."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "foo.py").write_text("def bar(): pass\n")
    build = src / "BUILD.bazel"
    build.write_text("# Stale BUILD content\n")

    init_bazel_workspace(tmp_path)

    text = build.read_text()
    assert "py_library" in text
    assert "Stale" not in text


def test_init_creates_conftest(tmp_path: Path) -> None:
    """Should create tests/conftest.py that hooks XML_OUTPUT_FILE."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("")

    init_bazel_workspace(tmp_path)

    conftest = tests / "conftest.py"
    assert conftest.exists()
    text = conftest.read_text()
    assert "XML_OUTPUT_FILE" in text
    assert "pytest_configure" in text


def test_tests_build_includes_conftest(tmp_path: Path) -> None:
    """BUILD should include conftest.py in srcs when it exists."""
    (tmp_path / "src").mkdir()
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("def test_it(): pass\n")

    init_bazel_workspace(tmp_path)

    text = (tests / "BUILD.bazel").read_text()
    assert "conftest.py" in text


def test_stale_module_bazel_regenerated(tmp_path: Path) -> None:
    """MODULE.bazel missing pip setup should be regenerated."""
    module = tmp_path / "MODULE.bazel"
    module.write_text('module(name = "old_project")\n')

    init_bazel_workspace(tmp_path)

    text = module.read_text()
    assert "pip" in text
    assert "rules_python" in text


def test_module_bazel_with_pip_not_overwritten(tmp_path: Path) -> None:
    """MODULE.bazel that already has pip setup should not be overwritten."""
    module = tmp_path / "MODULE.bazel"
    original = 'module(name = "custom")\npip.parse(hub_name = "pip")\n'
    module.write_text(original)

    init_bazel_workspace(tmp_path)

    assert module.read_text() == original


def test_tests_build_no_deps_for_unlisted_packages(tmp_path: Path) -> None:
    """Imports not in requirements.txt should not appear in BUILD deps."""
    (tmp_path / "src").mkdir()
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("import scipy\n")

    init_bazel_workspace(tmp_path)

    text = (tests / "BUILD.bazel").read_text()
    assert "scipy" not in text
    assert '"//src"' in text


def test_tests_build_auto_detects_pip_deps(tmp_path: Path) -> None:
    """Test files importing a package from requirements.txt get it as a dep."""
    (tmp_path / "src").mkdir()
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("import numpy as np\n")
    (tmp_path / "requirements.txt").write_text("numpy==2.3.1\npytest==9.0.2\n")

    init_bazel_workspace(tmp_path)

    text = (tests / "BUILD.bazel").read_text()
    assert '@pip//numpy' in text
    assert '"//src"' in text


def test_src_build_auto_detects_pip_deps(tmp_path: Path) -> None:
    """Source files importing a package from requirements.txt get it as a dep."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "planner.py").write_text("import numpy as np\n")
    (tmp_path / "requirements.txt").write_text("numpy==2.3.1\npytest==9.0.2\n")

    init_bazel_workspace(tmp_path)

    text = (src / "BUILD.bazel").read_text()
    assert '@pip//numpy' in text


def test_stdlib_imports_not_added_as_deps(tmp_path: Path) -> None:
    """Stdlib imports should never appear as pip deps."""
    (tmp_path / "src").mkdir()
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("import os\nimport math\nimport json\n")

    init_bazel_workspace(tmp_path)

    text = (tests / "BUILD.bazel").read_text()
    assert "@pip//os" not in text
    assert "@pip//math" not in text
    assert "@pip//json" not in text


def test_pip_deps_not_duplicated_with_pytest(tmp_path: Path) -> None:
    """@pip//pytest should not appear twice when test imports pytest."""
    (tmp_path / "src").mkdir()
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("import pytest\nimport numpy\n")
    (tmp_path / "requirements.txt").write_text("numpy==2.3.1\npytest==9.0.2\n")

    init_bazel_workspace(tmp_path)

    text = (tests / "BUILD.bazel").read_text()
    assert text.count("@pip//pytest") == 1
    assert '@pip//numpy' in text


def test_parse_requirements(tmp_path: Path) -> None:
    """_parse_requirements returns pip labels for non-stdlib packages."""
    reqs = tmp_path / "requirements.txt"
    reqs.write_text("numpy==2.3.1\ncoverage==7.13.5\npytest==9.0.2\n")

    mapping = _parse_requirements(tmp_path)

    assert "numpy" in mapping
    assert mapping["numpy"] == "@pip//numpy"
    assert "coverage" in mapping
    assert "pytest" in mapping


def test_scan_imports_basic(tmp_path: Path) -> None:
    """_scan_imports extracts top-level module names."""
    f = tmp_path / "example.py"
    f.write_text("import numpy as np\nfrom pathlib import Path\nimport os\n")

    names = _scan_imports(f)

    assert "numpy" in names
    assert "pathlib" in names
    assert "os" in names


def test_scan_imports_syntax_error_fallback(tmp_path: Path) -> None:
    """_scan_imports falls back to regex on syntax errors."""
    f = tmp_path / "broken.py"
    f.write_text("import numpy\ndef broken_ name():\n    pass\n")

    names = _scan_imports(f)

    assert "numpy" in names


def test_from_import_detected(tmp_path: Path) -> None:
    """from X import Y should detect X as a dep."""
    (tmp_path / "src").mkdir()
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_x.py").write_text("from numpy.linalg import norm\n")
    (tmp_path / "requirements.txt").write_text("numpy==2.3.1\npytest==9.0.2\n")

    init_bazel_workspace(tmp_path)

    text = (tests / "BUILD.bazel").read_text()
    assert '@pip//numpy' in text


# ── _pip_deps_for_files return type tests ────────────────────────────────────


def test_pip_deps_returns_resolved_and_unresolved(tmp_path: Path) -> None:
    """Return type is (sorted labels, unresolved set)."""
    f = tmp_path / "code.py"
    f.write_text("import numpy\nimport scipy\n")
    pip_packages = {"numpy": "@pip//numpy"}

    labels, unresolved = _pip_deps_for_files([f], pip_packages)

    assert labels == ["@pip//numpy"]
    assert "scipy" in unresolved


def test_pip_deps_stdlib_not_in_unresolved(tmp_path: Path) -> None:
    """Stdlib imports should not appear in the unresolved set."""
    f = tmp_path / "code.py"
    f.write_text("import os\nimport math\nimport json\n")
    pip_packages: dict[str, str] = {}

    labels, unresolved = _pip_deps_for_files([f], pip_packages)

    assert labels == []
    assert "os" not in unresolved
    assert "math" not in unresolved
    assert "json" not in unresolved


def test_pip_deps_internal_modules_not_in_unresolved(tmp_path: Path) -> None:
    """Internal modules (src, tests, tracing) should not be unresolved."""
    f = tmp_path / "code.py"
    f.write_text("import src\nimport tests\nimport tracing\nimport conftest\n")
    pip_packages: dict[str, str] = {}

    labels, unresolved = _pip_deps_for_files([f], pip_packages)

    assert labels == []
    for mod in _INTERNAL_MODULES:
        assert mod not in unresolved


def test_pip_deps_empty_file_list() -> None:
    """Empty file list returns empty results."""
    labels, unresolved = _pip_deps_for_files([], {})

    assert labels == []
    assert unresolved == set()


# ── _report_unresolved_deps tests ────────────────────────────────────────────


def test_report_unresolved_deps_warns_on_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Workspace with unresolved import logs a warning."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("import requests\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text("import pytest\n")
    reqs = tmp_path / "requirements.txt"
    reqs.write_text("pytest==9.0.2\n")

    logged: list[tuple[str, str, str]] = []
    from backend.server import forge_logger as _fl_mod
    monkeypatch.setattr(
        _fl_mod.forge_logger, "emit",
        lambda level, cat, msg, **kw: logged.append((level, cat, msg)),
    )

    _report_unresolved_deps(tmp_path)

    warn_msgs = [m for lv, _, m in logged if lv == "WARN"]
    assert any("requests" in m for m in warn_msgs)


def test_report_unresolved_deps_no_warning_when_resolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Workspace with all resolved imports logs no warning."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("import numpy\n")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text("import pytest\n")
    reqs = tmp_path / "requirements.txt"
    reqs.write_text("numpy==2.3.1\npytest==9.0.2\n")

    logged: list[tuple[str, str, str]] = []
    from backend.server import forge_logger as _fl_mod
    monkeypatch.setattr(
        _fl_mod.forge_logger, "emit",
        lambda level, cat, msg, **kw: logged.append((level, cat, msg)),
    )

    _report_unresolved_deps(tmp_path)

    warn_msgs = [m for lv, _, m in logged if lv == "WARN"]
    assert not any("Unresolved" in m for m in warn_msgs)


def test_report_unresolved_deps_no_source_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Workspace with no source files logs no warning."""
    reqs = tmp_path / "requirements.txt"
    reqs.write_text("pytest==9.0.2\n")

    logged: list[tuple[str, str, str]] = []
    from backend.server import forge_logger as _fl_mod
    monkeypatch.setattr(
        _fl_mod.forge_logger, "emit",
        lambda level, cat, msg, **kw: logged.append((level, cat, msg)),
    )

    _report_unresolved_deps(tmp_path)

    warn_msgs = [m for lv, _, m in logged if lv == "WARN"]
    assert not any("Unresolved" in m for m in warn_msgs)


# ── Shared stdlib constant (ranks 7/18 regression) ───────────────────────────


def test_stdlib_modules_include_future_and_common_stdlib() -> None:
    """Rank-18 regression: __future__ (and other stdlib omitted by the old
    hand-list) must be recognised as stdlib."""
    from backend.crew.known_modules import STDLIB_MODULES as _STDLIB_MODULES

    for mod in (
        "__future__", "datetime", "random", "unittest", "string",
        "statistics", "copy", "contextlib",
    ):
        assert mod in _STDLIB_MODULES, mod


def test_future_import_never_unresolved(tmp_path: Path) -> None:
    """Rank-18 live-run repro: 'from __future__ import annotations' fired a
    false 'add __future__ to requirements.txt' diagnostic every iteration."""
    f = tmp_path / "code.py"
    f.write_text(
        "from __future__ import annotations\n"
        "import datetime\n"
        "import random\n"
        "from unittest import mock\n"
    )
    labels, unresolved = _pip_deps_for_files([f], {})
    assert labels == []
    assert unresolved == set()


def test_report_unresolved_deps_silent_for_future(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No 'Unresolved imports' warning for stdlib-only imports."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text("from __future__ import annotations\nimport datetime\n")

    logged: list[tuple[str, str, str]] = []
    from backend.server import forge_logger as _fl_mod
    monkeypatch.setattr(
        _fl_mod.forge_logger, "emit",
        lambda level, cat, msg, **kw: logged.append((level, cat, msg)),
    )

    _report_unresolved_deps(tmp_path)

    warn_msgs = [m for lv, _, m in logged if lv == "WARN"]
    assert not any("Unresolved" in m for m in warn_msgs)
