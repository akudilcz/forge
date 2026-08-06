"""Persist a run's diagnostic artifacts next to the workspace ``forge.db``.

Cross-run analysis needs each build's structured logs DB and full LLM
trace *after* the run, but both live in per-process files under the repo
``.forge/`` directory and are routinely pruned by later pytest sessions
(live evidence: 12 of 14 analysed builds had their
``forge.test.logs.<pid>.db`` deleted before analysis, blocking waste and
timing reports). At end of run, :class:`~backend.pipeline.flow.ForgeFlow`
calls :func:`persist_run_artifacts` to copy the process's SQLite logs
DB(s) and its ``llm_trace`` JSONL into ``<workspace>/.forge/`` next to
``forge.db``. A missing source is a loud WARN, never a silent skip.

See ``specs/11-observability.md`` §"Run artifact persistence".
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

from backend.observability.llm_trace import resolve_trace_path
from backend.server.forge_logger import forge_logger

#: SYS covers startup/shutdown/retention lifecycle — artifact persistence
#: is end-of-run lifecycle work.
_CATEGORY = "SYS"


def persist_run_artifacts(workspace: Path, trace_dir: str | None) -> list[Path]:
    """Copy this process's logs DB(s) and LLM trace into ``<workspace>/.forge``.

    Args:
        workspace: The run's workspace directory (parent of ``.forge/forge.db``).
        trace_dir: ``llm.trace_dir`` from config, or ``None`` when the flow
            has no config — reported as a loud WARN, never silently skipped.

    Returns:
        The destination paths written (for tests and log correlation).
    """
    dest_dir = workspace / ".forge"
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []

    db_paths = forge_logger.sqlite_db_paths()
    if not db_paths:
        _warn("no SQLite log sink attached — logs DB not persisted")
    for db_path in db_paths:
        dest = _persist_logs_db(Path(db_path), dest_dir)
        if dest is not None:
            copied.append(dest)

    dest = _persist_llm_trace(trace_dir, dest_dir)
    if dest is not None:
        copied.append(dest)

    if copied:
        names = ", ".join(p.name for p in copied)
        forge_logger.emit(
            "INFO", _CATEGORY, f"Run artifacts persisted to {dest_dir}: {names}"
        )
    return copied


def _persist_logs_db(src: Path, dest_dir: Path) -> Path | None:
    """Snapshot one SQLite logs DB into *dest_dir* via the backup API.

    The backup API yields a consistent snapshot of a live WAL database —
    a plain file copy of a DB mid-write would not.
    """
    if not src.exists():
        _warn(f"logs DB missing at {src} — not persisted")
        return None
    dest = dest_dir / src.name
    if src.resolve() == dest.resolve():
        return None  # already lives in the workspace .forge dir
    try:
        with sqlite3.connect(src) as source, sqlite3.connect(dest) as target:
            source.backup(target)
    except sqlite3.Error as exc:
        _warn(f"logs DB snapshot failed for {src}: {exc}")
        return None
    return dest


def _persist_llm_trace(trace_dir: str | None, dest_dir: Path) -> Path | None:
    """Copy this process's llm_trace JSONL into *dest_dir*."""
    if trace_dir is None:
        _warn("no config available — llm_trace location unknown, not persisted")
        return None
    src = resolve_trace_path(trace_dir)
    if not src.exists():
        _warn(f"llm_trace missing at {src} — not persisted")
        return None
    dest = dest_dir / src.name
    if src.resolve() == dest.resolve():
        return None
    shutil.copy2(src, dest)
    return dest


def _warn(message: str) -> None:
    forge_logger.emit("WARN", _CATEGORY, f"Run artifact persistence: {message}")
