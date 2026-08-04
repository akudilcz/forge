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

_logger = logging.getLogger(__name__)


class ForgeLogger:
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

    # ------------------------------------------------------------------
    # Loop lifecycle
    # ------------------------------------------------------------------

    def loop_start(self) -> None:
        """Emit a log entry when the build loop begins."""
        self.emit("INFO", "LOOP ", "Build loop started")

    def loop_complete(self) -> None:
        """Emit a log entry when the build loop finishes all phases."""
        self.emit("INFO", "LOOP ", "Build loop complete")

    def loop_cancelled(self) -> None:
        """Emit a log entry when the build loop is cancelled."""
        self.emit("INFO", "LOOP ", "Build loop cancelled")

    def loop_stop(self) -> None:
        """Emit a log entry when the loop stops after resolving one gap."""
        self.emit("INFO", "LOOP ", "Build loop stopped — single gap resolved")

    def loop_error(self, error: str) -> None:
        """Emit an error entry when the build loop exits due to an unhandled exception."""
        self.emit("ERROR", "LOOP ", f"Build loop error: {error}", error_type=type(error).__name__ if isinstance(error, BaseException) else None)

    # ------------------------------------------------------------------
    # Phase
    # ------------------------------------------------------------------

    def phase_start(self, phase: int) -> None:
        self.emit("INFO", "PHASE", f"Phase {phase} started", phase=phase)

    def phase_complete(self, phase: int) -> None:
        self.emit("INFO", "PHASE", f"Phase {phase} complete", phase=phase)

    def phase_no_gaps(self, phase: int, iteration: int, skipped: int) -> None:
        self.emit(
            "INFO",
            "PHASE",
            f"Phase {phase} no gaps (iter={iteration} skipped={skipped})",
            phase=phase,
            cycle=iteration,
            skipped=skipped,
        )

    # ------------------------------------------------------------------
    # Gap
    # ------------------------------------------------------------------

    def gap_dispatch(self, gap_type: str, node_id: str, phase: int) -> None:
        self.emit(
            "INFO",
            "GAP  ",
            f"Dispatch {gap_type} → {node_id} (phase {phase})",
            gap_type=gap_type,
            node_id=node_id,
            phase=phase,
        )

    def gap_no_progress(self, gap_type: str, node_id: str, consecutive: int) -> None:
        self.emit(
            "WARN",
            "GAP  ",
            f"No progress on {gap_type}:{node_id} (attempt {consecutive})",
            gap_type=gap_type,
            node_id=node_id,
            attempt=consecutive,
        )

    def gap_resolved(self, gap_type: str, node_id: str) -> None:
        self.emit(
            "INFO",
            "GAP  ",
            f"Resolved {gap_type}:{node_id}",
            gap_type=gap_type,
            node_id=node_id,
        )

    # ------------------------------------------------------------------
    # Agent
    # ------------------------------------------------------------------

    def agent_dispatch(self, role: str, gap_type: str, node_id: str) -> None:
        self.emit(
            "INFO",
            "AGENT",
            f"{role} ← {gap_type}:{node_id}",
            agent_id=role,
            gap_type=gap_type,
            node_id=node_id,
        )

    def agent_done(self, role: str, elapsed_ms: float) -> None:
        self.emit(
            "INFO",
            "AGENT",
            f"{role} done ({elapsed_ms:.0f} ms)",
            agent_id=role,
            duration_ms=int(elapsed_ms),
        )

    def agent_error(self, role: str, error: str) -> None:
        self.emit(
            "ERROR",
            "AGENT",
            f"{role} error: {error}",
            agent_id=role,
            error_type=error.split(":")[0] if ":" in error else None,
        )

    def no_agent_for_gap(self, gap_type: str) -> None:
        self.emit(
            "WARN",
            "AGENT",
            f"No agent registered for {gap_type}",
            gap_type=gap_type,
        )

    # ------------------------------------------------------------------
    # LLM
    # ------------------------------------------------------------------

    def llm_call(
        self,
        model: str,
        agent: str,
        prompt_tokens: int,
        context_window: int = 0,
    ) -> None:
        ctx_str = ""
        if context_window > 0:
            pct = (prompt_tokens / context_window) * 100
            ctx_str = f" ctx={pct:.0f}%"
        self.emit(
            "INFO",
            "LLM  ",
            f"→ {model} [{agent}] ~{prompt_tokens}t{ctx_str}",
            model=model,
            agent_id=agent,
            prompt_tokens=prompt_tokens,
            context_window=context_window or None,
        )

    def llm_response(
        self,
        model: str,
        completion_tokens: int,
        elapsed_ms: float,
        tool: str | None,
        *,
        prompt_tokens: int = 0,
        total_tokens: int = 0,
        context_window: int = 0,
    ) -> None:
        tool_str = f" tool={tool}" if tool else ""
        ctx_str = ""
        extras: dict[str, Any] = {}
        if context_window > 0 and prompt_tokens > 0:
            pct = (prompt_tokens / context_window) * 100
            ctx_str = f" ctx={pct:.0f}% ({prompt_tokens}/{context_window})"
            extras["context_window"] = context_window
        if total_tokens:
            extras["total_tokens"] = total_tokens
        self.emit(
            "INFO",
            "LLM  ",
            f"← {model} {completion_tokens}t{tool_str} {elapsed_ms:.0f}ms{ctx_str}",
            model=model,
            prompt_tokens=prompt_tokens or None,
            completion_tokens=completion_tokens,
            duration_ms=int(elapsed_ms),
            tool_name=tool,
            **extras,
        )

    def llm_error(self, model: str, error: str) -> None:
        self.emit(
            "ERROR",
            "LLM  ",
            f"✗ {model}: {error}",
            model=model,
            error_type=error.split(":")[0] if ":" in error else None,
        )

    # ------------------------------------------------------------------
    # LLM prompt / response content (full diagnostic detail)
    # ------------------------------------------------------------------

    def llm_prompt(self, model: str, messages: list[dict[str, Any]]) -> None:
        last_user = next(
            (m.get("content") or "" for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        snippet = last_user.replace("\n", " ↵ ")
        self.emit(
            "INFO",
            "LLM  ",
            f"prompt → {model}  {snippet}",
            model=model,
            message_count=len(messages),
        )

    def llm_content(self, model: str, content: str, tool_calls: list[dict[str, Any]]) -> None:
        snippet = (content or "").strip().replace("\n", " ↵ ")
        if tool_calls:
            for tc in tool_calls:
                fn = tc.get("function") or {}
                args = str(fn.get("arguments") or "")
                self.emit(
                    "INFO",
                    "LLM  ",
                    f"← tool_call {fn.get('name')}({args})",
                    model=model,
                    tool_name=fn.get("name"),
                    tool_call_count=len(tool_calls),
                )
        elif snippet:
            self.emit("INFO", "LLM  ", f"← text  {snippet}", model=model)
        else:
            self.emit("WARN", "LLM  ", f"← {model} empty response", model=model)

    # ------------------------------------------------------------------
    # CrewAI step (tool invocations and final outputs)
    # ------------------------------------------------------------------

    def crew_thought(self, thought: str) -> None:
        snippet = thought.strip().replace("\n", " ↵ ")
        self.emit("INFO", "CREW ", f"thought: {snippet}")

    def crew_tool_call(self, tool: str, tool_input: Any) -> None:
        """Log an outgoing tool call.

        Obs #9: ``tool_input`` is preserved as structured JSON in
        ``extras.tool_args`` so later filtering ("calls where graph_add_node
        was invoked with node_type=HLR") doesn't need substring matches.
        The ``msg`` still carries a truncated stringified form for the
        human-readable file / WS feeds.
        """
        snippet = str(tool_input)[:400].replace("\n", " ↵ ")
        self.emit(
            "INFO", "CREW ", f"→ {tool}({snippet})",
            tool_name=tool,
            tool_args=tool_input if isinstance(tool_input, (dict, list)) else str(tool_input),
        )

    def crew_tool_result(self, tool: str, result: str) -> None:
        snippet = str(result).replace("\n", " ↵ ")
        self.emit(
            "INFO", "CREW ", f"← {tool}: {snippet}",
            tool_name=tool,
        )

    def crew_finish(self, output: str) -> None:
        snippet = str(output).strip().replace("\n", " ↵ ")
        self.emit("INFO", "CREW ", f"finish: {snippet}")

    # ------------------------------------------------------------------
    # Tool
    # ------------------------------------------------------------------

    def tool_call(self, tool_name: str, agent: str) -> None:
        self.emit(
            "INFO", "TOOL ", f"{agent} → {tool_name}",
            tool_name=tool_name, agent_id=agent,
        )

    def tool_result(
        self,
        tool_name: str,
        success: bool,
        snippet: str = "",
        *,
        full_output: str | None = None,
    ) -> None:
        level = "INFO" if success else "WARN"
        status = "ok" if success else "err"
        suffix = f" → {snippet}" if snippet else ""
        # Obs #4: On failure, attach the trailing ~4KB of full output to
        # extras so agents (and operators) can diagnose without re-running.
        extras_kw: dict[str, Any] = {}
        if not success and full_output:
            extras_kw["tool_output"] = full_output[-4096:]
        self.emit(
            level, "TOOL ", f"{tool_name} [{status}]{suffix}",
            tool_name=tool_name, tool_success=success,
            **extras_kw,
        )

    # ------------------------------------------------------------------
    # Decision points (explicit "why we chose X")
    # ------------------------------------------------------------------

    def decision(self, category: str, choice: str, reason: str, **extras: Any) -> None:
        """Emit a structured decision record — used to trace why a particular
        path was taken (fast-path vs agent, reparent vs create, etc.)."""
        self.emit(
            "INFO",
            "DECIDE",
            f"[{category}] {choice} — {reason}",
            decision_category=category,
            choice=choice,
            reason=reason,
            **extras,
        )

    # ------------------------------------------------------------------
    # Graph writes
    # ------------------------------------------------------------------

    def graph_write(
        self,
        op: str,
        node_id: str,
        node_type: str,
        *,
        changed_by: str = "",
        change_reason: str = "",
        **extras: Any,
    ) -> None:
        """Record a graph mutation: add/update/delete/reparent/add_edge/remove_edge."""
        self.emit(
            "INFO",
            "GRAPH",
            f"{op} {node_type} {node_id}"
            + (f" — {change_reason}" if change_reason else ""),
            node_id=node_id,
            node_type=node_type,
            graph_op=op,
            changed_by=changed_by,
            **extras,
        )

    # ------------------------------------------------------------------
    # User actions
    # ------------------------------------------------------------------

    def user_action(self, action: str, detail: str | None = None) -> None:
        msg = f"{action}" + (f" — {detail}" if detail else "")
        self.emit("INFO", "USER ", msg)


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
