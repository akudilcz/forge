"""Waste report over a FORGE build graph DB and observability log DB.

Quantifies LLM work that did not contribute to the finished build:

1. REPEAT WORK   — gap keys ``(gap_type, node_id)`` dispatched more than once
                   (dispatches are distinct ``gap_id`` values); every LLM call
                   on a dispatch after the first is waste.
2. DISCARDED     — nodes created then deleted (present in ``pg_node_history``
                   but absent from ``pg_nodes``, or lifecycle ``deleted``);
                   all LLM calls on those nodes are waste. Also node churn:
                   nodes rewritten more than three times.
3. NO-OP WORK    — history entries whose content_hash equals the previous
                   version's (metadata-only rewrites).
4. OVERSIZED     — LLM calls whose prompt exceeds a caller-chosen threshold
                   (30000 is a reasonable choice); the excess is waste.
5. SUMMARY       — total vs wasted tokens. Each call counts once, priority
                   DISCARDED > REPEAT > OVERSIZED; discarded/repeat calls
                   contribute their full tokens, oversized only the excess.

Usage::

    uv run python -m backend.scripts.waste_report \\
        <forge-db> <logs-db> <oversize-threshold-tokens>
"""

from __future__ import annotations

import sqlite3
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class LlmCall:
    """One LLM call row from the logs DB."""

    row_id: int
    phase: int | None
    gap_type: str | None
    gap_id: str | None
    node_id: str | None
    prompt_tokens: int
    completion_tokens: int

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def _load_llm_calls(conn: sqlite3.Connection) -> list[LlmCall]:
    """All LLM calls, in insertion order. NULL token counts read as 0."""
    rows = conn.execute(
        "SELECT id, phase, gap_type, gap_id, node_id,"
        " COALESCE(prompt_tokens, 0), COALESCE(completion_tokens, 0)"
        " FROM logs WHERE category = 'LLM' ORDER BY id"
    ).fetchall()
    return [LlmCall(*row) for row in rows]


def _redispatch_call_ids(calls: list[LlmCall]) -> tuple[set[int], list[str]]:
    """Row ids of calls on any dispatch after a key's first, plus the section.

    A dispatch is a distinct gap_id; the first dispatch of a key is the one
    whose gap_id appears earliest in the log.
    """
    first_gap_id: dict[tuple[str, str], str] = {}
    dispatches: dict[tuple[str, str], set[str]] = {}
    for call in calls:
        if call.gap_type is None or call.node_id is None or call.gap_id is None:
            continue
        key = (call.gap_type, call.node_id)
        first_gap_id.setdefault(key, call.gap_id)
        dispatches.setdefault(key, set()).add(call.gap_id)
    repeat_ids = {
        c.row_id
        for c in calls
        if c.gap_type is not None and c.node_id is not None and c.gap_id is not None
        and c.gap_id != first_gap_id[(c.gap_type, c.node_id)]
    }
    distribution: dict[int, int] = {}
    for gap_ids in dispatches.values():
        distribution[len(gap_ids)] = distribution.get(len(gap_ids), 0) + 1
    repeat_calls = [c for c in calls if c.row_id in repeat_ids]
    repeat_tokens = sum(c.total_tokens for c in repeat_calls)
    repeated_keys = sorted(
        (k for k, v in dispatches.items() if len(v) > 1),
        key=lambda k: len(dispatches[k]),
        reverse=True,
    )
    lines = ["1. REPEAT WORK"]
    for count in sorted(distribution):
        lines.append(f"  keys dispatched {count}x: {distribution[count]}")
    lines.append(f"  re-dispatch calls: {len(repeat_calls)}")
    lines.append(f"  re-dispatch tokens: {repeat_tokens}")
    for gap_type, node_id in repeated_keys[:10]:
        n = len(dispatches[(gap_type, node_id)])
        lines.append(f"  top: {gap_type}:{node_id}  {n} dispatches")
    return repeat_ids, lines


def _discarded_nodes(conn: sqlite3.Connection) -> set[str]:
    """Node ids created then deleted: history-only or lifecycle 'deleted'."""
    live = {
        row[0]
        for row in conn.execute(
            "SELECT node_id FROM pg_nodes WHERE lifecycle != 'deleted'"
        )
    }
    deleted = {
        row[0]
        for row in conn.execute(
            "SELECT node_id FROM pg_nodes WHERE lifecycle = 'deleted'"
        )
    }
    historic = {
        row[0] for row in conn.execute("SELECT DISTINCT node_id FROM pg_node_history")
    }
    return (historic - live) | deleted


def _node_type(conn: sqlite3.Connection, node_id: str) -> str:
    """Node type from pg_nodes when present, else the node-id prefix."""
    row = conn.execute(
        "SELECT node_type FROM pg_nodes WHERE node_id = ?", (node_id,)
    ).fetchone()
    if row is not None:
        return str(row[0])
    return node_id.split("-")[0]


