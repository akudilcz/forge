"""Live progress dashboard for a running FORGE pipeline.

Polls a project's ``forge.db`` on a fixed interval and prints a
one-line summary of phase state plus node counts. Intended for
tailing integration test runs (``tmp_path/forge.db``) without
touching the running process.

Usage::

    uv run python -m backend.scripts.forge_watch <path/to/forge.db>
    uv run python -m backend.scripts.forge_watch --interval 5 <db>
    uv run python -m backend.scripts.forge_watch -v <db>   # plus delta log

The script is read-only — it opens the SQLite file with ``mode=ro``.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

_NODE_TYPES = (
    "PROJECT", "DOCUMENT", "PARA", "HLR", "ARCHITECTURE",
    "MODULE", "CONTRACT", "LLR", "DESIGN", "SUITE",
    "CASE_HLR", "CASE_LLR", "CODE", "TEST", "RESULT",
)
_PHASE_NAMES = {
    0: "Init", 1: "Read", 2: "Chunk", 3: "HLR", 4: "Arch",
    5: "Mods", 6: "Contracts", 7: "LLR", 8: "Design", 9: "Suite",
    10: "Verify", 11: "Docs", 12: "Code", 13: "Sync", 14: "Build",
}


@dataclass
class Snapshot:
    active_phase: int | None
    phase_status: dict[int, str] = field(default_factory=dict)
    node_counts: dict[str, int] = field(default_factory=dict)
    history_row_count: int = 0
    result_counts: dict[str, int] = field(default_factory=dict)


def _connect(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path}?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=2.0)


def _snapshot(conn: sqlite3.Connection) -> Snapshot:
    cur = conn.cursor()

    phase_status: dict[int, str] = {}
    active: int | None = None
    rows = cur.execute(
        "SELECT phase_number, status FROM phase_states ORDER BY phase_number"
    ).fetchall()
    for phase_number, status in rows:
        phase_status[phase_number] = status
        if status == "active":
            active = phase_number

    node_counts: dict[str, int] = {}
    rows = cur.execute(
        "SELECT node_type, COUNT(*) FROM pg_nodes GROUP BY node_type"
    ).fetchall()
    for node_type, count in rows:
        node_counts[node_type] = count

    history_row_count = cur.execute(
        "SELECT COUNT(*) FROM pg_node_history"
    ).fetchone()[0]

    result_counts: dict[str, int] = {}
    rows = cur.execute(
        "SELECT json_extract(properties, '$.status'), COUNT(*) "
        "FROM pg_nodes WHERE node_type = 'RESULT' "
        "GROUP BY json_extract(properties, '$.status')"
    ).fetchall()
    for status, count in rows:
        if status is not None:
            result_counts[status] = count

    return Snapshot(
        active_phase=active,
        phase_status=phase_status,
        node_counts=node_counts,
        history_row_count=history_row_count,
        result_counts=result_counts,
    )


def _format_line(snap: Snapshot, prev: Snapshot | None, now: datetime) -> str:
    phase_tag: str
    if snap.active_phase is None:
        done = sum(1 for s in snap.phase_status.values() if s == "complete")
        total = len(snap.phase_status) or 15
        phase_tag = f"P-/- done={done}/{total}"
    else:
        name = _PHASE_NAMES.get(snap.active_phase, "?")
        phase_tag = f"P{snap.active_phase:02d}/{name}"

    interesting = ("PARA", "HLR", "LLR", "MOD", "CON", "DES", "CASE", "CODE", "TEST", "RES")
    count_map = {
        "PARA": snap.node_counts.get("PARA", 0),
        "HLR": snap.node_counts.get("HLR", 0),
        "LLR": snap.node_counts.get("LLR", 0),
        "MOD": snap.node_counts.get("MODULE", 0),
        "CON": snap.node_counts.get("CONTRACT", 0),
        "DES": snap.node_counts.get("DESIGN", 0),
        "CASE": snap.node_counts.get("CASE_HLR", 0) + snap.node_counts.get("CASE_LLR", 0),
        "CODE": snap.node_counts.get("CODE", 0),
        "TEST": snap.node_counts.get("TEST", 0),
        "RES": snap.node_counts.get("RESULT", 0),
    }
    counts = " ".join(f"{k}={count_map[k]}" for k in interesting)

    delta = ""
    if prev is not None:
        d_hist = snap.history_row_count - prev.history_row_count
        if d_hist:
            delta = f" +{d_hist}Δ"

    results = ""
    if snap.result_counts:
        passed = snap.result_counts.get("passed", 0)
        failed = snap.result_counts.get("failed", 0)
        skipped = snap.result_counts.get("skipped", 0)
        results = f" | tests P={passed} F={failed} S={skipped}"

    ts = now.strftime("%H:%M:%S")
    return f"[{ts}] {phase_tag} | {counts}{delta}{results}"


def _verbose_deltas(
    conn: sqlite3.Connection, since_row_id: int
) -> tuple[list[str], int]:
    """Return newly-added pg_node_history rows since the given id."""
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT id, node_id, change_reason, changed_by, created_at "
        "FROM pg_node_history WHERE id > ? ORDER BY id LIMIT 50",
        (since_row_id,),
    ).fetchall()
    lines: list[str] = []
    last_id = since_row_id
    for row_id, node_id, reason, changed_by, created_at in rows:
        last_id = row_id
        reason_short = (reason or "")[:60]
        lines.append(f"    {created_at[11:19]}  {changed_by}  {node_id}  {reason_short}")
    return lines, last_id


def watch(db_path: Path, interval: float, verbose: bool) -> None:
    if not db_path.exists():
        print(f"Waiting for {db_path} to be created...", flush=True)
        while not db_path.exists():
            time.sleep(1.0)

    prev: Snapshot | None = None
    last_history_id = 0
    while True:
        try:
            with _connect(db_path) as conn:
                snap = _snapshot(conn)
                line = _format_line(snap, prev, datetime.now())
                print(line, flush=True)
                if verbose:
                    new_lines, last_history_id = _verbose_deltas(conn, last_history_id)
                    for line in new_lines:
                        print(line, flush=True)
                prev = snap
                # Stop polling once the final phase is complete
                if snap.phase_status.get(14) == "complete":
                    print("[done] Phase 14 complete", flush=True)
                    return
        except sqlite3.OperationalError as exc:
            # Database may be briefly locked during writes; retry next tick
            print(f"[retry] {exc}", flush=True)
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("db", type=Path, help="Path to forge.db")
    parser.add_argument(
        "--interval", type=float, default=2.0, help="Poll interval in seconds"
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Print newly-created/modified graph nodes each tick",
    )
    args = parser.parse_args(argv)
    try:
        watch(args.db, args.interval, args.verbose)
    except KeyboardInterrupt:
        print("\n[stopped]", flush=True)
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
