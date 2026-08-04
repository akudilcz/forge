"""SQLite DDL and migrations for the observability log store."""

from __future__ import annotations

import sqlite3

CREATE_LOGS_TABLE = """
CREATE TABLE IF NOT EXISTS logs (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    ts_ms             INTEGER NOT NULL,
    level             TEXT    NOT NULL,
    category          TEXT    NOT NULL,
    msg               TEXT    NOT NULL,
    detail            TEXT,
    run_id            TEXT,
    phase             INTEGER,
    cycle             INTEGER,
    gap_type          TEXT,
    gap_id            TEXT,
    node_id           TEXT,
    agent_id          TEXT,
    call_id           TEXT,
    model             TEXT,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    tool_call_count   INTEGER,
    tool_name         TEXT,
    duration_ms       INTEGER,
    error_type        TEXT,
    extras            TEXT
)
"""

CREATE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_logs_ts       ON logs(ts_ms)",
    "CREATE INDEX IF NOT EXISTS idx_logs_level    ON logs(level, ts_ms)",
    "CREATE INDEX IF NOT EXISTS idx_logs_run      ON logs(run_id, ts_ms)",
    "CREATE INDEX IF NOT EXISTS idx_logs_phase    ON logs(run_id, phase, ts_ms)",
    "CREATE INDEX IF NOT EXISTS idx_logs_gap      ON logs(gap_type, node_id, ts_ms)",
    "CREATE INDEX IF NOT EXISTS idx_logs_category ON logs(category, ts_ms)",
    "CREATE INDEX IF NOT EXISTS idx_logs_call     ON logs(call_id)",
    "CREATE INDEX IF NOT EXISTS idx_logs_node     ON logs(node_id, ts_ms)",
)

CREATE_DROPPED_TABLE = """
CREATE TABLE IF NOT EXISTS logs_dropped (
    ts_ms  INTEGER PRIMARY KEY,
    count  INTEGER NOT NULL,
    reason TEXT    NOT NULL
)
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create logs tables and indexes if missing. Idempotent.

    Safe to call on every startup: tables and indexes use IF NOT EXISTS
    so repeated invocation is a no-op on an already-migrated DB.
    """
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(CREATE_LOGS_TABLE)
    for stmt in CREATE_INDEXES:
        conn.execute(stmt)
    conn.execute(CREATE_DROPPED_TABLE)
    conn.commit()
