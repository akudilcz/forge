"""PhaseStore — SQLite-backed phase state, single source of truth across restarts."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

_DEFAULT_PHASES: list[tuple[int, str]] = [
    (0,  "Initialise Project"),  # L0  → PROJECT (human-initiated)
    (1,  "Read Forge.md"),   # L1  → DOCUMENT (human-initiated)
    (2,  "Chunk Forge.md"),  # L1  → PARA          [UNCHUNKED_DOCUMENT]
    (3,  "HLR"),             # L2  → HLR           [UNCOVERED_PARA]
    (4,  "Architecture"),    # L3  → ARCHITECTURE  [UNARCHITECTED]
    (5,  "Modularisation"),  # L3  → MODULE        [UNMODULARISED]
    (6,  "Contracts"),       # L4  → CONTRACT      [UNCONTRACTED]
    (7,  "Requirements"),    # L2  → LLR           [UNREFINED_HLR]
    (8,  "Design"),          # L5  → DESIGN        [UNDESIGNED]
    (9,  "Test Suite"),      # L6  → SUITE         [UNSUITED]
    (10, "Verification"),    # L6  → CASE          [UNTESTED_HLR/LLR]
    (11, "Doco Render"),      # —   → .md docs      [no gap — deterministic render]
    (12, "Code Gen"),        # L5  → CODE+TEST     [LangGraph agents]
    (13, "Workspace Sync"),  # L5/L6 → CODE+TEST   [UNSYNCED_DESIGN + UNSYNCED_TEST]
    (14, "Deliverables"),    # —   → ZIP pack      [no gap — deterministic render]
]

VALID_STATUSES = {"pending", "active", "awaiting_approval", "complete", "skipped"}


class PhaseStore:
    """Read/write phase states from/to SQLite.

    Uses the same forge.db file as ProjectGraph so there is one database file
    and no separate session.json to get out of sync.
    """

    def __init__(self, db_path: str) -> None:
        self._db = db_path
        self._init_schema()

    # ── Schema ────────────────────────────────────────────────────────────────

    def _init_schema(self) -> None:
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS phase_states (
                    phase_number INTEGER PRIMARY KEY,
                    name         TEXT    NOT NULL,
                    status       TEXT    NOT NULL DEFAULT 'pending',
                    updated_at   TEXT    NOT NULL
                )
                """
            )
            conn.commit()
            now = datetime.now(UTC).isoformat()
            for num, name in _DEFAULT_PHASES:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO phase_states (phase_number, name, status, updated_at)
                    VALUES (?, ?, 'pending', ?)
                    """,
                    (num, name, now),
                )
            conn.commit()
            # Correct any stale phase names that differ from _DEFAULT_PHASES
            for num, name in _DEFAULT_PHASES:
                conn.execute(
                    "UPDATE phase_states SET name=?, updated_at=? WHERE phase_number=? AND name!=?",
                    (name, now, num, name),
                )
            # Remove any phase rows not in the current _DEFAULT_PHASES list
            # (handles old phases 13/14 that were removed from the schema)
            valid_nums = ",".join(str(n) for n, _ in _DEFAULT_PHASES)
            conn.execute(f"DELETE FROM phase_states WHERE phase_number NOT IN ({valid_nums})")
            conn.commit()

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_all(self) -> list[dict[str, Any]]:
        """Return all phases ordered by phase_number."""
        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT phase_number, name, status, updated_at "
                "FROM phase_states ORDER BY phase_number"
            ).fetchall()
            return [dict(r) for r in rows]

    def get(self, phase: int) -> dict[str, Any] | None:
        """Return a single phase dict, or None if not found."""
        with sqlite3.connect(self._db) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT phase_number, name, status, updated_at "
                "FROM phase_states WHERE phase_number=?",
                (phase,),
            ).fetchone()
            return dict(row) if row else None

    # ── Write ─────────────────────────────────────────────────────────────────

    def set_status(self, phase: int, status: str) -> None:
        """Update the status of a single phase."""
        if status not in VALID_STATUSES:
            raise ValueError(f"Invalid status '{status}'. Valid: {VALID_STATUSES}")
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                "UPDATE phase_states SET status=?, updated_at=? WHERE phase_number=?",
                (status, datetime.now(UTC).isoformat(), phase),
            )
            conn.commit()

    def reset_all(self) -> None:
        """Reset all phases to pending."""
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                "UPDATE phase_states SET status='pending', updated_at=?",
                (datetime.now(UTC).isoformat(),),
            )
            conn.commit()

    def reset_active_to_pending(self) -> None:
        """Reset any 'active' phases to 'pending'.

        Called at server startup: a phase that was marked 'active' when the
        server stopped is not actually running, so the UI should not show a
        spinner for it.  Phases that were 'awaiting_approval', 'complete', or
        'skipped' are left unchanged so the user can see what was accomplished.
        """
        with sqlite3.connect(self._db) as conn:
            conn.execute(
                "UPDATE phase_states SET status='pending', updated_at=? WHERE status='active'",
                (datetime.now(UTC).isoformat(),),
            )
            conn.commit()
