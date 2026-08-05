"""Tests for the phase timing report over an observability log DB."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.scripts.phase_timing_report import build_report, main


def _make_log_db(path: Path) -> str:
    """Create a minimal logs DB with two phases and mixed categories."""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE logs (id INTEGER PRIMARY KEY, ts_ms INTEGER, level TEXT,"
        " category TEXT, msg TEXT, phase INTEGER, gap_type TEXT,"
        " prompt_tokens INTEGER, completion_tokens INTEGER, duration_ms INTEGER)"
    )
    rows = [
        (1_000, "INFO", "LLM", "call", 2, "STALE_NODE", 20_000, 100, 5_000),
        (7_000, "INFO", "LLM", "call", 2, "STALE_NODE", 21_000, 90, 4_000),
        (12_000, "INFO", "TOOL", "write", 2, None, None, None, 50),
        (20_000, "INFO", "LLM", "call", 3, "UNCOVERED_PARA", 3_000, 40, 2_000),
    ]
    conn.executemany(
        "INSERT INTO logs (ts_ms, level, category, msg, phase, gap_type,"
        " prompt_tokens, completion_tokens, duration_ms)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()
    return str(path)


def test_build_report_summarises_phases_categories_and_gap_types(tmp_path: Path) -> None:
    db = _make_log_db(tmp_path / "logs.db")
    report = build_report(db)
    assert "records=4" in report
    assert "phase 2" in report
    assert "phase 3" in report
    # LLM dominates recorded durations and STALE_NODE is the top gap type
    llm_line = next(line for line in report.splitlines() if line.strip().startswith("LLM"))
    assert "n=    3" in llm_line
    stale_line = next(line for line in report.splitlines() if "STALE_NODE" in line)
    assert "prompt_tok=41000" in stale_line


def test_build_report_empty_db_raises(tmp_path: Path) -> None:
    db = tmp_path / "empty.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE logs (id INTEGER PRIMARY KEY, ts_ms INTEGER, level TEXT,"
                 " category TEXT, msg TEXT, phase INTEGER, gap_type TEXT,"
                 " prompt_tokens INTEGER, completion_tokens INTEGER, duration_ms INTEGER)")
    conn.close()
    with pytest.raises(ValueError, match="no records"):
        build_report(str(db))


def test_main_requires_exactly_one_argument() -> None:
    assert main(["prog"]) == 2


def test_main_prints_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = _make_log_db(tmp_path / "logs.db")
    assert main(["prog", db]) == 0
    out = capsys.readouterr().out
    assert "timing report" in out
