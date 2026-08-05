"""Tests for backend.server.forge_logger — sink lifecycle, emit fan-out, coercion.

Uses fresh ForgeLogger instances (never the module singleton) so the
session-scoped SQLite sink wired by conftest is left untouched.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from backend.observability.log_record import LogRecord
from backend.server.forge_logger import ForgeLogger, _coerce


class _CaptureSink:
    """Minimal LogSink capturing every record."""

    def __init__(self) -> None:
        self.records: list[LogRecord] = []
        self.closed = False

    def write(self, record: LogRecord) -> None:
        self.records.append(record)

    def close(self) -> None:
        self.closed = True


def _logger_with_capture() -> tuple[ForgeLogger, _CaptureSink]:
    logger = ForgeLogger()
    sink = _CaptureSink()
    logger.add_sink(sink)
    return logger, sink


# ── initialise / close ──────────────────────────────────────────────────────


class TestLifecycle:
    def test_initialise_wires_default_sinks(self, tmp_path: Path) -> None:
        logger = ForgeLogger()
        logger.initialise(tmp_path / "forge.log", MagicMock())
        try:
            assert logger._file_sink is not None
            assert logger._sqlite_sink is not None
            assert logger._ws_sink is not None
            assert (tmp_path / "forge.log").exists()
            assert (tmp_path / "forge.logs.db").exists()
        finally:
            logger.close()
        assert logger._sinks == []
        assert logger._file_sink is None

    def test_initialise_survives_sqlite_failure(self, tmp_path: Path) -> None:
        logger = ForgeLogger()
        with patch(
            "backend.server.forge_logger.SQLiteLogSink",
            side_effect=RuntimeError("locked"),
        ):
            logger.initialise(tmp_path / "forge.log", MagicMock())
        try:
            assert logger._sqlite_sink is None
            assert logger._file_sink is not None  # other sinks still wired
        finally:
            logger.close()

    def test_close_tolerates_failing_sink(self) -> None:
        logger = ForgeLogger()
        bad = MagicMock()
        bad.close.side_effect = RuntimeError("cannot close")
        good = _CaptureSink()
        logger.add_sink(bad)
        logger.add_sink(good)
        logger.close()  # must not raise
        assert good.closed is True
        assert logger._sinks == []


class TestStdStreams:
    def test_enable_stderr_idempotent(self) -> None:
        logger = ForgeLogger()
        logger.enable_stderr()
        first = logger._stderr_sink
        logger.enable_stderr()
        assert logger._stderr_sink is first
        assert len(logger._sinks) == 1
        logger.disable_stderr()
        assert logger._stderr_sink is None
        assert logger._sinks == []

    def test_disable_stderr_when_not_enabled_is_noop(self) -> None:
        logger = ForgeLogger()
        logger.disable_stderr()
        assert logger._sinks == []

    def test_enable_stdout_idempotent(self) -> None:
        logger = ForgeLogger()
        logger.enable_stdout()
        first = logger._stdout_sink
        logger.enable_stdout()
        assert logger._stdout_sink is first
        assert len(logger._sinks) == 1
        logger.disable_stdout()
        assert logger._stdout_sink is None
        assert logger._sinks == []

    def test_disable_stdout_when_not_enabled_is_noop(self) -> None:
        logger = ForgeLogger()
        logger.disable_stdout()
        assert logger._sinks == []


# ── emit ────────────────────────────────────────────────────────────────────


class TestEmit:
    def test_emit_builds_record_with_promoted_and_extras(self) -> None:
        logger, sink = _logger_with_capture()
        logger.emit(
            "INFO", "PHASE", "hello", "details",
            phase=3, custom_key="custom_value",
        )
        assert len(sink.records) == 1
        rec = sink.records[0]
        assert rec.level == "INFO"
        assert rec.msg == "hello"
        assert rec.detail == "details"
        assert rec.phase == 3
        assert rec.extras == {"custom_key": "custom_value"}

    def test_emit_survives_failing_sink(self) -> None:
        logger, sink = _logger_with_capture()
        bad = MagicMock()
        bad.write.side_effect = RuntimeError("disk full")
        logger._sinks.insert(0, bad)
        logger.emit("INFO", "SYS  ", "still delivered")
        assert sink.records[0].msg == "still delivered"

    def test_emit_coerces_bad_int_meta_to_none(self) -> None:
        logger, sink = _logger_with_capture()
        logger.emit("INFO", "PHASE", "msg", phase="not-a-number", duration_ms="9")
        rec = sink.records[0]
        assert rec.phase is None
        assert rec.duration_ms == 9


# ── convenience helpers ─────────────────────────────────────────────────────


class TestHelpers:
    def _all_msgs(self, sink: _CaptureSink) -> list[str]:
        return [r.msg for r in sink.records]

    def test_loop_and_phase_helpers(self) -> None:
        logger, sink = _logger_with_capture()
        logger.loop_start()
        logger.loop_complete()
        logger.loop_cancelled()
        logger.loop_stop()
        logger.loop_error("boom")
        logger.phase_start(2)
        logger.phase_complete(2)
        logger.phase_no_gaps(2, 1, 0)
        assert len(sink.records) == 8
        assert "Build loop error: boom" in self._all_msgs(sink)

    def test_gap_and_agent_helpers(self) -> None:
        logger, sink = _logger_with_capture()
        logger.gap_dispatch("MISSING_HLR", "n1", 3)
        logger.gap_no_progress("MISSING_HLR", "n1", 2)
        logger.gap_resolved("MISSING_HLR", "n1")
        logger.agent_dispatch("req", "MISSING_HLR", "n1")
        logger.agent_done("req", 1234.5)
        logger.agent_error("req", "TypeError: bad")
        logger.no_agent_for_gap("UNKNOWN_GAP")
        assert len(sink.records) == 7
        assert sink.records[5].level == "ERROR"

    def test_llm_helpers(self) -> None:
        logger, sink = _logger_with_capture()
        logger.llm_call("gpt", "req", 100, context_window=1000)
        logger.llm_call("gpt", "req", 100)  # no context window
        logger.llm_response(
            "gpt", 50, 900.0, "graph_read",
            prompt_tokens=100, total_tokens=150, context_window=1000,
        )
        logger.llm_response("gpt", 50, 900.0, None)
        logger.llm_error("gpt", "RateLimitError: slow down")
        logger.llm_prompt("gpt", [{"role": "user", "content": "hi\nthere"}])
        logger.llm_content("gpt", "an answer", [])
        logger.llm_content(
            "gpt", "",
            [{"function": {"name": "f", "arguments": "{}"}}],
        )
        logger.llm_content("gpt", "", [])  # empty response → WARN
        assert sink.records[-1].level == "WARN"
        assert any("ctx=10%" in m for m in self._all_msgs(sink))

    def test_crew_tool_decision_graph_user_helpers(self) -> None:
        logger, sink = _logger_with_capture()
        logger.crew_thought("thinking\nhard")
        logger.crew_tool_call("graph_add_node", {"node_type": "HLR"})
        logger.crew_tool_result("graph_add_node", "ok")
        logger.crew_finish("done")
        logger.tool_call("file_read", "req")
        logger.tool_result("file_read", True, "content")
        logger.tool_result(
            "file_read", False, "err", full_output="x" * 5000,
        )
        logger.decision("dispatch", "fast-path", "no LLM needed", phase=2)
        logger.graph_write(
            "add", "n1", "HLR", changed_by="req", change_reason="new req",
        )
        logger.user_action("clicked", "reset button")
        logger.user_action("clicked")
        failure = sink.records[6]
        assert failure.level == "WARN"
        assert failure.extras is not None
        assert len(failure.extras["tool_output"]) == 4096  # trailing 4KB kept


# ── _coerce ─────────────────────────────────────────────────────────────────


class TestCoerce:
    def test_int_keys_coerced(self) -> None:
        assert _coerce("phase", "7") == 7
        assert _coerce("duration_ms", 12.9) == 12

    def test_bad_int_returns_none(self) -> None:
        assert _coerce("phase", "seven") is None
        assert _coerce("cycle", None) is None

    def test_other_keys_stringified(self) -> None:
        assert _coerce("model", 42) == "42"
        assert _coerce("model", None) is None
