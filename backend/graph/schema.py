"""SQLite DDL for the FORGE Project Graph Engine.

Schema aligns with DESIGN_GRAPH.md §Storage Architecture:
  * pg_nodes — universal node fields including layer, parent_id, lifecycle.
  * pg_edges — supplementary traceability edges (7 edge types with provenance).
  * pg_node_history — append-only audit trail of every node mutation.
  * Supporting tables: session, cost_entries.
"""

from __future__ import annotations

import json

import aiosqlite

from backend.graph.provenance import DERIVED_FROM_HASH, provenance_hash

SCHEMA_SQL = """
-- FORGE Project Graph SQLite Schema v3

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS pg_nodes (
    node_id         TEXT    PRIMARY KEY,
    node_type       TEXT    NOT NULL,
    layer           INTEGER NOT NULL DEFAULT 0,
    title           TEXT    NOT NULL DEFAULT '',
    content         TEXT    NOT NULL DEFAULT '',
    content_hash    TEXT    NOT NULL DEFAULT '',
    version         INTEGER NOT NULL DEFAULT 1,
    parent_id       TEXT    REFERENCES pg_nodes(node_id) ON DELETE RESTRICT,
    lifecycle       TEXT    NOT NULL DEFAULT 'draft',
    created_by      TEXT    NOT NULL DEFAULT 'system',
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    content_updated_at TEXT NOT NULL DEFAULT '',
    properties      TEXT    NOT NULL DEFAULT '{}',
    trace_to        TEXT    NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS pg_edges (
    edge_id     TEXT  PRIMARY KEY,
    edge_type   TEXT  NOT NULL,
    source_id   TEXT  NOT NULL REFERENCES pg_nodes(node_id) ON DELETE CASCADE,
    target_id   TEXT  NOT NULL REFERENCES pg_nodes(node_id) ON DELETE CASCADE,
    rationale   TEXT,
    confidence  REAL  NOT NULL DEFAULT 1.0,
    created_by  TEXT  NOT NULL DEFAULT 'system',
    created_at  TEXT  NOT NULL
);

CREATE TABLE IF NOT EXISTS pg_node_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id         TEXT    NOT NULL,
    version         INTEGER NOT NULL,
    content_hash    TEXT    NOT NULL,
    content         TEXT    NOT NULL,
    properties      TEXT    NOT NULL,
    lifecycle       TEXT    NOT NULL,
    changed_by      TEXT    NOT NULL,
    changed_at      TEXT    NOT NULL,
    change_reason   TEXT
);

CREATE TABLE IF NOT EXISTS session (
    session_id   TEXT PRIMARY KEY,
    project_name TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'INITIALISED',
    loop_state   TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cost_entries (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id        TEXT    NOT NULL,
    agent_id          TEXT    NOT NULL,
    model             TEXT    NOT NULL,
    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd          REAL    NOT NULL DEFAULT 0.0,
    duration_ms       INTEGER NOT NULL DEFAULT 0,
    timestamp         TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS pg_node_sequences (
    node_type TEXT PRIMARY KEY,
    next_seq  INTEGER NOT NULL DEFAULT 1
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_pg_nodes_type     ON pg_nodes(node_type);
CREATE INDEX IF NOT EXISTS idx_pg_nodes_layer    ON pg_nodes(layer);
CREATE INDEX IF NOT EXISTS idx_pg_nodes_parent   ON pg_nodes(parent_id);
CREATE INDEX IF NOT EXISTS idx_pg_nodes_state    ON pg_nodes(lifecycle);
CREATE INDEX IF NOT EXISTS idx_pg_edges_source   ON pg_edges(source_id);
CREATE INDEX IF NOT EXISTS idx_pg_edges_target   ON pg_edges(target_id);
CREATE INDEX IF NOT EXISTS idx_pg_edges_type     ON pg_edges(edge_type);
"""


async def apply_schema(db: aiosqlite.Connection) -> None:
    """Apply the schema to the given aiosqlite connection.

    This function is idempotent: it uses CREATE TABLE IF NOT EXISTS and
    CREATE INDEX IF NOT EXISTS throughout, so re-running on an existing
    database is safe.

    Migrations (applied in order):
      1. Rename display_label → title if needed.
      2. Add trace_to column and promote from properties JSON.
      3. Add content_updated_at column, backfilled from updated_at.
      4. Backfill properties.derived_from_hash provenance stamps (LOUD).

    Args:
        db: An open aiosqlite.Connection instance.
    """
    await db.executescript(SCHEMA_SQL)
    await db.commit()
    await _migrate_display_label(db)
    await _migrate_trace_to_column(db)
    await _migrate_content_updated_at(db)
    await _migrate_derived_from_hash(db)


