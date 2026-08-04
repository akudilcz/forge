"""Root test conftest — wires the structured observability sink so every
unit and integration test emits into a queryable SQLite log DB.

Test-run observability
----------------------
At session start we wipe ``<repo>/.forge/forge.test.logs.db`` and attach a
:class:`SQLiteLogSink` to the module-level ``forge_logger``. Every log
emit made during the run is persisted there, including structured
``category``, ``gap_type``, ``node_id``, ``agent_id``, ``model``,
``duration_ms``, and ``extras`` columns.

After the session finishes, the sink is flushed + closed and a short
terminal summary (per-level and per-category counts) is printed so
``pytest`` output ends with a pointer to the DB for deeper analysis:

    uv run python -m backend.observability.query --db .forge/forge.test.logs.db ...

(the stable name is a symlink to the most recent run's PID-keyed DB)

The sink is session-scoped and autouse so nothing in individual tests
needs to change. Other per-test capture sinks (see ``test_instrumentation.py``)
continue to coexist — ``forge_logger.add_sink`` is additive.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from backend.observability.log_sinks import SQLiteLogSink
from backend.server.forge_logger import forge_logger

_FORGE_DIR: Path = Path(__file__).resolve().parents[2] / ".forge"

# One DB per pytest process. A fixed path could not support two concurrent
# sessions: the fixture below wipes the file at session start, so starting a
# quick unit run while a multi-hour integration run is in flight used to delete
# the running session's DB out from under it, producing a wave of bogus
# "no such table: logs" fixture errors in both. Running the fast suite while a
# long one grinds away is exactly what a developer does, so the path is keyed on
# PID and the newest run is symlinked to the stable name for convenience.
TEST_LOGS_DB: Path = _FORGE_DIR / f"forge.test.logs.{os.getpid()}.db"
TEST_LOGS_LATEST: Path = _FORGE_DIR / "forge.test.logs.db"


def _pid_is_running(pid: int) -> bool:
    """True if a process with this PID exists.

    Signal 0 performs the permission and existence checks without delivering
    anything. An EPERM means the process exists but belongs to someone else,
    which still counts as alive.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _prune_dead_session_dbs() -> None:
    """Delete log DBs left by finished sessions, never one still in use.

    Liveness is decided by whether the PID in the filename is still running —
    not by mtime, which cannot distinguish a long-idle live session from an
    abandoned file, and would happily delete a running session's DB.
    """
    for path in _FORGE_DIR.glob("forge.test.logs.*.db"):
        if path == TEST_LOGS_DB:
            continue
        stem = path.name.removeprefix("forge.test.logs.").removesuffix(".db")
        if not stem.isdigit():
            continue
        if not _pid_is_running(int(stem)):
            path.unlink(missing_ok=True)


@pytest.fixture(scope="session", autouse=True)
def _wire_sqlite_log_sink() -> Iterator[SQLiteLogSink]:
    """Attach a SQLiteLogSink to forge_logger for the full test session."""
    TEST_LOGS_DB.parent.mkdir(parents=True, exist_ok=True)
    if TEST_LOGS_DB.exists():
        TEST_LOGS_DB.unlink()

    _prune_dead_session_dbs()

    sink = SQLiteLogSink(TEST_LOGS_DB)
    forge_logger.add_sink(sink)

    # Point the stable name at this run so the documented query command keeps
    # working without the caller needing to know the PID.
    try:
        TEST_LOGS_LATEST.unlink(missing_ok=True)
        TEST_LOGS_LATEST.symlink_to(TEST_LOGS_DB.name)
    except OSError:
        pass  # symlinks may be unavailable; the PID-keyed DB is still written

    try:
        yield sink
    finally:
        sink.close()
        if sink in forge_logger._sinks:
            forge_logger._sinks.remove(sink)


@pytest.hookimpl(trylast=True)
def pytest_terminal_summary(
    terminalreporter: pytest.TerminalReporter,
    exitstatus: int,
    config: pytest.Config,
) -> None:
    """Emit a per-level/per-category summary of the test-session log DB."""
    if not TEST_LOGS_DB.exists():
        terminalreporter.write_sep("=", f"observability trace: DB not found at {TEST_LOGS_DB}")
        return
    try:
        with sqlite3.connect(TEST_LOGS_DB) as conn:
            total = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
            by_level = conn.execute(
                "SELECT level, COUNT(*) FROM logs GROUP BY level ORDER BY 2 DESC"
            ).fetchall()
            by_cat = conn.execute(
                "SELECT category, COUNT(*) FROM logs GROUP BY category ORDER BY 2 DESC"
            ).fetchall()
            dropped = conn.execute("SELECT COALESCE(SUM(count),0) FROM logs_dropped").fetchone()[0]
    except sqlite3.Error as exc:
        terminalreporter.write_sep("=", f"forge.test.logs.db: query failed ({exc})")
        return

    terminalreporter.write_sep("=", "observability trace")
    terminalreporter.write_line(f"db: {TEST_LOGS_DB}")
    terminalreporter.write_line(f"total records: {total}  dropped: {dropped}")
    terminalreporter.write_line("by level:    " + "  ".join(f"{lvl}={n}" for lvl, n in by_level))
    terminalreporter.write_line("by category: " + "  ".join(f"{cat}={n}" for cat, n in by_cat))
