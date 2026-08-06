"""ForgeLogger — structured observability front-end.

Builds a :class:`LogRecord` for every event and fans it out to registered
:class:`LogSink` instances. Keeps the existing ``emit`` signature and
convenience helpers for compatibility with the ~100 call sites across
the codebase.

Sink configuration happens at startup (see
``backend/server/lifespan.py``). Defaults:

* :class:`FileLogSink`    → ``forge.log`` (line-buffered pretty-print).
* :class:`SQLiteLogSink`  → ``.forge/forge.logs.db`` (structured,
  retention-bounded, queryable via ``GET /api/v1/logs``).
* :class:`WSLogSink`      → ``FORGE_LOG`` WebSocket events.
* Optional: :class:`StdoutLogSink` / :class:`StderrLogSink` for tests.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from backend.observability.log_context import current_context
from backend.observability.log_record import (
    PROMOTED_META_KEYS,
    LogCategory,
    LogRecord,
    normalise_category,
)
from backend.observability.log_sinks import (
    FileLogSink,
    LogSink,
    SQLiteLogSink,
    StderrLogSink,
    StdoutLogSink,
    WSLogSink,
)
from backend.server.forge_logger_events import EventEmitters

_logger = logging.getLogger(__name__)


class ForgeLogger(EventEmitters):
    """Singleton structured diagnostic logger.

    Thread-safe: ``emit`` builds a LogRecord and hands it to each sink.
    The sinks themselves are responsible for non-blocking writes.
    """

    def __init__(self) -> None:
        self._sinks: list[LogSink] = []
        self._file_sink: FileLogSink | None = None
        self._sqlite_sink: SQLiteLogSink | None = None
        self._ws_sink: WSLogSink | None = None
        self._stderr_sink: StderrLogSink | None = None
        self._stdout_sink: StdoutLogSink | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialise(
        self,
        log_path: Path,
        ws_manager: Any,
        *,
        sqlite_path: Path | None = None,
    ) -> None:
        """Wire up the default sink set.

        * *log_path*     — flat-file pretty-printed output.
        * *ws_manager*   — :class:`WebSocketManager` for live UI.
        * *sqlite_path*  — structured log store; defaults to
          ``log_path.parent / 'forge.logs.db'``.
        """
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self._file_sink = FileLogSink(log_path)
        self._sinks.append(self._file_sink)

        sqlite_path = sqlite_path or log_path.parent / "forge.logs.db"
        try:
            self._sqlite_sink = SQLiteLogSink(sqlite_path)
            self._sinks.append(self._sqlite_sink)
        except Exception:  # noqa: BLE001
            _logger.exception("Could not open SQLite log sink at %s", sqlite_path)

        self._ws_sink = WSLogSink(ws_manager)
        self._sinks.append(self._ws_sink)

        self.emit("INFO", "SYS  ", "ForgeLogger initialised — structured sinks active")

    def add_sink(self, sink: LogSink) -> None:
        """Register an additional sink. Useful for tests."""
        self._sinks.append(sink)

    def sqlite_db_paths(self) -> list[str]:
        """DB paths of every attached SQLite sink.

        Used by run-artifact persistence
        (``backend/observability/run_artifacts.py``) to locate this
        process's logs DB regardless of whether it was wired at server
        startup or by a test conftest via :meth:`add_sink`.
        """
        return [
            sink.db_path for sink in self._sinks if isinstance(sink, SQLiteLogSink)
        ]

    def enable_stderr(self) -> None:
        if self._stderr_sink is None:
            self._stderr_sink = StderrLogSink()
            self._sinks.append(self._stderr_sink)

    def disable_stderr(self) -> None:
        if self._stderr_sink is not None and self._stderr_sink in self._sinks:
            self._sinks.remove(self._stderr_sink)
            self._stderr_sink = None

    def enable_stdout(self) -> None:
        if self._stdout_sink is None:
            self._stdout_sink = StdoutLogSink()
            self._sinks.append(self._stdout_sink)

    def disable_stdout(self) -> None:
        if self._stdout_sink is not None and self._stdout_sink in self._sinks:
            self._sinks.remove(self._stdout_sink)
            self._stdout_sink = None

    def close(self) -> None:
        """Flush every sink and release resources."""
        for sink in self._sinks:
            try:
                sink.close()
            except Exception:  # noqa: BLE001
                _logger.exception("Error closing sink %r", sink)
        self._sinks.clear()
        self._file_sink = None
        self._sqlite_sink = None
        self._ws_sink = None
        self._stderr_sink = None
        self._stdout_sink = None

    # ------------------------------------------------------------------
    # Core emit
    # ------------------------------------------------------------------

    def emit(
        self,
        level: str,
        cat: str | LogCategory,
        msg: str,
        detail: str | None = None,
        **meta: Any,
    ) -> None:
        """Build a LogRecord and hand it to every sink.

        ``cat`` may be a :class:`LogCategory` member or a string; strings
        are normalised (trimmed + upper-cased) so legacy space-padded
        categories collapse onto the canonical form used for filtering.

        Well-known meta keys (phase, gap_type, node_id, agent_id, model,
        duration_ms, etc.) are promoted to dedicated columns; everything
        else falls through into ``extras``. Correlation fields from
        :mod:`backend.observability.log_context` are merged in.
        """
        category = normalise_category(cat)

        # Start with the current correlation context; meta overrides.
        merged: dict[str, Any] = dict(current_context())
        for k, v in meta.items():
            if v is not None:
                merged[k] = v

        # Split into promoted columns vs extras.
        promoted: dict[str, Any] = {}
        extras: dict[str, Any] = {}
        for k, v in merged.items():
            if k in PROMOTED_META_KEYS:
                promoted[k] = _coerce(k, v)
            else:
                extras[k] = v

        record = LogRecord(
            ts_ms=int(time.time() * 1000),
            level=level,
            category=category,
            msg=msg,
            detail=detail,
            extras=extras or None,
            **promoted,
        )

        for sink in self._sinks:
            try:
                sink.write(record)
            except Exception:  # noqa: BLE001
                # Sinks are best-effort — never crash the caller.
                _logger.exception("Sink %r raised on write; record dropped", sink)

    # Domain event helpers (loop/phase/gap/agent/LLM/crew/tool/decision/
    # graph/user) are inherited from EventEmitters.


def _coerce(key: str, value: Any) -> Any:
    """Coerce meta values to the expected column type. Tolerant of bad input."""
    _int_keys = {"phase", "cycle", "prompt_tokens", "completion_tokens",
                 "tool_call_count", "duration_ms"}
    if key in _int_keys:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return str(value) if value is not None else None


# Module-level singleton — import and use directly.
forge_logger = ForgeLogger()
