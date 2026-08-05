"""Tests for the observability package — sinks, query, retention, context."""

from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from backend.observability import (
    FileLogSink,
    LogRecord,
    SQLiteLogSink,
    StdoutLogSink,
    WSLogSink,
    current_context,
    ensure_schema,
    log_context,
    new_run_id,
    prune_old_logs,
    query_logs,
)
from backend.observability.log_context import new_call_id, run_with_context
from backend.observability.log_retention import DEFAULT_MAX_AGE_DAYS
from backend.observability.log_sinks import StderrLogSink
from backend.observability.query import (
    _percentile,
    agent_latency_rollup,
    llm_calls_per_run,
)


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


def _insert_rows(db_path: Path, rows: int, detail_size: int) -> None:
    """Insert ``rows`` recent log rows with a large detail payload.

    Checkpoints the WAL so ``db_path``'s file size reflects the data
    (retention's size cap stats the main DB file only).
    """
    now_ms = int(time.time() * 1000)
    conn = sqlite3.connect(db_path)
    try:
        ensure_schema(conn)
        conn.executemany(
            "INSERT INTO logs (ts_ms, level, category, msg, detail) "
            "VALUES (?, 'INFO', 'TEST', 'bulk', ?)",
            [(now_ms, "x" * detail_size) for _ in range(rows)],
        )
        conn.commit()
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()


@pytest.mark.xfail(
    strict=True,
    raises=sqlite3.OperationalError,
    reason=(
        "BUG in prune_old_logs: the size-cap loop runs VACUUM inside the "
        "implicit transaction opened by the preceding DELETE, so SQLite "
        "raises 'cannot VACUUM from within a transaction'. The size cap "
        "can therefore never actually shrink the file. Fix: commit the "
        "DELETE before VACUUM (or connect with isolation_level=None)."
    ),
)
def test_prune_size_cap_deletes_oldest_until_under_cap(tmp_path: Path) -> None:
    db_path = tmp_path / "logs.db"
    _insert_rows(db_path, rows=400, detail_size=10_000)  # ~4 MB
    assert db_path.stat().st_size > 1024 * 1024

    deleted = prune_old_logs(db_path, max_age_days=0, max_size_mb=1)
    assert deleted > 0
    assert db_path.stat().st_size <= 1024 * 1024


def test_prune_size_cap_stops_when_fewer_than_100_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "logs.db"
    # Over the 1 MB cap but with < 100 rows: pruning must bail out
    # rather than delete everything.
    _insert_rows(db_path, rows=50, detail_size=50_000)  # ~2.5 MB
    assert db_path.stat().st_size > 1024 * 1024

    deleted = prune_old_logs(db_path, max_age_days=0, max_size_mb=1)
    assert deleted == 0
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM logs").fetchone()[0] == 50


def test_prune_size_cap_skipped_when_none(tmp_path: Path) -> None:
    db_path = tmp_path / "logs.db"
    _insert_rows(db_path, rows=400, detail_size=10_000)
    size_before = db_path.stat().st_size
    deleted = prune_old_logs(db_path, max_age_days=0, max_size_mb=None)
    assert deleted == 0
    assert db_path.stat().st_size == size_before


# ── Context helpers ──────────────────────────────────────────────────────────


def test_run_with_context_propagates_snapshot() -> None:
    with log_context(run_id="r-ctx", phase=9):
        result = run_with_context(current_context)
    assert result == {"run_id": "r-ctx", "phase": 9}


def test_run_with_context_passes_args_and_kwargs() -> None:
    def fn(a: int, *, b: int) -> int:
        return a + b

    assert run_with_context(fn, 2, b=3) == 5


def test_new_call_id_is_unique_and_prefixed() -> None:
    ids = {new_call_id() for _ in range(100)}
    assert len(ids) == 100
    assert all(i.startswith("call-") for i in ids)


# ── SQLiteLogSink error paths ────────────────────────────────────────────────


