"""Tests for backend/scripts/forge_watch.py — the live progress dashboard.

The watcher polls a forge.db read-only; tests build minimal SQLite
fixtures with the three tables it reads (phase_states, pg_nodes,
pg_node_history) and stub out ``time.sleep`` so loops never block.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from backend.scripts.forge_watch import (
    Snapshot,
    _connect,
    _format_line,
    _snapshot,
    _verbose_deltas,
    main,
    watch,
)

_NOW = datetime(2026, 8, 5, 12, 30, 45)


def _make_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE phase_states (phase_number INTEGER, status TEXT)"
        )
        conn.execute(
            "CREATE TABLE pg_nodes (node_type TEXT, properties TEXT)"
        )
        conn.execute(
            "CREATE TABLE pg_node_history ("
            "id INTEGER PRIMARY KEY, node_id TEXT, change_reason TEXT, "
            "changed_by TEXT, created_at TEXT)"
        )
        conn.commit()


def _insert(db: Path, table: str, rows: list[tuple[Any, ...]]) -> None:
    with sqlite3.connect(db) as conn:
        placeholders = ",".join("?" * len(rows[0]))
        conn.executemany(f"INSERT INTO {table} VALUES ({placeholders})", rows)
        conn.commit()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "forge.db"
    _make_db(path)
    return path


# ── _connect ─────────────────────────────────────────────────────────────────


def test_connect_opens_read_only(db_path: Path) -> None:
    conn = _connect(db_path)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("INSERT INTO pg_nodes VALUES ('HLR', '{}')")
    finally:
        conn.close()


# ── _snapshot ────────────────────────────────────────────────────────────────


def test_snapshot_empty_db(db_path: Path) -> None:
    with _connect(db_path) as conn:
        snap = _snapshot(conn)
    assert snap.active_phase is None
    assert snap.phase_status == {}
    assert snap.node_counts == {}
    assert snap.history_row_count == 0
    assert snap.result_counts == {}


def test_snapshot_reads_phases_nodes_history_and_results(db_path: Path) -> None:
    _insert(db_path, "phase_states", [(0, "complete"), (1, "active"), (2, "pending")])
    _insert(db_path, "pg_nodes", [
        ("HLR", "{}"),
        ("HLR", "{}"),
        ("RESULT", '{"status": "passed"}'),
        ("RESULT", '{"status": "failed"}'),
        ("RESULT", '{"status": "passed"}'),
        ("RESULT", "{}"),  # no status → excluded from result_counts
    ])
    _insert(db_path, "pg_node_history", [
        (1, "HLR-0001", "created", "agent-a", "2026-08-05T12:00:00"),
    ])

    with _connect(db_path) as conn:
        snap = _snapshot(conn)

    assert snap.active_phase == 1
    assert snap.phase_status == {0: "complete", 1: "active", 2: "pending"}
    assert snap.node_counts == {"HLR": 2, "RESULT": 4}
    assert snap.history_row_count == 1
    assert snap.result_counts == {"passed": 2, "failed": 1}


# ── _format_line ─────────────────────────────────────────────────────────────


def test_format_line_no_active_phase_and_empty_status() -> None:
    line = _format_line(Snapshot(active_phase=None), None, _NOW)
    assert "[12:30:45]" in line
    assert "P-/- done=0/15" in line  # empty status defaults total to 15
    assert "PARA=0" in line


def test_format_line_no_active_phase_counts_completed() -> None:
    snap = Snapshot(
        active_phase=None,
        phase_status={0: "complete", 1: "complete", 2: "pending"},
    )
    assert "P-/- done=2/3" in _format_line(snap, None, _NOW)


def test_format_line_active_phase_uses_name() -> None:
    snap = Snapshot(active_phase=7)
    assert "P07/LLR" in _format_line(snap, None, _NOW)


def test_format_line_unknown_phase_number_shows_question_mark() -> None:
    snap = Snapshot(active_phase=99)
    assert "P99/?" in _format_line(snap, None, _NOW)


def test_format_line_node_counts_and_case_sum() -> None:
    snap = Snapshot(
        active_phase=9,
        node_counts={"CASE_HLR": 2, "CASE_LLR": 3, "MODULE": 4, "CONTRACT": 1},
    )
    line = _format_line(snap, None, _NOW)
    assert "CASE=5" in line
    assert "MOD=4" in line
    assert "CON=1" in line


def test_format_line_history_delta_against_prev() -> None:
    prev = Snapshot(active_phase=3, history_row_count=10)
    snap = Snapshot(active_phase=3, history_row_count=17)
    assert "+7Δ" in _format_line(snap, prev, _NOW)


def test_format_line_zero_history_delta_omitted() -> None:
    prev = Snapshot(active_phase=3, history_row_count=10)
    snap = Snapshot(active_phase=3, history_row_count=10)
    assert "Δ" not in _format_line(snap, prev, _NOW)


def test_format_line_result_counts_rendered() -> None:
    snap = Snapshot(
        active_phase=10,
        result_counts={"passed": 8, "failed": 2},  # skipped absent → 0
    )
    assert "| tests P=8 F=2 S=0" in _format_line(snap, None, _NOW)


def test_format_line_no_results_section_when_empty() -> None:
    assert "tests" not in _format_line(Snapshot(active_phase=1), None, _NOW)


# ── _verbose_deltas ──────────────────────────────────────────────────────────


def test_verbose_deltas_returns_rows_after_id(db_path: Path) -> None:
    _insert(db_path, "pg_node_history", [
        (1, "HLR-0001", "created", "agent-a", "2026-08-05T12:00:01"),
        (2, "LLR-0002", None, "agent-b", "2026-08-05T12:00:02"),  # None reason
        (3, "LLR-0003", "r" * 100, "agent-c", "2026-08-05T12:00:03"),
    ])
    with _connect(db_path) as conn:
        lines, last_id = _verbose_deltas(conn, 1)
    assert last_id == 3
    assert len(lines) == 2
    assert "12:00:02" in lines[0]
    assert "agent-b" in lines[0]
    assert "LLR-0002" in lines[0]
    # Long reasons truncated to 60 chars.
    assert "r" * 60 in lines[1]
    assert "r" * 61 not in lines[1]


def test_verbose_deltas_no_new_rows(db_path: Path) -> None:
    with _connect(db_path) as conn:
        lines, last_id = _verbose_deltas(conn, 5)
    assert lines == []
    assert last_id == 5


# ── watch loop ───────────────────────────────────────────────────────────────


def _complete_db(path: Path) -> None:
    _make_db(path)
    _insert(path, "phase_states", [(14, "complete")])


def test_watch_stops_when_phase_14_complete(
    db_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _insert(db_path, "phase_states", [(14, "complete")])
    with patch("backend.scripts.forge_watch.time.sleep") as sleep:
        watch(db_path, 2.0, False)
    sleep.assert_not_called()
    out = capsys.readouterr().out
    assert "[done] Phase 14 complete" in out
    assert "P-/- done=1/1" in out


def test_watch_waits_for_db_to_appear(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "forge.db"

    def create_db_on_sleep(_seconds: float) -> None:
        if not db_path.exists():
            _complete_db(db_path)

    with patch(
        "backend.scripts.forge_watch.time.sleep", side_effect=create_db_on_sleep
    ):
        watch(db_path, 2.0, False)
    out = capsys.readouterr().out
    assert f"Waiting for {db_path} to be created..." in out
    assert "[done] Phase 14 complete" in out


def test_watch_verbose_prints_history_lines(
    db_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _insert(db_path, "phase_states", [(14, "complete")])
    _insert(db_path, "pg_node_history", [
        (1, "HLR-0001", "created", "agent-a", "2026-08-05T12:00:01"),
    ])
    with patch("backend.scripts.forge_watch.time.sleep"):
        watch(db_path, 2.0, True)
    out = capsys.readouterr().out
    assert "agent-a" in out
    assert "HLR-0001" in out


def test_watch_retries_on_operational_error(
    db_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _insert(db_path, "phase_states", [(14, "complete")])
    real_connect = _connect
    calls = {"n": 0}

    def flaky(path: Path) -> sqlite3.Connection:
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real_connect(path)

    with (
        patch("backend.scripts.forge_watch._connect", side_effect=flaky),
        patch("backend.scripts.forge_watch.time.sleep") as sleep,
    ):
        watch(db_path, 2.0, False)
    assert calls["n"] == 2
    sleep.assert_called_once_with(2.0)
    out = capsys.readouterr().out
    assert "[retry] database is locked" in out
    assert "[done] Phase 14 complete" in out


def test_watch_polls_until_complete(
    db_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """First tick sees an active phase; completion arrives on tick two."""
    _insert(db_path, "phase_states", [(14, "active")])

    def complete_on_sleep(_seconds: float) -> None:
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE phase_states SET status='complete'")
            conn.commit()
        _insert(db_path, "pg_node_history", [
            (1, "HLR-0001", "created", "agent-a", "2026-08-05T12:00:01"),
        ])

    with patch(
        "backend.scripts.forge_watch.time.sleep", side_effect=complete_on_sleep
    ):
        watch(db_path, 2.0, False)
    out = capsys.readouterr().out
    assert "P14/Build" in out
    assert "+1Δ" in out  # second tick reports the history delta vs prev
    assert "[done] Phase 14 complete" in out


# ── main ─────────────────────────────────────────────────────────────────────


def test_main_parses_args_and_runs_watch(db_path: Path) -> None:
    with patch("backend.scripts.forge_watch.watch") as watch_mock:
        rc = main([str(db_path), "--interval", "5", "-v"])
    assert rc == 0
    watch_mock.assert_called_once_with(db_path, 5.0, True)


def test_main_default_interval_and_non_verbose(db_path: Path) -> None:
    with patch("backend.scripts.forge_watch.watch") as watch_mock:
        rc = main([str(db_path)])
    assert rc == 0
    watch_mock.assert_called_once_with(db_path, 2.0, False)


def test_main_handles_keyboard_interrupt(
    db_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with patch(
        "backend.scripts.forge_watch.watch", side_effect=KeyboardInterrupt
    ):
        rc = main([str(db_path)])
    assert rc == 0
    assert "[stopped]" in capsys.readouterr().out


def test_main_requires_db_argument() -> None:
    with pytest.raises(SystemExit):
        main([])
