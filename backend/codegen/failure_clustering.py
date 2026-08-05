"""Failing-test clustering — turn test failures into actionable gaps.

Splits test failures into dependency/import errors (clustered by missing
module into TEST_ENV_BROKEN gaps with a fix hint) and genuine test
failures (grouped by file into FAILING_TESTS gaps with rich error
summaries). Uses the build environment protocol for language-agnostic
import-error detection.
"""

from __future__ import annotations

from typing import Any

from backend.codegen.gap_model import Gap, GapKind


def _check_failing_tests(
    gaps: list[Gap],
    test_results: list[Any],
) -> None:
    """Add FAILING_TESTS gaps grouped by file.

    Import/dependency errors that share the same root module are
    clustered into a single TEST_ENV_BROKEN gap with a fix hint,
    instead of N separate FAILING_TESTS gaps. Uses the build
    environment protocol for language-agnostic detection.
    """
    dep_errors, other_failures = _partition_dep_errors(test_results)
    _report_dep_error_clusters(gaps, dep_errors)
    _report_test_failures(gaps, other_failures)


def _partition_dep_errors(
    test_results: list[Any],
) -> tuple[list[tuple[Any, str]], list[Any]]:
    """Split failures into dependency errors vs other test failures.

    Returns (dep_errors_with_module, other_failures).
    """
    from backend.codegen.build_env import detect_build_environment

    # Try to detect the build environment for smart error classification
    build_env = None
    try:
        import os
        ws = os.environ.get("FORGE_WORKSPACE", "")
        if ws:
            from pathlib import Path
            build_env = detect_build_environment(Path(ws))
    except Exception:  # noqa: BLE001
        pass

    dep_errors: list[tuple[Any, str]] = []
    other_failures: list[Any] = []
    for result in test_results:
        if result.status not in ("failed", "error"):
            continue
        msg = (getattr(result, "error_message", "") or "") + (getattr(result, "error_detail", "") or "")
        module = build_env.is_import_error(msg) if build_env else _fallback_import_check(msg)
        if module:
            dep_errors.append((result, module))
        else:
            other_failures.append(result)
    return dep_errors, other_failures


def _fallback_import_check(msg: str) -> str | None:
    """Fallback import error detection when no build env is detected."""
    import re
    if "ModuleNotFoundError" not in msg and "ImportError" not in msg:
        return None
    match = re.search(r"No module named '([^']+)'", msg)
    return match.group(1).split(".")[0] if match else None


def _report_dep_error_clusters(
    gaps: list[Gap], dep_errors: list[tuple[Any, str]],
) -> None:
    """Cluster dependency errors by missing module into TEST_ENV_BROKEN gaps."""
    from backend.codegen.build_env import detect_build_environment

    build_env = None
    try:
        import os
        ws = os.environ.get("FORGE_WORKSPACE", "")
        if ws:
            from pathlib import Path
            build_env = detect_build_environment(Path(ws))
    except Exception:  # noqa: BLE001
        pass

    clusters: dict[str, list[str]] = {}
    for result, module in dep_errors:
        clusters.setdefault(module, []).append(result.file_path or result.test_id)

    manifest = build_env.manifest_file() if build_env else "requirements.txt"
    for module, files in clusters.items():
        unique_files = sorted(set(files))
        fix = build_env.fix_hint_for_missing_dep(module) if build_env else f"Add '{module}' to {manifest}"
        gaps.append(Gap(
            kind=GapKind.TEST_ENV_BROKEN,
            node_id="",
            file_path=manifest,
            details=f"{len(files)} test(s) across {len(unique_files)} file(s) fail with missing dependency '{module}'. {fix}",
            context={
                "missing_module": module,
                "affected_files": unique_files,
                "affected_count": len(files),
            },
        ))


def _report_test_failures(
    gaps: list[Gap], failures: list[Any],
) -> None:
    """Add FAILING_TESTS gaps for non-dependency failures, grouped by file."""
    by_file: dict[str, list[Any]] = {}
    for result in failures:
        by_file.setdefault(result.file_path, []).append(result)

    for file_path, file_failures in by_file.items():
        test_ids = [r.test_id for r in file_failures]
        error_summaries = _build_error_summaries(file_failures)
        gaps.append(Gap(
            kind=GapKind.FAILING_TESTS,
            node_id="",
            file_path=file_path,
            details=f"{len(file_failures)} failing test(s)",
            context={
                "test_ids": test_ids,
                "failing_count": len(file_failures),
                "error_summaries": error_summaries,
            },
        ))


def _build_error_summaries(failures: list[Any]) -> list[str]:
    """Build rich per-test error summaries for the agent prompt.

    Includes the full traceback so the agent can trace the root cause
    through exception chains, broad except blocks, and internal errors.
    """
    summaries: list[str] = []
    for r in failures:
        msg = getattr(r, "error_message", "") or ""
        detail = getattr(r, "error_detail", "") or ""
        label = r.test_id
        if detail:
            # Include the full traceback — agents need the complete
            # chain to diagnose issues like swallowed exceptions
            lines = [ln for ln in detail.splitlines() if ln.strip()]
            summaries.append(f"{label}: {msg}\n  " + "\n  ".join(lines))
        elif msg:
            summaries.append(f"{label}: {msg}")
        else:
            summaries.append(f"{label}: (no error detail)")
    return summaries