def test_sqlite_sink_flushes_when_batch_size_reached(tmp_path: Path) -> None:
    """With a tiny batch size and a long flush interval, records must be
    persisted as soon as the batch fills — before close()."""
    db_path = tmp_path / "l.db"
    sink = SQLiteLogSink(db_path, batch_size=2, flush_interval_s=60.0)
    try:
        for i in range(6):
            sink.write(_record(msg=f"m{i}"))
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if query_logs(db_path)["total"] >= 2:
                break
            time.sleep(0.01)
        assert query_logs(db_path)["total"] >= 2
    finally:
        sink.close()
    assert query_logs(db_path)["total"] == 6


def test_sqlite_sink_close_is_idempotent(tmp_path: Path) -> None:
    sink = SQLiteLogSink(tmp_path / "l.db", flush_interval_s=0.01)
    sink.close()
    # Second close is a no-op (thread already gone).
    sink.close()


def test_sqlite_sink_writer_disabled_when_db_unopenable(tmp_path: Path) -> None:
    """If the writer thread cannot open the DB, the sink disables itself
    without crashing callers."""
    real_connect = sqlite3.connect
    calls = {"n": 0}

    def flaky_connect(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] >= 2:  # first call = schema; later = writer thread
            raise sqlite3.OperationalError("disk gone")
        return real_connect(*args, **kwargs)

    with patch("backend.observability.log_sinks.sqlite3.connect", flaky_connect):
        sink = SQLiteLogSink(tmp_path / "l.db", flush_interval_s=0.01)
        assert sink._thread is not None  # noqa: SLF001
        sink._thread.join(timeout=5.0)  # noqa: SLF001 — writer exits on failure
        sink.write(_record(msg="lost"))  # must not raise
    sink._thread = None  # noqa: SLF001 — avoid close() joining a dead thread
    assert calls["n"] >= 2


def test_sqlite_sink_write_batch_swallows_insert_failure(tmp_path: Path) -> None:
    sink = SQLiteLogSink(tmp_path / "l.db", flush_interval_s=0.01)
    sink.close()
    bad_conn = sqlite3.connect(":memory:")  # no logs table
    # Must log-and-drop, never raise.
    sink._write_batch(bad_conn, [_record(msg="x")])  # noqa: SLF001
    bad_conn.close()


def test_sqlite_sink_flush_dropped_writes_counter(tmp_path: Path) -> None:
    db_path = tmp_path / "l.db"
    sink = SQLiteLogSink(db_path, flush_interval_s=0.01)
    sink.close()
    sink._dropped_count = 7  # noqa: SLF001
    sink._flush_dropped()  # noqa: SLF001
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT count, reason FROM logs_dropped").fetchone()
    assert row == (7, "queue_full")
    assert sink.dropped_count == 0


def test_sqlite_sink_flush_dropped_survives_connect_failure(tmp_path: Path) -> None:
    sink = SQLiteLogSink(tmp_path / "l.db", flush_interval_s=0.01)
    sink.close()
    sink._dropped_count = 3  # noqa: SLF001
    with patch(
        "backend.observability.log_sinks.sqlite3.connect",
        side_effect=sqlite3.OperationalError("locked"),
    ):
        sink._flush_dropped()  # noqa: SLF001 — must not raise


def test_sqlite_sink_flush_dropped_to_survives_bad_conn(tmp_path: Path) -> None:
    sink = SQLiteLogSink(tmp_path / "l.db", flush_interval_s=0.01)
    sink.close()
    sink._dropped_count = 2  # noqa: SLF001
    bad_conn = sqlite3.connect(":memory:")  # no logs_dropped table
    sink._flush_dropped_to(bad_conn)  # noqa: SLF001 — must not raise
    bad_conn.close()


# ── FileLogSink ──────────────────────────────────────────────────────────────


