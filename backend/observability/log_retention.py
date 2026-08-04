"""Retention helper — prune old log rows.

Two bounds:
- ``max_age_days`` — drop rows older than N days.
- ``max_size_mb``  — if the DB file is larger than N MB after age pruning,
  drop the oldest rows until the file shrinks below the cap (obs #12).

Runs on startup and can be scheduled periodically.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

DEFAULT_MAX_AGE_DAYS = 3
DEFAULT_MAX_SIZE_MB = 500


def prune_old_logs(
    db_path: Path | str,
    *,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    max_size_mb: int | None = DEFAULT_MAX_SIZE_MB,
) -> int:
    """Prune rows older than ``max_age_days``, then cap file size at
    ``max_size_mb`` (or skip size cap when None).

    Returns the number of rows deleted. Safe to call while writers are
    active (WAL).
    """
    deleted = 0

    if max_age_days > 0:
        cutoff_ms = int(time.time() * 1000) - max_age_days * 86_400_000
        with sqlite3.connect(str(db_path), timeout=5.0) as conn:
            conn.execute("PRAGMA busy_timeout=5000")
            cur = conn.execute("DELETE FROM logs WHERE ts_ms < ?", (cutoff_ms,))
            deleted += cur.rowcount or 0
            conn.execute("DELETE FROM logs_dropped WHERE ts_ms < ?", (cutoff_ms,))
            conn.commit()

    # Obs #12: size cap. Prune oldest 10% at a time until size fits.
    if max_size_mb is not None and max_size_mb > 0:
        path = Path(db_path)
        while path.exists() and path.stat().st_size > max_size_mb * 1024 * 1024:
            with sqlite3.connect(str(db_path), timeout=5.0) as conn:
                conn.execute("PRAGMA busy_timeout=5000")
                total = conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
                if total < 100:
                    break
                drop = max(1, total // 10)
                cur = conn.execute(
                    "DELETE FROM logs WHERE id IN "
                    "(SELECT id FROM logs ORDER BY ts_ms ASC LIMIT ?)",
                    (drop,),
                )
                deleted += cur.rowcount or 0
                conn.execute("VACUUM")
                conn.commit()

    return deleted
