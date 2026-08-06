"""Industrial-strength structured logging for FORGE.

See ``specs/11-observability.md`` for the architecture and
``specs/11-observability.md`` for the user-facing spec. Public surface:

* :class:`LogRecord`  — the canonical structured log record
* :func:`log_context` — contextmanager to push correlation fields
* :class:`SQLiteLogSink`, :class:`FileLogSink`, :class:`WSLogSink`
* :func:`query_logs` — programmatic query helper (used by HTTP router)
* :func:`prune_old_logs` — retention helper
"""

from backend.observability.llm_trace import LLMTraceWriter, resolve_trace_path
from backend.observability.log_context import (
    current_context,
    log_context,
    new_call_id,
    new_run_id,
    run_with_context,
)
from backend.observability.log_record import (
    LogCategory,
    LogRecord,
    normalise_category,
    validate_category,
)
from backend.observability.log_retention import prune_old_logs
from backend.observability.log_schema import ensure_schema
from backend.observability.log_sinks import (
    FileLogSink,
    LogSink,
    SQLiteLogSink,
    StdoutLogSink,
    WSLogSink,
)
from backend.observability.query import query_logs

__all__ = [
    "FileLogSink",
    "LLMTraceWriter",
    "LogCategory",
    "LogRecord",
    "LogSink",
    "SQLiteLogSink",
    "StdoutLogSink",
    "WSLogSink",
    "current_context",
    "ensure_schema",
    "log_context",
    "new_call_id",
    "new_run_id",
    "run_with_context",
    "normalise_category",
    "prune_old_logs",
    "query_logs",
    "resolve_trace_path",
    "validate_category",
]
