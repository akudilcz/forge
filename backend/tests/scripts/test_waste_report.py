"""Tests for the waste report over a build graph DB and observability log DB."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from backend.scripts.waste_report import build_report, main


def _make_forge_db(path: Path) -> str:
    """Create a minimal forge DB with live, churned, and discarded nodes."""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE pg_nodes (node_id TEXT PRIMARY KEY, node_type TEXT,"
        " layer INTEGER, title TEXT, content TEXT, content_hash TEXT,"
        " version INTEGER, parent_id TEXT, lifecycle TEXT, created_by TEXT,"
        " created_at TEXT, updated_at TEXT, properties TEXT, trace_to TEXT)"
    )
    conn.execute(
        "CREATE TABLE pg_node_history (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " node_id TEXT, version INTEGER, content_hash TEXT, content TEXT,"
        " properties TEXT, lifecycle TEXT, changed_by TEXT, changed_at TEXT,"
        " change_reason TEXT)"
    )
    conn.execute(
        "CREATE TABLE pg_edges (edge_id TEXT PRIMARY KEY, edge_type TEXT,"
        " source_id TEXT, target_id TEXT, rationale TEXT, confidence REAL,"
        " created_by TEXT, created_at TEXT)"
    )
    conn.execute(
        "CREATE TABLE phase_states (phase_number INTEGER PRIMARY KEY,"
        " name TEXT, status TEXT, updated_at TEXT)"
    )
    nodes = [
        ("HLR-0001", "HLR", "active"),
        ("LLR-0002", "LLR", "draft"),
        ("HLR-0009", "HLR", "deleted"),
    ]
    for node_id, node_type, lifecycle in nodes:
        conn.execute(
            "INSERT INTO pg_nodes (node_id, node_type, layer, title, content,"
            " content_hash, version, lifecycle, created_by, created_at,"
            " updated_at, properties, trace_to)"
            " VALUES (?, ?, 0, '', '', '', 1, ?, 'agent', 't', 't', '{}', '[]')",
            (node_id, node_type, lifecycle),
        )
    history = [
        # LLR-0002 churn: 5 versions, one no-op pair (v2 -> v3 same hash).
        ("LLR-0002", 1, "h1", "agent", "Initial draft"),
        ("LLR-0002", 2, "h2", "agent", "Rewrote to required format"),
        ("LLR-0002", 3, "h2", "agent", "Verified the requirement already conforms"),
        ("LLR-0002", 4, "h3", "agent", "Tighten wording"),
        ("LLR-0002", 5, "h4", "agent", "Final polish"),
        # DEL-0003 exists only in history: created then deleted.
        ("DEL-0003", 1, "hx", "agent", "Initial draft"),
        ("DEL-0003", 2, "hy", "agent", "Rework"),
        # HLR-0009 is lifecycle-deleted in pg_nodes.
        ("HLR-0009", 1, "hz", "agent", "Initial draft"),
    ]
    conn.executemany(
        "INSERT INTO pg_node_history (node_id, version, content_hash, content,"
        " properties, lifecycle, changed_by, changed_at, change_reason)"
        " VALUES (?, ?, ?, '', '{}', 'draft', ?, 't', ?)",
        history,
    )
    conn.commit()
    conn.close()
    return str(path)


def _make_logs_db(path: Path) -> str:
    """Create a logs DB exercising repeat, discarded, and oversized calls."""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE logs (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " ts_ms INTEGER, level TEXT, category TEXT, msg TEXT, phase INTEGER,"
        " gap_type TEXT, gap_id TEXT, node_id TEXT, agent_id TEXT, model TEXT,"
        " prompt_tokens INTEGER, completion_tokens INTEGER, duration_ms INTEGER)"
    )
    rows = [
        # (ts, category, phase, gap_type, gap_id, node_id, prompt, completion)
        # First dispatch of REWORK:DEL-0003 — discarded-node work (cat 2).
        (500, "LLM", 2, "REWORK", "REWORK:DEL-0003:1", "DEL-0003", 300, 30),
        # First dispatch of MALFORMED key: two calls, not waste.
        (1000, "LLM", 2, "MALFORMED_REQUIREMENT",
         "MALFORMED_REQUIREMENT:HLR-0001:1", "HLR-0001", 1000, 100),
        (2000, "LLM", 2, "MALFORMED_REQUIREMENT",
         "MALFORMED_REQUIREMENT:HLR-0001:1", "HLR-0001", 1100, 50),
        # Re-dispatch of the same key (cat 1): 550 wasted tokens.
        (3000, "LLM", 2, "MALFORMED_REQUIREMENT",
         "MALFORMED_REQUIREMENT:HLR-0001:2", "HLR-0001", 500, 50),
        # Discarded-node calls (cat 2): 770 + 220 wasted tokens.
        (4000, "LLM", 3, "INADEQUATE_CONTENT",
         "INADEQUATE_CONTENT:DEL-0003:1", "DEL-0003", 700, 70),
        (5000, "LLM", 3, None, None, "HLR-0009", 200, 20),
        # Oversized call (cat 4): excess = 40000 - 30000 = 10000.
        (6000, "LLM", 4, "UNCOVERED_PARA",
         "UNCOVERED_PARA:HLR-0001:1", "HLR-0001", 40_000, 10),
        # Triple-qualified call: discarded node AND re-dispatch AND oversized.
        # Must count once, as cat 2 (full 50050 tokens).
        (7000, "LLM", 4, "REWORK", "REWORK:DEL-0003:2", "DEL-0003", 50_000, 50),
        # Non-LLM row: ignored entirely.
        (8000, "TOOL", 2, None, None, "HLR-0001", None, None),
    ]
    conn.executemany(
        "INSERT INTO logs (ts_ms, level, category, msg, phase, gap_type,"
        " gap_id, node_id, prompt_tokens, completion_tokens)"
        " VALUES (?, 'INFO', ?, 'call', ?, ?, ?, ?, ?, ?)",
        [(ts, cat, ph, gt, gi, ni, pt, ct) for ts, cat, ph, gt, gi, ni, pt, ct in rows],
    )
    conn.commit()
    conn.close()
    return str(path)


@pytest.fixture
def report(tmp_path: Path) -> str:
    forge = _make_forge_db(tmp_path / "forge.db")
    logs = _make_logs_db(tmp_path / "logs.db")
    return build_report(forge, logs, 30_000)


def test_repeat_work_counts_redispatches(report: str) -> None:
    # Two keys re-dispatched: MALFORMED...:HLR-0001 and REWORK:DEL-0003.
    assert "keys dispatched 2x: 2" in report
    # Re-dispatch calls: id4 (550 tok) and the triple call (50050 tok).
    assert "re-dispatch calls: 2" in report
    assert "re-dispatch tokens: 50600" in report
    assert "MALFORMED_REQUIREMENT:HLR-0001" in report


def test_discarded_work_counts_deleted_nodes_and_tokens(report: str) -> None:
    # DEL-0003 (history-only) and HLR-0009 (lifecycle deleted).
    assert "discarded nodes: 2" in report
    assert "DEL: 1" in report
    assert "HLR: 1" in report
    # Calls on DEL-0003 (300+30, 700+70, 50000+50) + HLR-0009 (200+20).
    assert "tokens on discarded nodes: 51370" in report


def test_churn_lists_high_version_nodes(report: str) -> None:
    assert "churned nodes (>3 versions): 1" in report
    assert "LLR-0002" in report
    assert "5 versions" in report


def test_noop_history_counts_same_hash_consecutive_versions(report: str) -> None:
    assert "no-op history entries: 1" in report
    assert "by agent: 1" in report
    assert "Verified the requirement already conform" in report


def test_oversized_calls_report_excess(report: str) -> None:
    # Two calls above 30000: the 40000 one and the 50000 triple call.
    assert "oversized calls (>30000 prompt tokens): 2" in report
    assert "total excess tokens: 30000" in report
    assert "UNCOVERED_PARA" in report


def test_summary_counts_each_call_once_with_priority(report: str) -> None:
    # Total tokens over all 8 LLM calls.
    assert "total LLM tokens: 94180" in report
    # Waste: cat2 = 51370 (incl. triple call once), cat1 = 550,
    # cat4 excess = 10000 (40000-call only; triple call already cat 2).
    assert "wasted tokens: 61920" in report
    assert "waste: 65.7%" in report


def test_empty_logs_db_raises(tmp_path: Path) -> None:
    forge = _make_forge_db(tmp_path / "forge.db")
    empty = tmp_path / "empty_logs.db"
    conn = sqlite3.connect(empty)
    conn.execute("CREATE TABLE logs (id INTEGER PRIMARY KEY, ts_ms INTEGER,"
                 " category TEXT, gap_type TEXT, gap_id TEXT, node_id TEXT,"
                 " phase INTEGER, prompt_tokens INTEGER, completion_tokens INTEGER)")
    conn.close()
    with pytest.raises(ValueError, match="no records"):
        build_report(forge, str(empty), 30_000)


def test_empty_forge_db_raises(tmp_path: Path) -> None:
    logs = _make_logs_db(tmp_path / "logs.db")
    empty = tmp_path / "empty_forge.db"
    conn = sqlite3.connect(empty)
    conn.execute("CREATE TABLE pg_nodes (node_id TEXT PRIMARY KEY, node_type TEXT,"
                 " lifecycle TEXT)")
    conn.execute("CREATE TABLE pg_node_history (id INTEGER PRIMARY KEY,"
                 " node_id TEXT, version INTEGER, content_hash TEXT,"
                 " changed_by TEXT, change_reason TEXT)")
    conn.close()
    with pytest.raises(ValueError, match="no nodes"):
        build_report(str(empty), logs, 30_000)


def test_main_requires_exactly_three_arguments() -> None:
    assert main(["prog"]) == 2
    assert main(["prog", "a", "b"]) == 2


def test_main_prints_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    forge = _make_forge_db(tmp_path / "forge.db")
    logs = _make_logs_db(tmp_path / "logs.db")
    assert main(["prog", forge, logs, "30000"]) == 0
    out = capsys.readouterr().out
    assert "waste report" in out
