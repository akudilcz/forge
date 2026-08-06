"""Behavioural tests for run-artifact persistence (specs/11).

At end of run the flow copies this process's SQLite logs DB(s) and its
llm_trace JSONL into ``<workspace>/.forge`` next to ``forge.db`` — pytest
churn used to delete the per-PID repo-level files before analysis (12 of
14 builds). Missing sources produce loud WARNs, never silent skips.
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from backend.pipeline.flow import ForgeFlow

from backend.observability.log_record import LogRecord
from backend.observability.log_sinks import SQLiteLogSink
from backend.observability.run_artifacts import persist_run_artifacts
from backend.server.forge_logger import forge_logger


def _record(msg: str) -> LogRecord:
    return LogRecord(ts_ms=1, level="INFO", category="SYS", msg=msg)


@pytest.fixture
def _sink(tmp_path: Path) -> Iterator[SQLiteLogSink]:
    """A real attached sink whose DB holds one flushed record."""
    sink = SQLiteLogSink(tmp_path / "logs" / "forge.test.logs.123.db")
    sink.write(_record("hello"))
    sink.close()  # flush; forge_logger.sqlite_db_paths still sees it
    forge_logger.add_sink(sink)
    yield sink
    if sink in forge_logger._sinks:
        forge_logger._sinks.remove(sink)


class TestPersistRunArtifacts:
    def test_copies_logs_db_and_trace_into_workspace_forge_dir(
        self, tmp_path: Path, _sink: SQLiteLogSink
    ) -> None:
        workspace = tmp_path / "ws"
        trace_dir = tmp_path / "traces"
        trace_dir.mkdir()
        trace_file = trace_dir / f"trace.{os.getpid()}.jsonl"
        trace_file.write_text('{"call_id": "c1"}\n', encoding="utf-8")

        copied = persist_run_artifacts(workspace, str(trace_dir))

        dest_db = workspace / ".forge" / "forge.test.logs.123.db"
        dest_trace = workspace / ".forge" / trace_file.name
        assert dest_db in copied
        assert dest_trace in copied
        with sqlite3.connect(dest_db) as conn:
            (count,) = conn.execute("SELECT COUNT(*) FROM logs").fetchone()
        assert count == 1
        assert dest_trace.read_text(encoding="utf-8") == '{"call_id": "c1"}\n'

    def test_missing_trace_file_warns_loudly(
        self, tmp_path: Path, _sink: SQLiteLogSink
    ) -> None:
        workspace = tmp_path / "ws"
        empty_trace_dir = tmp_path / "no-traces"

        with patch("backend.observability.run_artifacts.forge_logger") as mock_log:
            mock_log.sqlite_db_paths.return_value = [_sink.db_path]
            persist_run_artifacts(workspace, str(empty_trace_dir))

        warns = [c for c in mock_log.emit.call_args_list if c[0][0] == "WARN"]
        assert any("llm_trace missing" in c[0][2] for c in warns)

    def test_no_sqlite_sink_warns_loudly(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"

        with patch("backend.observability.run_artifacts.forge_logger") as mock_log:
            mock_log.sqlite_db_paths.return_value = []
            persist_run_artifacts(workspace, None)

        warns = [c[0][2] for c in mock_log.emit.call_args_list if c[0][0] == "WARN"]
        assert any("no SQLite log sink" in msg for msg in warns)
        assert any("llm_trace location unknown" in msg for msg in warns)

    def test_missing_logs_db_file_warns_loudly(self, tmp_path: Path) -> None:
        workspace = tmp_path / "ws"

        with patch("backend.observability.run_artifacts.forge_logger") as mock_log:
            mock_log.sqlite_db_paths.return_value = [str(tmp_path / "gone.db")]
            persist_run_artifacts(workspace, None)

        warns = [c[0][2] for c in mock_log.emit.call_args_list if c[0][0] == "WARN"]
        assert any("logs DB missing" in msg for msg in warns)


class TestFlowEndOfRunHook:
    """kickoff_async persists artifacts on every exit path."""

    def _flow(self, tmp_path: Path) -> ForgeFlow:
        from backend.pipeline.flow import ForgeFlow

        config = MagicMock()
        config.project.workspace_dir = str(tmp_path / "ws")
        config.llm.trace_dir = str(tmp_path / "traces")
        return ForgeFlow(
            pool=MagicMock(),
            graph=MagicMock(),
            config=config,
            broadcaster=MagicMock(),
            phase_store=MagicMock(),
            workspace=tmp_path / "ws",
        )

    async def test_persists_on_successful_completion(self, tmp_path: Path) -> None:
        flow = self._flow(tmp_path)
        flow.state.start_phase = 1
        flow.state.end_phase = 0  # empty range: completes immediately
        with patch(
            "backend.observability.run_artifacts.persist_run_artifacts"
        ) as mock_persist:
            await flow.kickoff_async()
        mock_persist.assert_called_once_with(
            tmp_path / "ws", str(tmp_path / "traces")
        )

    async def test_persists_even_when_a_phase_errors(self, tmp_path: Path) -> None:
        flow = self._flow(tmp_path)
        flow.state.start_phase = 1
        flow.state.end_phase = 1
        with (
            patch.object(flow, "_run_phase", AsyncMock(side_effect=RuntimeError("halt"))),
            patch(
                "backend.observability.run_artifacts.persist_run_artifacts"
            ) as mock_persist,
        ):
            await flow.kickoff_async()
        assert flow.state.loop_status == "error"
        mock_persist.assert_called_once()

    async def test_persistence_failure_is_loud_but_not_fatal(
        self, tmp_path: Path
    ) -> None:
        flow = self._flow(tmp_path)
        flow.state.start_phase = 1
        flow.state.end_phase = 0
        with (
            patch(
                "backend.observability.run_artifacts.persist_run_artifacts",
                side_effect=OSError("disk full"),
            ),
            patch("backend.pipeline.flow.forge_logger") as mock_log,
        ):
            await flow.kickoff_async()
        errors = [
            c for c in mock_log.emit.call_args_list
            if c[0][0] == "ERROR" and "persistence failed" in c[0][2]
        ]
        assert errors
