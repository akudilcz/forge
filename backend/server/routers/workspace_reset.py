"""Workspace reset — remove generated artifacts while preserving user files.

Used by the phases router's ``POST /phases/reset`` endpoint. Re-exported by
:mod:`backend.server.routers.phases`, which remains the public facade.
"""

from pathlib import Path

from backend.config.models import ForgeConfig


def _reset_workspace(config: ForgeConfig) -> None:
    """Remove generated artifacts from workspace, preserving user files.

    Cleans: src/, tests/, docs/, tracing/, deliverables/, build artifacts.
    Preserves: FORGE.MD, requirements.txt, .forge/, and any other user files.
    """
    import shutil

    workspace = Path(config.project.workspace_dir)
    if not workspace.is_dir():
        return

    # Directories created by phase 12 and other phases
    for dirname in ("src", "tests", "docs", "tracing", "deliverables"):
        target = workspace / dirname
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)

    # Build artifacts and coverage files
    for pattern in (
        "BUILD.bazel", "MODULE.bazel", "MODULE.bazel.lock",
        ".bazelrc", ".coveragerc", ".coverage",
        "coverage.lcov", "coverage-test-results.xml",
        "deliverables.zip",
    ):
        for f in workspace.glob(pattern):
            f.unlink(missing_ok=True)

    # Bazel symlinks
    for link in ("bazel-bin", "bazel-out", "bazel-testlogs", "bazel-workspace"):
        p = workspace / link
        if p.is_symlink() or p.is_dir():
            if p.is_symlink():
                p.unlink()
            else:
                shutil.rmtree(p, ignore_errors=True)