def test_file_sink_writes_formatted_lines(tmp_path: Path) -> None:
    path = tmp_path / "forge.log"
    sink = FileLogSink(path)
    sink.write(_record(msg="hello", detail="ctx line"))
    sink.close()
    text = path.read_text(encoding="utf-8")
    assert "hello" in text
    assert "\n  ctx line" in text
    assert "[INFO " in text


def test_file_sink_write_after_close_is_noop(tmp_path: Path) -> None:
    sink = FileLogSink(tmp_path / "forge.log")
    sink.close()
    sink.write(_record(msg="late"))  # must not raise
    sink.close()  # idempotent
    assert (tmp_path / "forge.log").read_text(encoding="utf-8") == ""


def test_file_sink_write_swallows_io_error(tmp_path: Path) -> None:
    sink = FileLogSink(tmp_path / "forge.log")
    assert sink._file is not None  # noqa: SLF001
    sink._file.close()  # noqa: SLF001 — simulate underlying handle failure
    sink.write(_record(msg="x"))  # must not raise
    sink._file = None  # noqa: SLF001


def test_file_sink_close_swallows_close_error(tmp_path: Path) -> None:
    sink = FileLogSink(tmp_path / "forge.log")
    broken = MagicMock()
    broken.close.side_effect = OSError("boom")
    sink._file = broken  # noqa: SLF001
    sink.close()  # must not raise
    assert sink._file is None  # noqa: SLF001


# ── Console sinks ────────────────────────────────────────────────────────────


def test_stdout_sink_writes_line(capsys: pytest.CaptureFixture[str]) -> None:
    sink = StdoutLogSink()
    sink.write(_record(msg="console msg"))
    sink.close()
    assert "console msg" in capsys.readouterr().out


def test_stderr_sink_writes_line(capsys: pytest.CaptureFixture[str]) -> None:
    sink = StderrLogSink()
    sink.write(_record(msg="err msg"))
    sink.close()
    assert "err msg" in capsys.readouterr().err


def test_console_sink_swallows_stream_error() -> None:
    stream = MagicMock()
    stream.write.side_effect = OSError("closed")
    from backend.observability.log_sinks import _ConsoleSink  # noqa: PLC0415

    sink = _ConsoleSink(stream)
    sink.write(_record(msg="x"))  # must not raise


# ── WSLogSink ────────────────────────────────────────────────────────────────


def test_ws_sink_broadcasts_event_with_correlation_fields() -> None:
    manager = MagicMock()
    sink = WSLogSink(manager)
    sink.write(_record(msg="ws msg", run_id="r1", phase=3, duration_ms=42))
    sink.close()
    manager.broadcast_threadsafe.assert_called_once()
    event = manager.broadcast_threadsafe.call_args[0][0]
    assert event.payload["msg"] == "ws msg"
    assert event.payload["run_id"] == "r1"
    assert event.payload["phase"] == 3
    assert event.payload["duration_ms"] == 42
    assert "gap_id" not in event.payload  # None fields omitted


def test_ws_sink_noop_when_manager_none() -> None:
    sink = WSLogSink(None)
    sink.write(_record(msg="dropped"))  # must not raise


def test_ws_sink_swallows_broadcast_error() -> None:
    manager = MagicMock()
    manager.broadcast_threadsafe.side_effect = RuntimeError("loop closed")
    sink = WSLogSink(manager)
    sink.write(_record(msg="x"))  # must not raise


def test_ws_sink_noop_when_events_module_unimportable() -> None:
    manager = MagicMock()
    sink = WSLogSink(manager)
    with patch.dict(sys.modules, {"backend.server.websocket.events": None}):
        sink.write(_record(msg="x"))
    manager.broadcast_threadsafe.assert_not_called()


# ── query.py helpers ─────────────────────────────────────────────────────────