def _discarded_section(
    forge: sqlite3.Connection, calls: list[LlmCall], discarded: set[str]
) -> list[str]:
    """Section 2: discarded-node counts, attributable tokens, and churn."""
    by_type: dict[str, int] = {}
    by_changed_by: dict[str, int] = {}
    for node_id in sorted(discarded):
        type_name = _node_type(forge, node_id)
        by_type[type_name] = by_type.get(type_name, 0) + 1
        row = forge.execute(
            "SELECT changed_by FROM pg_node_history WHERE node_id = ?"
            " ORDER BY version DESC LIMIT 1",
            (node_id,),
        ).fetchone()
        changed_by = str(row[0]) if row is not None else "<no history>"
        by_changed_by[changed_by] = by_changed_by.get(changed_by, 0) + 1
    tokens = sum(c.total_tokens for c in calls if c.node_id in discarded)
    churn = forge.execute(
        "SELECT node_id, COUNT(*) FROM pg_node_history"
        " GROUP BY node_id HAVING COUNT(*) > 3 ORDER BY 2 DESC, 1 LIMIT 10"
    ).fetchall()
    churn_total = forge.execute(
        "SELECT COUNT(*) FROM (SELECT node_id FROM pg_node_history"
        " GROUP BY node_id HAVING COUNT(*) > 3)"
    ).fetchone()[0]
    lines = ["2. INVALID/DISCARDED WORK", f"  discarded nodes: {len(discarded)}"]
    lines += [f"  by type {t}: {n}" for t, n in sorted(by_type.items())]
    lines += [f"  by last changed_by {c}: {n}" for c, n in sorted(by_changed_by.items())]
    lines.append(f"  tokens on discarded nodes: {tokens}")
    lines.append(f"  churned nodes (>3 versions): {churn_total}")
    lines += [f"  churn top: {node_id}  {n} versions" for node_id, n in churn]
    return lines


def _noop_section(forge: sqlite3.Connection) -> list[str]:
    """Section 3: consecutive same-hash history entries, by actor and reason."""
    rows = forge.execute(
        "SELECT node_id, version, content_hash, changed_by, change_reason"
        " FROM pg_node_history ORDER BY node_id, version"
    ).fetchall()
    by_actor: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    total = 0
    previous: tuple[str, str] | None = None
    for node_id, _version, content_hash, changed_by, change_reason in rows:
        if previous == (node_id, content_hash):
            total += 1
            by_actor[changed_by] = by_actor.get(changed_by, 0) + 1
            prefix = (change_reason or "<none>")[:40]
            by_reason[prefix] = by_reason.get(prefix, 0) + 1
        previous = (node_id, content_hash)
    lines = ["3. NO-OP WORK", f"  no-op history entries: {total}"]
    lines += [f"  by {actor}: {n}" for actor, n in sorted(by_actor.items())]
    top_reasons = sorted(by_reason.items(), key=lambda kv: kv[1], reverse=True)
    lines += [f"  reason: {reason!r} x{n}" for reason, n in top_reasons[:10]]
    return lines


def _oversized_section(calls: list[LlmCall], threshold: int) -> tuple[set[int], list[str]]:
    """Section 4: calls whose prompt exceeds the threshold."""
    oversized = [c for c in calls if c.prompt_tokens > threshold]
    excess = sum(c.prompt_tokens - threshold for c in oversized)
    lines = [
        "4. OVERSIZED CALLS",
        f"  oversized calls (>{threshold} prompt tokens): {len(oversized)}",
        f"  total excess tokens: {excess}",
    ]
    top = sorted(oversized, key=lambda c: c.prompt_tokens, reverse=True)[:5]
    lines += [
        f"  top: {c.prompt_tokens} prompt tokens"
        f"  phase={c.phase}  gap_type={c.gap_type}  node={c.node_id}"
        for c in top
    ]
    return {c.row_id for c in oversized}, lines


def _summary_section(
    calls: list[LlmCall],
    discarded: set[str],
    repeat_ids: set[int],
    oversized_ids: set[int],
    threshold: int,
) -> list[str]:
    """Section 5: each call wasted once, priority discarded > repeat > oversized."""
    total = sum(c.total_tokens for c in calls)
    wasted = 0
    for call in calls:
        if call.node_id in discarded:
            wasted += call.total_tokens
        elif call.row_id in repeat_ids:
            wasted += call.total_tokens
        elif call.row_id in oversized_ids:
            wasted += call.prompt_tokens - threshold
    return [
        "5. SUMMARY",
        f"  LLM calls: {len(calls)}",
        f"  total LLM tokens: {total}",
        f"  wasted tokens: {wasted}",
        f"  waste: {100.0 * wasted / total:.1f}%",
    ]


def _connect_read_only(db_path: str) -> sqlite3.Connection:
    """Open a DB strictly read-only; missing files fail loudly."""
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def build_report(forge_db: str, logs_db: str, oversize_threshold: int) -> str:
    """Render the full waste report for one build as a printable string."""
    forge = _connect_read_only(forge_db)
    logs = _connect_read_only(logs_db)
    try:
        if logs.execute("SELECT COUNT(*) FROM logs").fetchone()[0] == 0:
            raise ValueError(f"log DB has no records: {logs_db}")
        if forge.execute("SELECT COUNT(*) FROM pg_nodes").fetchone()[0] == 0:
            raise ValueError(f"forge DB has no nodes: {forge_db}")
        calls = _load_llm_calls(logs)
        if not calls:
            raise ValueError(f"log DB has no LLM calls: {logs_db}")
        discarded = _discarded_nodes(forge)
        repeat_ids, repeat_lines = _redispatch_call_ids(calls)
        oversized_ids, oversized_lines = _oversized_section(calls, oversize_threshold)
        lines = [
            f"waste report: forge={forge_db} logs={logs_db}",
            *repeat_lines,
            *_discarded_section(forge, calls, discarded),
            *_noop_section(forge),
            *oversized_lines,
            *_summary_section(calls, discarded, repeat_ids, oversized_ids,
                              oversize_threshold),
        ]
        return "\n".join(lines)
    finally:
        forge.close()
        logs.close()


def main(argv: list[str]) -> int:
    """CLI entry point: forge-db, logs-db, oversize-threshold-tokens."""
    if len(argv) != 4:
        print(
            "usage: python -m backend.scripts.waste_report"
            " <forge-db> <logs-db> <oversize-threshold-tokens (e.g. 30000)>"
        )
        return 2
    print(build_report(argv[1], argv[2], int(argv[3])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