async def _migrate_display_label(db: aiosqlite.Connection) -> None:
    """Rename display_label → title on existing databases if needed."""
    async with db.execute("PRAGMA table_info(pg_nodes)") as cur:
        cols = {row[1] async for row in cur}
    if "display_label" in cols and "title" not in cols:
        await db.execute("ALTER TABLE pg_nodes RENAME COLUMN display_label TO title")
        await db.commit()
    elif "display_label" in cols and "title" in cols:
        await db.execute("UPDATE pg_nodes SET title = display_label WHERE title = ''")
        await db.commit()


async def _migrate_trace_to_column(db: aiosqlite.Connection) -> None:
    """Add trace_to column and promote trace_to from properties JSON.

    Existing databases store trace_to inside the properties JSON blob.
    This migration adds a first-class trace_to column and copies data
    from properties.trace_to into it, then removes the key from properties.
    """
    async with db.execute("PRAGMA table_info(pg_nodes)") as cur:
        cols = {row[1] async for row in cur}
    if "trace_to" in cols:
        return  # already migrated

    await db.execute(
        "ALTER TABLE pg_nodes ADD COLUMN trace_to TEXT NOT NULL DEFAULT '[]'"
    )
    # Copy trace_to from properties JSON into the new column
    await db.execute(
        """UPDATE pg_nodes
           SET trace_to = json_extract(properties, '$.trace_to')
           WHERE json_extract(properties, '$.trace_to') IS NOT NULL"""
    )
    # Remove trace_to from properties JSON
    await db.execute(
        """UPDATE pg_nodes
           SET properties = json_remove(properties, '$.trace_to')
           WHERE json_extract(properties, '$.trace_to') IS NOT NULL"""
    )
    await db.commit()


async def _migrate_content_updated_at(db: aiosqlite.Connection) -> None:
    """Add content_updated_at column, backfilled from updated_at.

    ``content_updated_at`` records the last content/title change and is the
    reference point for STALE_NODE detection. For existing rows the exact
    value is unknowable, so it is backfilled from ``updated_at`` — the
    conservative upper bound that never misses a genuine content change.
    Rows created by a fresh schema before this migration ran carry the DDL
    default '' and are backfilled the same way.
    """
    async with db.execute("PRAGMA table_info(pg_nodes)") as cur:
        cols = {row[1] async for row in cur}
    if "content_updated_at" not in cols:
        await db.execute(
            "ALTER TABLE pg_nodes ADD COLUMN content_updated_at TEXT NOT NULL DEFAULT ''"
        )
    await db.execute(
        "UPDATE pg_nodes SET content_updated_at = updated_at WHERE content_updated_at = ''"
    )
    await db.commit()


async def _migrate_derived_from_hash(db: aiosqlite.Connection) -> None:
    """Backfill ``properties.derived_from_hash`` provenance stamps — LOUDLY.

    STALE_NODE detection compares a child's stored provenance stamp against
    the hash of its parent's current content (specs/12 §2.6). Nodes created
    before provenance stamping existed carry no stamp, and their historical
    parent content is unknowable — so the current parent content is taken as
    the provenance baseline (stamp-on-first-load). That assumption means any
    parent edit that happened BEFORE this migration will not surface as
    STALE_NODE, which is why every backfill run is logged at WARNING with
    the affected node count: silence here would hide the assumption.

    Idempotent: only rows missing the property are touched. Rows without a
    parent are never stamped.
    """
    query = (
        "SELECT c.node_id, c.properties, p.content "
        "FROM pg_nodes c JOIN pg_nodes p ON c.parent_id = p.node_id "
        f"WHERE json_extract(c.properties, '$.{DERIVED_FROM_HASH}') IS NULL"
    )
    async with db.execute(query) as cur:
        rows = list(await cur.fetchall())
    if not rows:
        return
    for node_id, props_raw, parent_content in rows:
        props = json.loads(props_raw or "{}")
        props[DERIVED_FROM_HASH] = provenance_hash(parent_content or "")
        await db.execute(
            "UPDATE pg_nodes SET properties=? WHERE node_id=?",
            (json.dumps(props), node_id),
        )
    await db.commit()

    from backend.server.forge_logger import forge_logger  # noqa: PLC0415

    forge_logger.emit(
        "WARNING", "GRAPH",
        f"provenance backfill: stamped {len(rows)} node(s) with "
        f"{DERIVED_FROM_HASH} from their parent's CURRENT content "
        f"(historical provenance unknowable — parent edits predating this "
        f"migration will not surface as STALE_NODE).",
        count=len(rows),
    )
