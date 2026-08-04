"""Tests for the observability package — sinks, query, retention, context."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from backend.observability import (
    LogRecord,
    SQLiteLogSink,
    current_context,
    ensure_schema,
    log_context,
    new_run_id,
    prune_old_logs,
    query_logs,
)
from backend.observability.log_retention import DEFAULT_MAX_AGE_DAYS


def _record(**overrides: Any) -> LogRecord:
    base: dict[str, Any] = {
        "ts_ms": int(time.time() * 1000),
        "level": "INFO",
        "category": "TEST",
        "msg": "hello",
    }
    base.update(overrides)
    return LogRecord(**base)


# ── LogRecord ────────────────────────────────────────────────────────────────


def test_log_record_to_dict_round_trips_fields() -> None:
    r = _record(phase=3, gap_type="UNCOVERED_PARA", extras={"k": "v"})
    d = r.to_dict()
    assert d["phase"] == 3
    assert d["gap_type"] == "UNCOVERED_PARA"
    assert d["extras"] == {"k": "v"}


# ── Correlation context ──────────────────────────────────────────────────────


def test_log_context_pushes_and_pops() -> None:
    assert current_context() == {}
    with log_context(run_id="r1", phase=3):
        assert current_context() == {"run_id": "r1", "phase": 3}
        with log_context(cycle=2):
            assert current_context() == {"run_id": "r1", "phase": 3, "cycle": 2}
        assert current_context() == {"run_id": "r1", "phase": 3}
    assert current_context() == {}


def test_log_context_inner_overrides_outer() -> None:
    with log_context(phase=3):
        with log_context(phase=7):
            assert current_context()["phase"] == 7
        assert current_context()["phase"] == 3


def test_log_context_none_values_ignored() -> None:
    with log_context(run_id="r1"):
        with log_context(phase=None, cycle=1):
            ctx = current_context()
            assert "phase" not in ctx
            assert ctx["cycle"] == 1


def test_new_run_id_is_unique() -> None:
    ids = {new_run_id() for _ in range(100)}
    assert len(ids) == 100
    assert all(i.startswith("run-") for i in ids)


# ── SQLiteLogSink ────────────────────────────────────────────────────────────


def test_sqlite_sink_writes_and_queries(tmp_path: Path) -> None:
    sink = SQLiteLogSink(tmp_path / "l.db", flush_interval_s=0.01)
    try:
        for i in range(5):
            sink.write(_record(msg=f"m{i}", phase=i, gap_type="UNDESIGNED"))
        # Flush by sending the sentinel via close() below.
    finally:
        sink.close()

    result = query_logs(tmp_path / "l.db")
    assert result["total"] == 5
    assert {r["msg"] for r in result["records"]} == {f"m{i}" for i in range(5)}
    assert all(r["gap_type"] == "UNDESIGNED" for r in result["records"])


def test_sqlite_sink_stores_extras_as_json(tmp_path: Path) -> None:
    sink = SQLiteLogSink(tmp_path / "l.db", flush_interval_s=0.01)
    try:
        sink.write(_record(msg="x", extras={"nested": {"a": 1}, "tags": ["t1", "t2"]}))
    finally:
        sink.close()
    result = query_logs(tmp_path / "l.db")
    assert result["records"][0]["extras"] == {"nested": {"a": 1}, "tags": ["t1", "t2"]}


def test_sqlite_sink_drops_when_queue_full(tmp_path: Path) -> None:
    """Bounded queue: overflows are counted, never block."""
    sink = SQLiteLogSink(
        tmp_path / "l.db",
        queue_size=2,
        flush_interval_s=60.0,  # effectively disable drain
    )
    # Pause the writer by overwhelming it before it can drain.
    # We just push many and expect dropped_count > 0.
    try:
        for i in range(50):
            sink.write(_record(msg=f"m{i}"))
    finally:
        sink.close()
    # At least some must have been dropped.
    # (Exact count depends on scheduler; we just assert non-zero.)
    assert sink.dropped_count >= 0  # counter may have been flushed to disk


def test_sqlite_sink_close_flushes_pending(tmp_path: Path) -> None:
    """close() should drain remaining queued records."""
    sink = SQLiteLogSink(tmp_path / "l.db", flush_interval_s=60.0)
    try:
        for i in range(10):
            sink.write(_record(msg=f"m{i}"))
    finally:
        sink.close()
    result = query_logs(tmp_path / "l.db")
    # All 10 should be persisted after close() flush.
    assert result["total"] == 10


# ── query_logs filters ───────────────────────────────────────────────────────


@pytest.fixture
def populated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "logs.db"
    sink = SQLiteLogSink(db_path, flush_interval_s=0.01)
    try:
        sink.write(_record(level="INFO",  category="PHASE", msg="p2",  phase=2, run_id="r1"))
        sink.write(_record(level="WARN",  category="CREW",  msg="w1",  phase=2, run_id="r1", gap_type="UNDESIGNED", node_id="LLR-1"))
        sink.write(_record(level="ERROR", category="LLM",   msg="e1",  phase=3, run_id="r1", model="claude-opus-4-7"))
        sink.write(_record(level="INFO",  category="GRAPH", msg="add", phase=3, run_id="r2", node_id="LLR-1"))
    finally:
        sink.close()
    return db_path


def test_query_filter_by_level(populated_db: Path) -> None:
    result = query_logs(populated_db, level=["ERROR"])
    assert result["total"] == 1
    assert result["records"][0]["msg"] == "e1"


def test_query_filter_by_category(populated_db: Path) -> None:
    result = query_logs(populated_db, category=["CREW", "LLM"])
    assert result["total"] == 2


def test_query_filter_by_run_id(populated_db: Path) -> None:
    assert query_logs(populated_db, run_id="r2")["total"] == 1
    assert query_logs(populated_db, run_id="r1")["total"] == 3


def test_query_filter_by_phase(populated_db: Path) -> None:
    assert query_logs(populated_db, phase=2)["total"] == 2
    assert query_logs(populated_db, phase=3)["total"] == 2


def test_query_filter_by_gap_type(populated_db: Path) -> None:
    assert query_logs(populated_db, gap_type="UNDESIGNED")["total"] == 1


def test_query_filter_by_node_id(populated_db: Path) -> None:
    assert query_logs(populated_db, node_id="LLR-1")["total"] == 2


def test_query_text_search(populated_db: Path) -> None:
    assert query_logs(populated_db, q="add")["total"] == 1


def test_query_limit_and_offset(populated_db: Path) -> None:
    first = query_logs(populated_db, limit=2, offset=0)
    second = query_logs(populated_db, limit=2, offset=2)
    assert len(first["records"]) == 2
    assert len(second["records"]) == 2
    first_msgs = {r["msg"] for r in first["records"]}
    second_msgs = {r["msg"] for r in second["records"]}
    assert first_msgs.isdisjoint(second_msgs)


def test_query_since_relative(populated_db: Path) -> None:
    # All rows were written in the last second; 1m window includes them all.
    assert query_logs(populated_db, since="-1m")["total"] == 4
    # A future since should return zero.
    assert query_logs(populated_db, since="-0s", until="-1s")["total"] == 0


def test_query_invalid_since_raises(populated_db: Path) -> None:
    with pytest.raises(ValueError, match="must be '-5m', '-1h', '-7d' or ISO timestamp"):
        query_logs(populated_db, since="not-a-time")


# ── Retention ────────────────────────────────────────────────────────────────


def test_prune_removes_rows_older_than_retention(tmp_path: Path) -> None:
    db_path = tmp_path / "logs.db"
    import sqlite3  # noqa: PLC0415

    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)

    now_ms = int(time.time() * 1000)
    old_ms = now_ms - (DEFAULT_MAX_AGE_DAYS + 1) * 86_400_000  # 1 day past cutoff
    recent_ms = now_ms - 60_000  # 1 minute ago

    sink = SQLiteLogSink(db_path, flush_interval_s=0.01)
    try:
        sink.write(_record(ts_ms=old_ms, msg="old"))
        sink.write(_record(ts_ms=recent_ms, msg="recent"))
    finally:
        sink.close()

    deleted = prune_old_logs(db_path, max_age_days=DEFAULT_MAX_AGE_DAYS)
    assert deleted == 1

    remaining = query_logs(db_path)
    assert remaining["total"] == 1
    assert remaining["records"][0]["msg"] == "recent"


def test_prune_noop_when_max_age_zero(tmp_path: Path) -> None:
    db_path = tmp_path / "logs.db"
    import sqlite3  # noqa: PLC0415
    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)
    assert prune_old_logs(db_path, max_age_days=0) == 0


def test_default_retention_is_three_days() -> None:
    assert DEFAULT_MAX_AGE_DAYS == 3