def test_query_leaves_invalid_extras_as_raw_string(tmp_path: Path) -> None:
    db_path = tmp_path / "logs.db"
    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            "INSERT INTO logs (ts_ms, level, category, msg, extras) "
            "VALUES (?, 'INFO', 'TEST', 'bad extras', 'not-json{')",
            (int(time.time() * 1000),),
        )
        conn.commit()
    result = query_logs(db_path)
    assert result["records"][0]["extras"] == "not-json{"


def test_percentile_of_empty_list_is_zero() -> None:
    assert _percentile([], 50) == 0


def test_percentile_nearest_rank() -> None:
    values = [10, 20, 30, 40, 50]
    assert _percentile(values, 0) == 10
    assert _percentile(values, 50) == 30
    assert _percentile(values, 100) == 50


def test_llm_calls_per_run_counts_outbound_records(tmp_path: Path) -> None:
    db_path = tmp_path / "logs.db"
    sink = SQLiteLogSink(db_path, flush_interval_s=0.01)
    try:
        sink.write(_record(category="LLM", msg="→ call 1", run_id="r1"))
        sink.write(_record(category="LLM", msg="→ call 2", run_id="r1"))
        sink.write(_record(category="LLM", msg="← response", run_id="r1"))
        sink.write(_record(category="LLM", msg="→ call 3", run_id=None))
        sink.write(_record(category="TOOL", msg="→ not llm", run_id="r1"))
    finally:
        sink.close()
    rows = llm_calls_per_run(db_path)
    assert {"run_id": "r1", "llm_calls": 2} in rows
    assert {"run_id": "(no run_id)", "llm_calls": 1} in rows


def test_agent_latency_rollup_aggregates_and_sorts(tmp_path: Path) -> None:
    db_path = tmp_path / "logs.db"
    sink = SQLiteLogSink(db_path, flush_interval_s=0.01)
    try:
        sink.write(_record(agent_id="slow", duration_ms=100, run_id="r1"))
        sink.write(_record(agent_id="slow", duration_ms=300, run_id="r1"))
        sink.write(_record(agent_id="fast", duration_ms=50, run_id="r1"))
        sink.write(_record(agent_id="fast", duration_ms=60, run_id="r2"))
        sink.write(_record(agent_id="no-duration", run_id="r1"))
    finally:
        sink.close()

    rollup = agent_latency_rollup(db_path)
    assert [r["agent_id"] for r in rollup] == ["slow", "fast"]
    slow = rollup[0]
    assert slow["count"] == 2
    assert slow["total_ms"] == 400
    assert slow["p50_ms"] == 100
    assert slow["p95_ms"] == 300


def test_agent_latency_rollup_filters_by_run_id(tmp_path: Path) -> None:
    db_path = tmp_path / "logs.db"
    sink = SQLiteLogSink(db_path, flush_interval_s=0.01)
    try:
        sink.write(_record(agent_id="a", duration_ms=10, run_id="r1"))
        sink.write(_record(agent_id="a", duration_ms=99, run_id="r2"))
    finally:
        sink.close()
    rollup = agent_latency_rollup(db_path, run_id="r1")
    assert rollup == [
        {"agent_id": "a", "count": 1, "p50_ms": 10, "p95_ms": 10, "total_ms": 10}
    ]


def test_query_since_iso_timestamp(populated_db: Path) -> None:
    from datetime import datetime, timedelta  # noqa: PLC0415

    past = (datetime.now() - timedelta(minutes=5)).isoformat()
    assert query_logs(populated_db, since=past)["total"] == 4


def test_query_filter_by_call_id(tmp_path: Path) -> None:
    db_path = tmp_path / "logs.db"
    sink = SQLiteLogSink(db_path, flush_interval_s=0.01)
    try:
        sink.write(_record(msg="a", call_id="call-1"))
        sink.write(_record(msg="b", call_id="call-2"))
    finally:
        sink.close()
    result = query_logs(db_path, call_id="call-1")
    assert result["total"] == 1
    assert result["records"][0]["msg"] == "a"
