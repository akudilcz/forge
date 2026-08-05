"""Provenance-hash stamping in the graph engine.

Authoring writes stamp ``properties.derived_from_hash`` — the SHA-256 of
the parent content the child was authored against. The engine stamps it
automatically at every write path so agents never supply it:

* add_node       → stamped from the live parent's current content
* update_node    → re-stamped when the child's own content changes;
                   carried over on metadata-only updates
* reparent_node  → re-stamped against the new parent
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.graph.engine import ProjectGraph
from backend.graph.models import GraphNode
from backend.graph.provenance import DERIVED_FROM_HASH, provenance_hash


@pytest.fixture
async def graph(tmp_path: Path) -> ProjectGraph:
    g = ProjectGraph(tmp_path / "graph.db")
    await g.initialise()
    return g


def _n(nid: str, ntype: str, **kw: object) -> GraphNode:
    return GraphNode(node_id=nid, node_type=ntype, **kw)  # type: ignore[arg-type]


async def _seed_parent_child(graph: ProjectGraph) -> None:
    await graph.add_node(_n("PARA-0001", "PARA", title="Input Handling",
                            content="Paragraph body."))
    await graph.add_node(_n("HLR-0001", "HLR", parent_id="PARA-0001",
                            title="Accept CSV Input",
                            content="The system shall accept CSV input."))


@pytest.mark.asyncio
async def test_add_node_stamps_parent_content_hash(graph: ProjectGraph) -> None:
    await _seed_parent_child(graph)
    child = await graph.node("HLR-0001")
    assert child is not None
    assert child.properties[DERIVED_FROM_HASH] == provenance_hash("Paragraph body.")


@pytest.mark.asyncio
async def test_add_node_without_parent_not_stamped(graph: ProjectGraph) -> None:
    await graph.add_node(_n("PROJECT-0001", "PROJECT", title="Root"))
    root = await graph.node("PROJECT-0001")
    assert root is not None
    assert DERIVED_FROM_HASH not in root.properties


@pytest.mark.asyncio
async def test_content_update_restamps(graph: ProjectGraph) -> None:
    await _seed_parent_child(graph)
    # Parent content changes → child stamp is now outdated.
    await graph.update_node("PARA-0001", "New paragraph body.", None, "t", "edit")
    # Re-authoring the child re-stamps against the CURRENT parent content.
    await graph.update_node(
        "HLR-0001", "The system shall accept UTF-8 CSV input.", None, "t", "edit"
    )
    child = await graph.node("HLR-0001")
    assert child is not None
    assert child.properties[DERIVED_FROM_HASH] == provenance_hash("New paragraph body.")


@pytest.mark.asyncio
async def test_metadata_only_update_preserves_stamp(graph: ProjectGraph) -> None:
    await _seed_parent_child(graph)
    # Replacement properties bag omitting the stamp must not lose it.
    await graph.update_node("HLR-0001", None, {"note": "x"}, "t", "meta only")
    child = await graph.node("HLR-0001")
    assert child is not None
    assert child.properties[DERIVED_FROM_HASH] == provenance_hash("Paragraph body.")
    assert child.properties["note"] == "x"


@pytest.mark.asyncio
async def test_reparent_restamps_against_new_parent(graph: ProjectGraph) -> None:
    await _seed_parent_child(graph)
    await graph.add_node(_n("PARA-0002", "PARA", title="Other Section",
                            content="Other paragraph."))
    await graph.reparent_node("HLR-0001", "PARA-0002", "t", "move")
    child = await graph.node("HLR-0001")
    assert child is not None
    assert child.properties[DERIVED_FROM_HASH] == provenance_hash("Other paragraph.")


@pytest.mark.asyncio
async def test_migration_backfills_unstamped_nodes(tmp_path: Path) -> None:
    """Legacy rows without the property are stamped loudly at schema time,
    treating the parent's CURRENT content as the provenance."""
    import json

    import aiosqlite

    db_path = tmp_path / "graph.db"
    g = ProjectGraph(db_path)
    await g.initialise()
    await _seed_parent_child(g)

    # Simulate a legacy row: strip the stamp directly in SQLite.
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "UPDATE pg_nodes SET properties='{}' WHERE node_id='HLR-0001'"
        )
        await db.commit()

    # Re-initialise → migration backfills from current parent content.
    g2 = ProjectGraph(db_path)
    await g2.initialise()
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT properties FROM pg_nodes WHERE node_id='HLR-0001'"
        ) as cur:
            row = await cur.fetchone()
    assert row is not None
    props = json.loads(row["properties"])
    assert props[DERIVED_FROM_HASH] == provenance_hash("Paragraph body.")
