"""Integration smoke tests for the observability stack.

These drive the full pipeline (graph engine, logger sinks, SQLite
persistence, query API) in an end-to-end way without spinning up a
server or talking to an LLM. They answer: "if I run a realistic
sequence of events, does the expected record trail land in the
database and come back correctly via the query API?"
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from backend.observability import (
    LogCategory,
    SQLiteLogSink,
    log_context,
    new_call_id,
    new_run_id,
    query_logs,
)
from backend.server.forge_logger import forge_logger


@pytest.fixture
def tmp_log_db(tmp_path: Path) -> Iterator[Path]:
    """A fresh SQLite log DB with the ForgeLogger singleton wired to it."""
    db = tmp_path / "forge.logs.db"
    sink = SQLiteLogSink(db, flush_interval_s=0.02)
    forge_logger.add_sink(sink)
    yield db
    if sink in forge_logger._sinks:
        forge_logger._sinks.remove(sink)
    sink.close()


# ── A realistic phase-run record trail ──────────────────────────────────────


def test_full_phase_run_produces_correlated_record_trail(tmp_log_db: Path) -> None:
    """Simulate the full sequence of events a real phase run would emit
    and verify every record lands in the DB keyed by the same run_id.
    """
    run_id = new_run_id()
    with log_context(run_id=run_id):
        forge_logger.loop_start()
        with log_context(phase=3):
            forge_logger.phase_start(3)
            with log_context(cycle=1):
                with log_context(
                    gap_type="UNCOVERED_PARA",
                    node_id="PARA-0001",
                    gap_id="UNCOVERED_PARA:PARA-0001:1",
                ):
                    forge_logger.decision(
                        "dispatch", "agent_dispatch",
                        "role=requirements_engineer", attempt=1,
                    )
                    forge_logger.agent_dispatch(
                        "requirements_engineer", "UNCOVERED_PARA", "PARA-0001",
                    )
                    call_id = new_call_id()
                    with log_context(call_id=call_id, model="claude-opus-4-7"):
                        forge_logger.emit(
                            "INFO", LogCategory.LLM, "response", duration_ms=1200,
                            prompt_tokens=800, completion_tokens=200,
                            tool_call_count=1, model="claude-opus-4-7",
                            call_id=call_id,
                        )
                        forge_logger.emit(
                            "INFO", LogCategory.TOOL, "graph_add_node",
                            tool_name="graph_add_node", duration_ms=8,
                            call_id=call_id,
                        )
                    forge_logger.graph_write(
                        "add_node", "HLR-0001", "HLR",
                        changed_by="agent",
                        change_reason="derived from PARA-0001",
                    )
                    forge_logger.gap_resolved("UNCOVERED_PARA", "PARA-0001")
            forge_logger.phase_complete(3)
        forge_logger.loop_complete()

    # Give the background writer a moment to drain, then snapshot.
    _wait_for_drain(tmp_log_db, expected_at_least=10)

    # Every record should have the same run_id.
    all_for_run = query_logs(tmp_log_db, run_id=run_id, limit=5000)
    assert all_for_run["total"] >= 10

    records = all_for_run["records"]
    categories = {r["category"] for r in records}
    assert {"LOOP", "PHASE", "DECIDE", "AGENT", "LLM", "TOOL", "GRAPH", "GAP"} <= categories

    # The LLM response we emitted should carry promoted columns.
    llm_rows = [r for r in records if r["category"] == "LLM"]
    assert any(
        r["duration_ms"] == 1200 and r["prompt_tokens"] == 800
        and r["completion_tokens"] == 200
        for r in llm_rows
    )

    # Phase filter should narrow the set.
    phase_3 = query_logs(tmp_log_db, run_id=run_id, phase=3, limit=5000)
    assert phase_3["total"] >= 8


def test_filter_by_gap_type_returns_only_that_gap(tmp_log_db: Path) -> None:
    run_id = new_run_id()
    with log_context(run_id=run_id):
        with log_context(gap_type="UNDESIGNED", node_id="LLR-1"):
            forge_logger.emit("INFO", LogCategory.GAP, "dispatching UNDESIGNED")
        with log_context(gap_type="UNCOVERED_PARA", node_id="PARA-1"):
            forge_logger.emit("INFO", LogCategory.GAP, "dispatching UNCOVERED_PARA")

    _wait_for_drain(tmp_log_db, expected_at_least=2)
    undesigned = query_logs(tmp_log_db, gap_type="UNDESIGNED", run_id=run_id)
    assert undesigned["total"] == 1
    assert undesigned["records"][0]["node_id"] == "LLR-1"


def test_call_id_filter_isolates_one_llm_call(tmp_log_db: Path) -> None:
    """`call_id` should let you trace one LLM call end-to-end across
    the request, the tool calls it made, and the response."""
    run_id = new_run_id()
    call_id = new_call_id()
    with log_context(run_id=run_id, call_id=call_id, model="claude-opus-4-7"):
        forge_logger.emit("INFO", LogCategory.LLM, "call start", model="claude-opus-4-7")
        forge_logger.emit(
            "INFO", LogCategory.TOOL, "graph_read",
            tool_name="graph_read", duration_ms=5,
        )
        forge_logger.emit(
            "INFO", LogCategory.LLM, "response",
            duration_ms=900, completion_tokens=120,
        )

    _wait_for_drain(tmp_log_db, expected_at_least=3)
    just_this_call = query_logs(tmp_log_db, call_id=call_id)
    assert just_this_call["total"] == 3
    assert all(r["call_id"] == call_id for r in just_this_call["records"])


def test_level_filter_returns_only_errors(tmp_log_db: Path) -> None:
    run_id = new_run_id()
    with log_context(run_id=run_id):
        forge_logger.emit("INFO", LogCategory.SYS, "ok")
        forge_logger.emit(
            "ERROR", LogCategory.FLOW, "something broke",
            error_type="RuntimeError",
        )
        forge_logger.emit("WARN", LogCategory.BATCH, "retry scheduled")

    _wait_for_drain(tmp_log_db, expected_at_least=3)
    errors = query_logs(tmp_log_db, level=["ERROR"], run_id=run_id)
    assert errors["total"] == 1
    assert errors["records"][0]["error_type"] == "RuntimeError"


def test_time_window_filter(tmp_log_db: Path) -> None:
    run_id = new_run_id()
    with log_context(run_id=run_id):
        forge_logger.emit("INFO", LogCategory.SYS, "recent event")

    _wait_for_drain(tmp_log_db, expected_at_least=1)
    # All our events are within the last minute; -5m should include them.
    recent = query_logs(tmp_log_db, run_id=run_id, since="-5m")
    assert recent["total"] >= 1
    # A window in the future should be empty.
    absent = query_logs(tmp_log_db, run_id=run_id, since="-0s", until="-1s")
    assert absent["total"] == 0


# ── Helpers ──────────────────────────────────────────────────────────────────


def _wait_for_drain(
    db_path: Path, expected_at_least: int, timeout_s: float = 2.0,
) -> None:
    """Poll the DB until the expected row count lands, or timeout."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        result = query_logs(db_path, limit=1)
        if result["total"] >= expected_at_least:
            return
        time.sleep(0.02)
    raise AssertionError(
        f"Only {query_logs(db_path)['total']} rows persisted; "
        f"expected >= {expected_at_least}"
    )
