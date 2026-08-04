"""Programmatic query helpers over the observability log store.

Used by the HTTP router and by tests. Returns rows as plain dicts so
callers don't need to import LogRecord.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from backend.observability.log_record import COLUMNS

_SINCE_RE = re.compile(r"^-(\d+)\s*(s|m|h|d)$")


def _parse_since(value: str | None, *, now_ms: int) -> int | None:
    if not value:
        return None
    m = _SINCE_RE.match(value.strip())
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        multipliers = {"s": 1_000, "m": 60_000, "h": 3_600_000, "d": 86_400_000}
        return now_ms - n * multipliers[unit]
    # ISO timestamp fallback — parse with datetime
    from datetime import datetime  # noqa: PLC0415

    try:
        return int(datetime.fromisoformat(value).timestamp() * 1000)
    except ValueError as exc:
        raise ValueError(
            f"since/until must be '-5m', '-1h', '-7d' or ISO timestamp; got {value!r}"
        ) from exc


def query_logs(
    db_path: Path | str,
    *,
    level: list[str] | None = None,
    category: list[str] | None = None,
    run_id: str | None = None,
    phase: int | None = None,
    gap_type: str | None = None,
    node_id: str | None = None,
    call_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    q: str | None = None,
    limit: int = 500,
    offset: int = 0,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Query the logs DB with rich filters. Returns ``{total, records, dropped_since}``.

    ``since`` / ``until`` accept relative syntax (``-5m``, ``-1h``, ``-7d``)
    or ISO timestamps.
    """
    import time  # noqa: PLC0415

    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    limit = max(1, min(limit, 5000))
    offset = max(0, offset)

    where: list[str] = []
    params: list[Any] = []

    if level:
        placeholders = ",".join("?" * len(level))
        where.append(f"level IN ({placeholders})")
        params.extend(level)
    if category:
        placeholders = ",".join("?" * len(category))
        where.append(f"category IN ({placeholders})")
        params.extend(category)
    if run_id is not None:
        where.append("run_id = ?")
        params.append(run_id)
    if phase is not None:
        where.append("phase = ?")
        params.append(phase)
    if gap_type is not None:
        where.append("gap_type = ?")
        params.append(gap_type)
    if node_id is not None:
        where.append("node_id = ?")
        params.append(node_id)
    if call_id is not None:
        where.append("call_id = ?")
        params.append(call_id)

    since_ms = _parse_since(since, now_ms=now_ms)
    until_ms = _parse_since(until, now_ms=now_ms)
    if since_ms is not None:
        where.append("ts_ms >= ?")
        params.append(since_ms)
    if until_ms is not None:
        where.append("ts_ms <= ?")
        params.append(until_ms)
    if q:
        where.append("(msg LIKE ? OR detail LIKE ?)")
        params.extend([f"%{q}%", f"%{q}%"])

    where_sql = f" WHERE {' AND '.join(where)}" if where else ""

    with sqlite3.connect(str(db_path), timeout=5.0) as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute(
            f"SELECT COUNT(*) FROM logs{where_sql}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT {','.join(COLUMNS)} FROM logs{where_sql} "
            f"ORDER BY ts_ms DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        records = [_row_to_dict(r) for r in rows]

        # Dropped-count summary — sum counts since ``since``, or all-time.
        dropped_where = ""
        dropped_params: list[Any] = []
        if since_ms is not None:
            dropped_where = " WHERE ts_ms >= ?"
            dropped_params.append(since_ms)
        dropped_row = conn.execute(
            f"SELECT COALESCE(SUM(count), 0), MAX(ts_ms) FROM logs_dropped{dropped_where}",
            dropped_params,
        ).fetchone()
        dropped_count = int(dropped_row[0]) if dropped_row else 0
        dropped_ts = dropped_row[1] if dropped_row else None

    return {
        "total": total,
        "records": records,
        "dropped_since": {"count": dropped_count, "ts_ms": dropped_ts},
    }


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    extras = d.get("extras")
    if extras:
        try:
            d["extras"] = json.loads(extras)
        except (ValueError, TypeError):
            # Leave as raw string if it isn't valid JSON.
            pass
    return d


def _percentile(values: list[int], pct: float) -> int:
    """Nearest-rank percentile (pct in [0, 100]). Returns 0 for empty list."""
    if not values:
        return 0
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(round((pct / 100.0) * (len(s) - 1)))))
    return int(s[idx])


def llm_calls_per_run(db_path: Path | str) -> list[dict[str, Any]]:
    """Count LLM calls per run (obs #10).

    The global ``llm_call_count`` in ``agents.factory`` increments across
    every test/process, so "how many LLM calls did THIS run make" can't
    be answered from its value alone. This helper computes it from the
    logs: any outbound record (``msg LIKE '→%'``) in the LLM category.
    """
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT run_id, COUNT(*) FROM logs "
            "WHERE category='LLM' AND msg LIKE '→%' "
            "GROUP BY run_id ORDER BY 2 DESC"
        ).fetchall()
    return [
        {"run_id": (r[0] or "(no run_id)"), "llm_calls": int(r[1])}
        for r in rows
    ]


def agent_latency_rollup(
    db_path: Path | str,
    *,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Aggregate per-agent latency (obs #6).

    Returns one row per agent_id with: count, p50_ms, p95_ms, total_ms, sorted
    by total_ms descending. Optionally restrict to a single run by ``run_id``.

    Example:
        [
          {"agent_id": "Design Architect", "count": 12, "p50_ms": 8200,
           "p95_ms": 42100, "total_ms": 157300},
          ...
        ]
    """
    sql = (
        "SELECT agent_id, duration_ms FROM logs "
        "WHERE agent_id IS NOT NULL AND duration_ms IS NOT NULL"
    )
    params: list[Any] = []
    if run_id is not None:
        sql += " AND run_id = ?"
        params.append(run_id)

    buckets: dict[str, list[int]] = {}
    with sqlite3.connect(str(db_path)) as conn:
        for agent_id, dur in conn.execute(sql, params):
            buckets.setdefault(str(agent_id), []).append(int(dur))

    rollup: list[dict[str, Any]] = []
    for agent_id, durs in buckets.items():
        rollup.append({
            "agent_id": agent_id,
            "count": len(durs),
            "p50_ms": _percentile(durs, 50),
            "p95_ms": _percentile(durs, 95),
            "total_ms": sum(durs),
        })
    rollup.sort(key=lambda r: r["total_ms"], reverse=True)
    return rollup
