"""Timing report over a FORGE observability log DB.

Answers "where did the time go?" for a build: wall-clock span per phase,
recorded operation durations by category, and the LLM hot spots (calls,
seconds, prompt tokens) grouped by gap type. Reads the ``logs`` table
written by the forge_logger sinks (``.forge/forge.test.logs.<pid>.db``).

Usage::

    uv run python -m backend.scripts.phase_timing_report <log-db-path>
"""

from __future__ import annotations

import sqlite3
import sys


def _phase_spans(conn: sqlite3.Connection) -> list[str]:
    """Wall-clock span and record count per phase."""
    rows = conn.execute(
        "SELECT phase, (MAX(ts_ms) - MIN(ts_ms)) / 1000.0, COUNT(*) "
        "FROM logs WHERE phase IS NOT NULL GROUP BY phase ORDER BY phase"
    ).fetchall()
    return [f"  phase {p}: {secs:8.1f}s  ({n} records)" for p, secs, n in rows]


def _category_durations(conn: sqlite3.Connection) -> list[str]:
    """Total recorded duration_ms by category, largest first."""
    rows = conn.execute(
        "SELECT category, SUM(duration_ms) / 1000.0, COUNT(*), AVG(duration_ms) "
        "FROM logs WHERE duration_ms IS NOT NULL "
        "GROUP BY category ORDER BY 2 DESC"
    ).fetchall()
    return [
        f"  {cat:8s} total {total:9.1f}s  n={n:5d}  avg {avg:8.0f}ms"
        for cat, total, n, avg in rows
    ]


def _llm_by_gap_type(conn: sqlite3.Connection) -> list[str]:
    """LLM time and prompt tokens per gap type — the work-volume hot spots."""
    rows = conn.execute(
        "SELECT COALESCE(gap_type, '<none>'), COUNT(*), "
        "SUM(duration_ms) / 1000.0, SUM(prompt_tokens) "
        "FROM logs WHERE category = 'LLM' "
        "GROUP BY gap_type ORDER BY 3 DESC"
    ).fetchall()
    return [
        f"  {gt:30s} n={n:5d}  {secs:9.1f}s  prompt_tok={tok if tok is not None else 0}"
        for gt, n, secs, tok in rows
    ]


def build_report(db_path: str) -> str:
    """Render the full timing report for one log DB as a printable string."""
    conn = sqlite3.connect(db_path)
    try:
        total = conn.execute(
            "SELECT COUNT(*), (MAX(ts_ms) - MIN(ts_ms)) / 1000.0 FROM logs"
        ).fetchone()
        if total[0] == 0:
            raise ValueError(f"log DB has no records: {db_path}")
        lines = [
            f"timing report: {db_path}",
            f"records={total[0]}  wall={total[1]:.1f}s",
            "phase wall spans:",
            *_phase_spans(conn),
            "recorded durations by category:",
            *_category_durations(conn),
            "LLM time by gap type:",
            *_llm_by_gap_type(conn),
        ]
        return "\n".join(lines)
    finally:
        conn.close()


def main(argv: list[str]) -> int:
    """CLI entry point: one required log-db-path argument."""
    if len(argv) != 2:
        print("usage: python -m backend.scripts.phase_timing_report <log-db-path>")
        return 2
    print(build_report(argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
