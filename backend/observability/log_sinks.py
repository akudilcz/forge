"""Log sinks — pluggable destinations for LogRecord.

The ``LogSink`` protocol is the integration point; the three concrete
sinks cover:

* :class:`SQLiteLogSink`   — persistent, queryable, bounded-queue
  background writer. Default production sink.
* :class:`FileLogSink`      — human-readable flat file (preserves the
  existing ``forge.log`` tailing workflow).
* :class:`WSLogSink`        — broadcasts as FORGE_LOG WebSocket events
  for the frontend live feed.
* :class:`StdoutLogSink` / :class:`StderrLogSink` — optional console
  mirrors used in tests.

Callers never block: every sink is expected to be best-effort.
SQLiteLogSink drops records (and tallies them) rather than stalling the
build loop when under pressure.
"""

from __future__ import annotations

import json
import logging
import queue
import sqlite3
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import IO, Any, Protocol

from backend.observability.log_record import COLUMNS, LogRecord
from backend.observability.log_schema import ensure_schema

logger = logging.getLogger(__name__)


class LogSink(Protocol):
    """Destination for structured log records."""

    def write(self, record: LogRecord) -> None: ...
    def close(self) -> None: ...


# ── SQLite sink (primary) ────────────────────────────────────────────────────


class SQLiteLogSink:
    """Background-thread SQLite writer with bounded queue and WAL.

    Writes are enqueued from any thread; a single writer thread drains
    the queue in batches. If the queue is full, the record is dropped
    and ``dropped_count`` is incremented so backpressure is never silent.
    """

    _SENTINEL: Any = object()

    def __init__(
        self,
        db_path: Path | str,
        *,
        queue_size: int = 10_000,
        batch_size: int = 256,
        flush_interval_s: float = 0.1,
    ) -> None:
        self._db_path = str(db_path)
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=queue_size)
        self._batch_size = batch_size
        self._flush_interval_s = flush_interval_s
        self._dropped_count = 0
        self._dropped_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._ensure_schema()
        self._start_writer()

    # ---- public API ----------------------------------------------------

    def write(self, record: LogRecord) -> None:
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            with self._dropped_lock:
                self._dropped_count += 1

    def close(self) -> None:
        """Flush pending records and stop the writer thread."""
        if self._thread is None:
            return
        self._queue.put(self._SENTINEL)
        self._thread.join(timeout=5.0)
        self._thread = None
        self._flush_dropped()

    @property
    def dropped_count(self) -> int:
        return self._dropped_count

    # ---- internals -----------------------------------------------------

    def _ensure_schema(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path, timeout=5.0) as conn:
            ensure_schema(conn)

    def _start_writer(self) -> None:
        self._thread = threading.Thread(
            target=self._writer_loop,
            name="forge-logs-writer",
            daemon=True,
        )
        self._thread.start()

    def _writer_loop(self) -> None:
        try:
            conn = sqlite3.connect(self._db_path, timeout=5.0, isolation_level=None)
            # autocommit=OFF via explicit BEGIN/COMMIT per batch
        except Exception:  # noqa: BLE001
            logger.exception("Log writer could not open database; sink disabled.")
            return

        try:
            last_flush = time.monotonic()
            batch: list[LogRecord] = []
            while True:
                timeout = max(0.0, self._flush_interval_s - (time.monotonic() - last_flush))
                try:
                    item = self._queue.get(timeout=timeout or 0.001)
                except queue.Empty:
                    item = None

                if item is self._SENTINEL:
                    self._write_batch(conn, batch)
                    self._flush_dropped_to(conn)
                    return
                if isinstance(item, LogRecord):
                    batch.append(item)

                if len(batch) >= self._batch_size or (
                    batch and time.monotonic() - last_flush >= self._flush_interval_s
                ):
                    self._write_batch(conn, batch)
                    self._flush_dropped_to(conn)
                    batch = []
                    last_flush = time.monotonic()
        finally:
            with suppress(Exception):
                conn.close()

    def _write_batch(self, conn: sqlite3.Connection, batch: list[LogRecord]) -> None:
        if not batch:
            return
        placeholders = ",".join("?" * len(COLUMNS))
        sql = f"INSERT INTO logs ({','.join(COLUMNS)}) VALUES ({placeholders})"
        rows = [_record_to_row(r) for r in batch]
        try:
            with conn:
                conn.executemany(sql, rows)
        except Exception:  # noqa: BLE001
            # Do NOT raise — sink must not crash the caller. Log and drop.
            logger.exception("SQLiteLogSink batch insert failed; dropping %d", len(rows))

    def _flush_dropped(self) -> None:
        """Record any dropped count to the DB. Safe to call from the main thread."""
        if self._dropped_count <= 0:
            return
        try:
            with sqlite3.connect(self._db_path, timeout=5.0) as conn:
                self._flush_dropped_to(conn)
        except Exception:  # noqa: BLE001
            logger.exception("Could not flush dropped counter")

    def _flush_dropped_to(self, conn: sqlite3.Connection) -> None:
        with self._dropped_lock:
            count = self._dropped_count
            self._dropped_count = 0
        if count <= 0:
            return
        try:
            with conn:
                conn.execute(
                    "INSERT OR REPLACE INTO logs_dropped (ts_ms, count, reason) "
                    "VALUES (?, ?, ?)",
                    (int(time.time() * 1000), count, "queue_full"),
                )
        except Exception:  # noqa: BLE001
            logger.exception("Could not record dropped-count batch of %d", count)


