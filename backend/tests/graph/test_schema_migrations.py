"""Tests for schema migrations that bring legacy DBs up to current layout."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from backend.graph.schema import (
    _migrate_content_updated_at,
    _migrate_display_label,
    _migrate_trace_to_column,
)


@pytest.mark.asyncio
async def test_migrate_display_label_renames_when_title_missing(tmp_path: Path) -> None:
    async with aiosqlite.connect(tmp_path / "db.sqlite") as db:
        await db.execute(
            "CREATE TABLE pg_nodes (node_id TEXT PRIMARY KEY, display_label TEXT)"
        )
        await db.commit()
        await _migrate_display_label(db)

        async with db.execute("PRAGMA table_info(pg_nodes)") as cur:
            cols = {row[1] async for row in cur}
        assert "title" in cols
        assert "display_label" not in cols


@pytest.mark.asyncio
async def test_migrate_display_label_copies_when_both_present(tmp_path: Path) -> None:
    async with aiosqlite.connect(tmp_path / "db.sqlite") as db:
        await db.execute(
            "CREATE TABLE pg_nodes ("
            "node_id TEXT PRIMARY KEY, display_label TEXT, title TEXT DEFAULT '')"
        )
        await db.execute(
            "INSERT INTO pg_nodes (node_id, display_label, title) VALUES (?, ?, ?)",
            ("n1", "Legacy Label", ""),
        )
        await db.commit()
        await _migrate_display_label(db)

        async with db.execute("SELECT title FROM pg_nodes WHERE node_id='n1'") as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == "Legacy Label"


@pytest.mark.asyncio
async def test_migrate_display_label_noop_without_legacy_column(tmp_path: Path) -> None:
    async with aiosqlite.connect(tmp_path / "db.sqlite") as db:
        await db.execute(
            "CREATE TABLE pg_nodes (node_id TEXT PRIMARY KEY, title TEXT)"
        )
        await db.commit()
        # Should not raise
        await _migrate_display_label(db)


@pytest.mark.asyncio
async def test_migrate_trace_to_column_promotes_from_properties(tmp_path: Path) -> None:
    async with aiosqlite.connect(tmp_path / "db.sqlite") as db:
        await db.execute(
            "CREATE TABLE pg_nodes "
            "(node_id TEXT PRIMARY KEY, properties TEXT NOT NULL DEFAULT '{}')"
        )
        await db.execute(
            "INSERT INTO pg_nodes (node_id, properties) VALUES (?, ?)",
            ("n1", '{"trace_to": ["REF-1"], "other": "x"}'),
        )
        await db.commit()
        await _migrate_trace_to_column(db)

        async with db.execute(
            "SELECT trace_to, properties FROM pg_nodes WHERE node_id='n1'"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        trace_to, props = row
        assert "REF-1" in trace_to
        assert "trace_to" not in props
        assert '"other"' in props
        assert '"x"' in props


@pytest.mark.asyncio
async def test_migrate_trace_to_column_noop_when_already_migrated(tmp_path: Path) -> None:
    async with aiosqlite.connect(tmp_path / "db.sqlite") as db:
        await db.execute(
            "CREATE TABLE pg_nodes "
            "(node_id TEXT PRIMARY KEY, trace_to TEXT, properties TEXT)"
        )
        await db.commit()
        # Idempotent — no error, no change
        await _migrate_trace_to_column(db)


@pytest.mark.asyncio
async def test_migrate_content_updated_at_backfills_from_updated_at(tmp_path: Path) -> None:
    async with aiosqlite.connect(tmp_path / "db.sqlite") as db:
        await db.execute(
            "CREATE TABLE pg_nodes "
            "(node_id TEXT PRIMARY KEY, updated_at TEXT NOT NULL)"
        )
        await db.execute(
            "INSERT INTO pg_nodes (node_id, updated_at) VALUES (?, ?)",
            ("n1", "2026-01-02T03:04:05+00:00"),
        )
        await db.commit()
        await _migrate_content_updated_at(db)

        async with db.execute(
            "SELECT content_updated_at FROM pg_nodes WHERE node_id='n1'"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == "2026-01-02T03:04:05+00:00"


@pytest.mark.asyncio
async def test_migrate_content_updated_at_noop_when_already_migrated(tmp_path: Path) -> None:
    async with aiosqlite.connect(tmp_path / "db.sqlite") as db:
        await db.execute(
            "CREATE TABLE pg_nodes "
            "(node_id TEXT PRIMARY KEY, updated_at TEXT, content_updated_at TEXT)"
        )
        await db.execute(
            "INSERT INTO pg_nodes (node_id, updated_at, content_updated_at) "
            "VALUES (?, ?, ?)",
            ("n1", "2026-02-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        await db.commit()
        # Idempotent — existing values are preserved.
        await _migrate_content_updated_at(db)

        async with db.execute(
            "SELECT content_updated_at FROM pg_nodes WHERE node_id='n1'"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert row[0] == "2026-01-01T00:00:00+00:00"
