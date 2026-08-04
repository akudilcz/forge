# Observability Architecture

This document describes how the system implements the structured logging
behaviour specified in `specs/11-observability.md`. The logger is a
single process-wide singleton that fans every event out to a set of
pluggable sinks, with the SQLite sink as the canonical store.

## Modules

| Module | Role |
|--------|------|
| `backend/server/forge_logger.py` | `ForgeLogger` singleton + typed helpers for each event family. |
| `backend/observability/log_record.py` | Immutable `LogRecord` dataclass + `LogCategory` enum + `PROMOTED_META_KEYS`. |
| `backend/observability/log_sinks.py` | `LogSink` protocol + concrete sinks: `SQLiteLogSink`, `FileLogSink`, `WSLogSink`, `StdoutLogSink`, `StderrLogSink`. |
| `backend/observability/log_schema.py` | SQLite DDL (`logs`, `logs_dropped`, indexes). |
| `backend/observability/log_context.py` | Per-async-task correlation context (`run_id`, `phase`, `cycle`, `call_id`, `node_id`, `gap_type`, etc.). |
| `backend/observability/log_retention.py` | `prune_old_logs` — deletes rows older than N days. |
| `backend/observability/query.py` | Shared helpers for the `/api/v1/logs` router. |
| `backend/server/routers/logs.py` | HTTP endpoint for the log viewer. |

## Data flow

```
callers (agents, tools, graph, pipeline, ...)
  └─▶ forge_logger.<helper>(...)                ← typed API (e.g. gap_resolved, llm_response)
        └─▶ ForgeLogger._emit(level, cat, msg, **meta)
              ├─▶ merge current correlation context
              ├─▶ split meta into promoted columns + extras
              └─▶ for each sink in self._sinks: sink.write(record)
                    ├─▶ SQLiteLogSink: enqueue → bg writer thread
                    ├─▶ FileLogSink:   pretty-print → flat file
                    ├─▶ WSLogSink:     broadcast FORGE_LOG event
                    └─▶ Stdout/Stderr: test mirrors
```

Sinks are **best-effort** and **non-blocking**. If a sink raises, the
logger swallows the exception so the caller never crashes. If the
SQLite queue is full, the record is dropped and `dropped_count` is
incremented (visible via `logs_dropped` + test-session summary).

## Correlation context

`log_context(**fields)` is a `contextvars` wrapper. Any sub-task inside
the `with log_context(...)` block automatically includes the fields on
every `_emit`. Typical stack:

- `run_id` set once at build-loop entry.
- `phase` set at phase start.
- `cycle` set at each pipeline cycle.
- `call_id` set per LLM turn (see `agents/factory.py`).
- `gap_type` / `node_id` set at gap dispatch.

## SQLite schema

Primary table `logs` has:

- `id INTEGER PRIMARY KEY AUTOINCREMENT`
- `ts_ms INTEGER NOT NULL`
- `level TEXT NOT NULL`
- `category TEXT NOT NULL`
- `msg TEXT NOT NULL`
- `detail TEXT`
- Promoted correlation: `run_id`, `phase`, `cycle`, `gap_type`, `gap_id`,
  `node_id`, `agent_id`, `call_id`.
- Promoted LLM/tool metadata: `model`, `prompt_tokens`,
  `completion_tokens`, `tool_call_count`, `tool_name`, `duration_ms`,
  `error_type`.
- `extras TEXT` — JSON blob for everything else.

Indexes: `ts_ms`, `(level, ts_ms)`, `(run_id, ts_ms)`,
`(run_id, phase, ts_ms)`, `(gap_type, node_id, ts_ms)`,
`(category, ts_ms)`, `call_id`, `(node_id, ts_ms)`.

Auxiliary `logs_dropped(ts_ms, count, reason)` records queue drops.

## Sink lifecycle

- **Server** — `backend/server/lifespan.py::_startup` calls
  `forge_logger.initialise(log_path, ws_manager, sqlite_path)` which
  creates `FileLogSink`, `SQLiteLogSink`, `WSLogSink` in order. `close()`
  is called on shutdown to flush the sink's queue.
- **Tests** — `backend/tests/conftest.py` wires a session-scoped
  `SQLiteLogSink` pointing at `.forge/forge.test.logs.db` and emits a
  per-level/per-category summary in `pytest_terminal_summary`.

## Retention

On startup, `prune_old_logs(db_path, max_age_days=30)` runs once. The
bound is deterministic; there is no rotation/backup policy. A fresh run
therefore sees roughly the last month of history.

## Extension points

- **New sink** — implement the `LogSink` protocol and register via
  `forge_logger.add_sink(sink)`. Tests use this for capture sinks.
- **New promoted key** — add the name to `PROMOTED_META_KEYS` and a
  column to `log_schema.py`; coerce rules live in
  `forge_logger._coerce`.
- **New category** — add a member to `LogCategory` and a helper on
  `ForgeLogger` to emit it.

## Non-goals

- No rotation; retention is the bound.
- No external exporter.
- No ordering guarantee across sinks beyond *"fan-out in the declared
  order"* — sinks are independent.