def _record_to_row(r: LogRecord) -> tuple[Any, ...]:
    extras = json.dumps(r.extras, default=str) if r.extras else None
    return (
        r.ts_ms,
        r.level,
        r.category,
        r.msg,
        r.detail,
        r.run_id,
        r.phase,
        r.cycle,
        r.gap_type,
        r.gap_id,
        r.node_id,
        r.agent_id,
        r.call_id,
        r.model,
        r.prompt_tokens,
        r.completion_tokens,
        r.tool_call_count,
        r.tool_name,
        r.duration_ms,
        r.error_type,
        extras,
    )


# ── File sink ────────────────────────────────────────────────────────────────


class FileLogSink:
    """Line-buffered pretty-printer to a flat file. Truncates on open."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file: IO[str] | None = path.open(
            "w", encoding="utf-8", buffering=1
        )

    def write(self, record: LogRecord) -> None:
        if self._file is None:
            return
        line = _format_line(record)
        try:
            self._file.write(line + "\n")
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except Exception:  # noqa: BLE001
                pass
            self._file = None


class _ConsoleSink:
    def __init__(self, stream: IO[str]) -> None:
        self._stream = stream

    def write(self, record: LogRecord) -> None:
        try:
            self._stream.write(_format_line(record) + "\n")
            self._stream.flush()
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        return None


class StdoutLogSink(_ConsoleSink):
    def __init__(self) -> None:
        super().__init__(sys.stdout)


class StderrLogSink(_ConsoleSink):
    def __init__(self) -> None:
        super().__init__(sys.stderr)


def _format_line(r: LogRecord) -> str:
    ts = time.strftime("%H:%M:%S", time.localtime(r.ts_ms / 1000)) + f".{r.ts_ms % 1000:03d}"
    line = f"[{ts}] [{r.level:<5}] [{r.category}] {r.msg}"
    if r.detail:
        line += f"\n  {r.detail}"
    return line


# ── WebSocket sink ───────────────────────────────────────────────────────────


class WSLogSink:
    """Broadcasts records as FORGE_LOG WebSocket events."""

    def __init__(self, ws_manager: Any) -> None:
        self._ws_manager = ws_manager

    def write(self, record: LogRecord) -> None:
        if self._ws_manager is None:
            return
        try:
            from backend.server.websocket.events import WSEvent, WSEventType  # noqa: PLC0415
        except Exception:  # noqa: BLE001
            return
        payload = {
            "ts": time.strftime("%H:%M:%S", time.localtime(record.ts_ms / 1000))
                  + f".{record.ts_ms % 1000:03d}",
            "level": record.level,
            "cat": record.category.strip(),
            "msg": record.msg,
            "detail": record.detail,
        }
        # Include correlation + metric fields for the frontend to filter on.
        for key in (
            "run_id", "phase", "cycle", "gap_type", "gap_id", "node_id",
            "agent_id", "call_id", "model", "duration_ms", "tool_name",
            "tool_call_count", "error_type",
        ):
            val = getattr(record, key)
            if val is not None:
                payload[key] = val
        event = WSEvent(event_type=WSEventType.FORGE_LOG, payload=payload)
        try:
            self._ws_manager.broadcast_threadsafe(event)
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        return None
