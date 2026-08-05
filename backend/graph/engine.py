"""ProjectGraph — central graph service backed by SQLite + NetworkX.

Architecture (DESIGN_GRAPH.md):
  * SQLite (aiosqlite) — persistence, full version history, audit trail.
  * NetworkX DiGraph (in-memory) — graph algorithms: ancestors, descendants,
    cycle detection, topological sort, impact propagation.

The two representations are kept in strict sync: every mutation writes
through to SQLite first, then updates NetworkX.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite
import networkx as nx

from backend.graph._algorithms import AlgorithmMixin
from backend.graph._queries import QueryMixin
from backend.graph.models import (
    NODE_TYPE_LAYER,
    GraphEdge,
    GraphNode,
    NodeType,
)
from backend.server.forge_logger import forge_logger


class ProjectGraph(QueryMixin, AlgorithmMixin):
    """Central graph service for the FORGE project, backed by SQLite and NetworkX.

    Mutations are written to SQLite first then mirrored to an in-memory
    NetworkX DiGraph used for all graph algorithms (ancestors, descendants,
    impact propagation, cycle detection).
    """

    def __init__(self, db_path: str | Path) -> None:
        """Initialise the engine with a database path."""
        self._db_path = str(db_path)
        self._g: nx.DiGraph = nx.DiGraph()
        self._on_change: Any = None  # optional callback(action, node_dict)

    def set_on_change(self, callback: Any) -> None:
        """Register a callback invoked after add/update/delete operations."""
        self._on_change = callback

    # ── Initialisation ──────────────────────────────────────────────────────

    async def initialise(self) -> None:
        """Apply schema and hydrate NetworkX from the database."""
        await self._apply_schema()
        await self._load_into_memory()

    async def _apply_schema(self) -> None:
        """Apply the DDL schema to the database."""
        from backend.graph.schema import apply_schema

        async with aiosqlite.connect(self._db_path) as db:
            await apply_schema(db)

    async def _load_into_memory(self) -> None:
        """Load all nodes and edges from SQLite into the NetworkX graph."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM pg_nodes") as cur:
                async for row in cur:
                    self._nx_add_node(dict(row))
            async with db.execute("SELECT * FROM pg_edges") as cur:
                async for row in cur:
                    self._nx_add_edge(dict(row))

    def _nx_add_node(self, row: dict[str, Any]) -> None:
        """Add or update a node in the NetworkX graph from a database row."""
        import logging as _logging  # noqa: PLC0415
        _logging.getLogger("forge.graph").debug(
            "nx_add_node: %s type=%s parent=%s content_len=%d",
            row.get("node_id"), row.get("node_type"),
            row.get("parent_id"), len(row.get("content") or ""),
        )
        raw_trace = row.get("trace_to", "[]")
        trace_to = json.loads(raw_trace) if isinstance(raw_trace, str) else (raw_trace or [])
        self._g.add_node(
            row["node_id"],
            node_type=row["node_type"],
            layer=row.get("layer", 0),
            title=row["title"],
            lifecycle=row["lifecycle"],
            content=row.get("content", ""),
            content_hash=row.get("content_hash", ""),
            version=row.get("version", 1),
            parent_id=row.get("parent_id"),
            trace_to=trace_to,
            created_by=row.get("created_by", "system"),
            created_at=row.get("created_at", ""),
            updated_at=row.get("updated_at", ""),
            content_updated_at=row.get("content_updated_at", ""),
            properties=json.loads(row.get("properties") or "{}") if isinstance(row.get("properties"), str) else (row.get("properties") or {}),
        )

    def _nx_add_edge(self, row: dict[str, Any]) -> None:
        """Add an edge to the NetworkX graph from a database row."""
        self._g.add_edge(
            row["source_id"],
            row["target_id"],
            edge_id=row["edge_id"],
            edge_type=row["edge_type"],
            confidence=row.get("confidence", 1.0),
        )

    # ── Core mutations ───────────────────────────────────────────────────────

    async def add_node(self, node: GraphNode) -> GraphNode:
        """Create a new node. Idempotent — existing nodes are replaced."""
        if not node.content_hash and node.content:
            node.content_hash = hashlib.sha256(node.content.encode()).hexdigest()

        if node.layer == 0 and node.node_type:
            try:
                nt = NodeType(node.node_type)
                node.layer = NODE_TYPE_LAYER.get(nt, 0)
            except ValueError:
                pass

        persist_props = dict(node.properties)
        if node.para_type:
            persist_props["sub_type"] = node.para_type
        persist_props.pop("trace_to", None)

        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO pg_nodes
                   (node_id, node_type, layer, title, content, content_hash,
                    version, parent_id, lifecycle, created_by,
                    created_at, updated_at, content_updated_at,
                    properties, trace_to)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    node.node_id, node.node_type, node.layer, node.title,
                    node.content, node.content_hash, node.version, node.parent_id,
                    node.lifecycle.value, node.created_by, now, now, now,
                    json.dumps(persist_props), json.dumps(node.trace_to),
                ),
            )
            await db.commit()
        node_dict = {
            "node_id": node.node_id, "node_type": node.node_type,
            "layer": node.layer, "title": node.title,
            "lifecycle": node.lifecycle.value, "content": node.content,
            "content_hash": node.content_hash, "version": node.version,
            "parent_id": node.parent_id, "trace_to": node.trace_to,
            "created_by": node.created_by, "created_at": now,
            "updated_at": now, "content_updated_at": now,
            "properties": persist_props,
        }
        self._nx_add_node(node_dict)
        if self._on_change:
            self._on_change("added", node_dict)
        forge_logger.graph_write(
            "add_node", node.node_id, node.node_type or "",
            changed_by=node.created_by,
            lifecycle=node.lifecycle.value,
            parent_id=node.parent_id or "",
            trace_to=node.trace_to,
            content_hash=node.content_hash[:12] if node.content_hash else "",
        )
        return node

    async def update_node(
        self, node_id: str, content: str | None,
        properties: dict[str, Any] | None, changed_by: str,
        change_reason: str, title: str | None = None,
        trace_to: list[str] | None = None,
    ) -> tuple[GraphNode, Any]:
        """Update a node's content, title, trace_to, and/or properties."""
        node = await self.node(node_id)
        if node is None:
            raise KeyError(f"Node not found: {node_id}")

        await self._save_history(node, changed_by, change_reason)

        new_content = content if content is not None else node.content
        merged_props = node.properties if properties is None else properties
        if trace_to is None and merged_props and "trace_to" in merged_props:
            trace_to = merged_props.pop("trace_to")
        elif merged_props:
            merged_props.pop("trace_to", None)
        new_trace = trace_to if trace_to is not None else node.trace_to
        new_label = title if title is not None else node.title
        new_hash = hashlib.sha256(new_content.encode()).hexdigest()
        now = datetime.now(UTC).isoformat()

        # Staleness is content-aware: content_updated_at moves only when the
        # content or title actually changed. Metadata-only updates (properties,
        # trace_to) must not cascade STALE_NODE gaps onto children.
        content_changed = new_content != node.content or new_label != node.title
        assert node.content_updated_at is not None  # guaranteed by model_post_init
        new_content_ts = now if content_changed else node.content_updated_at.isoformat()

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """UPDATE pg_nodes
                   SET title=?, content=?, content_hash=?, properties=?,
                       trace_to=?, version=version+1, updated_at=?,
                       content_updated_at=?
                   WHERE node_id=?""",
                (new_label, new_content, new_hash, json.dumps(merged_props or {}),
                 json.dumps(new_trace), now, new_content_ts, node_id),
            )
            await db.commit()

        if self._g.has_node(node_id):
            d = self._g.nodes[node_id]
            d["title"] = new_label
            d["content"] = new_content
            d["content_hash"] = new_hash
            d["trace_to"] = new_trace
            d["updated_at"] = now
            d["content_updated_at"] = new_content_ts
            d["properties"] = merged_props or {}
            d["version"] = d.get("version", 1) + 1

        updated = await self.node(node_id)
        assert updated is not None
        if self._on_change:
            self._on_change("updated", self._g.nodes.get(node_id, {}))
        impact = await self.impact_set(node_id)

        diff_summary: list[str] = []
        if content is not None and content != node.content:
            diff_summary.append(f"content({len(node.content or '')}->{len(content)})")
        if title is not None and title != node.title:
            diff_summary.append("title")
        if trace_to is not None and trace_to != node.trace_to:
            diff_summary.append(f"trace_to({len(node.trace_to)}->{len(trace_to)})")
        if properties is not None:
            diff_summary.append("properties")
        forge_logger.graph_write(
            "update_node", node_id, updated.node_type or "",
            changed_by=changed_by,
            change_reason=change_reason,
            diff=",".join(diff_summary) or "none",
        )
        return updated, impact

    async def add_edge(self, edge: GraphEdge) -> GraphEdge:
        """Create a traceability edge between two nodes."""
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """INSERT OR REPLACE INTO pg_edges
                   (edge_id, edge_type, source_id, target_id, rationale,
                    confidence, created_by, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (edge.edge_id, edge.edge_type, edge.source_id,
                 edge.target_id, edge.rationale, edge.confidence,
                 edge.created_by, now),
            )
            await db.commit()
        self._nx_add_edge({
            "source_id": edge.source_id, "target_id": edge.target_id,
            "edge_id": edge.edge_id, "edge_type": edge.edge_type,
            "confidence": edge.confidence,
        })
        return edge

    async def remove_edge(self, edge_id: str, justification: str = "") -> None:
        """Remove a traceability edge by its ID."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT source_id, target_id FROM pg_edges WHERE edge_id=?", (edge_id,)
            ) as cur:
                row = await cur.fetchone()
            if row:
                src, tgt = row["source_id"], row["target_id"]
                await db.execute("DELETE FROM pg_edges WHERE edge_id=?", (edge_id,))
                await db.commit()
                if self._g.has_edge(src, tgt):
                    if self._g[src][tgt].get("edge_id") == edge_id:
                        self._g.remove_edge(src, tgt)

    async def delete_node(self, node_id: str) -> None:
        """Delete a node and all its edges.

        Safe by default: if the node has children, reparent them to its
        parent BEFORE deleting. This prevents the orphan cascade where a
        semantic-dedup delete silently strands a subtree (seen in
        bubble_sort integration: a container heading was deleted and
        four children became ORPHAN_NODE gaps, triggering a recovery
        loop that blew the phase timeout).

        Callers that genuinely want a subtree cascade-delete should use
        ``delete_children_recursive`` explicitly.
        """
        node_data = self._g.nodes.get(node_id, {}) if self._g.has_node(node_id) else {}

        # Reparent children to the about-to-be-deleted node's parent.
        reparent_target = node_data.get("parent_id")
        # Containment is `parent_id`; `children_sync` is the authoritative query.
        # This used to walk `self._g.successors(node_id)`, but the NetworkX graph
        # only gains edges via `add_edge`, which no production path ever calls —
        # so `successors` always returned [] and the reparent never ran. Deleting
        # a node silently stranded its entire subtree with a `parent_id` pointing
        # at a row that no longer exists, and the audit log recorded
        # `reparented_children=None` every time, making it invisible.
        child_ids = [c.node_id for c in self.children_sync(node_id)]
        for child_id in child_ids:
            await self.reparent_node(
                child_id,
                reparent_target,
                changed_by="delete_node:auto-reparent",
                reason=(
                    f"Parent {node_id} being deleted; reparented to "
                    f"{reparent_target or '<root>'} to avoid orphan."
                ),
            )

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM pg_edges WHERE source_id=? OR target_id=?", (node_id, node_id))
            await db.execute("DELETE FROM pg_nodes WHERE node_id=?", (node_id,))
            await db.commit()
        if self._g.has_node(node_id):
            self._g.remove_node(node_id)
        if self._on_change and node_data:
            self._on_change("deleted", {"node_id": node_id, **node_data})
        forge_logger.graph_write(
            "delete_node", node_id, node_data.get("node_type", ""),
            reparented_children=len(child_ids) or None,
        )

    async def reparent_node(
        self, node_id: str, new_parent_id: str | None,
        changed_by: str, reason: str,
    ) -> GraphNode:
        """Move node_id to a new parent, updating both DB and in-memory graph."""
        node = await self.node(node_id)
        if node is None:
            raise KeyError(f"Node not found: {node_id}")
        if new_parent_id and await self.node(new_parent_id) is None:
            raise KeyError(f"New parent not found: {new_parent_id}")

        await self._save_history(node, changed_by, reason)

        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE pg_nodes SET parent_id=?, updated_at=?, version=version+1 WHERE node_id=?",
                (new_parent_id, now, node_id),
            )
            await db.commit()

        if self._g.has_node(node_id):
            d = self._g.nodes[node_id]
            d["parent_id"] = new_parent_id
            d["updated_at"] = now
            d["version"] = d.get("version", 1) + 1

        updated = await self.node(node_id)
        assert updated is not None
        forge_logger.graph_write(
            "reparent_node", node_id, updated.node_type or "",
            changed_by=changed_by, change_reason=reason,
            new_parent_id=new_parent_id or "",
        )
        return updated

    # ── Admin operations ─────────────────────────────────────────────────────

    async def reset(self) -> None:
        """Delete all nodes, edges, and history from the database and memory."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM pg_nodes")
            await db.execute("DELETE FROM pg_edges")
            await db.execute("DELETE FROM pg_node_history")
            await db.commit()
        self._g.clear()

    async def allocate_node_id(self, node_type: str) -> str:
        """Atomically allocate the next sequential ID for a node type."""
        nt = node_type.upper()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """INSERT INTO pg_node_sequences (node_type, next_seq) VALUES (?, 1)
                   ON CONFLICT(node_type) DO UPDATE
                   SET next_seq = pg_node_sequences.next_seq + 1""",
                (nt,),
            )
            async with db.execute(
                "SELECT next_seq FROM pg_node_sequences WHERE node_type = ?", (nt,)
            ) as cur:
                row = await cur.fetchone()
            await db.commit()
        seq = row[0] if row else 1
        return f"{nt}-{seq:04d}"

    async def reset_sequences(self, exclude: list[str] | None = None) -> None:
        """Reset node sequence counters, optionally excluding certain types."""
        async with aiosqlite.connect(self._db_path) as db:
            if exclude:
                placeholders = ",".join("?" for _ in exclude)
                await db.execute(
                    f"DELETE FROM pg_node_sequences WHERE node_type NOT IN ({placeholders})",
                    [t.upper() for t in exclude],
                )
            else:
                await db.execute("DELETE FROM pg_node_sequences")
            await db.commit()

    async def delete_children_recursive(self, parent_id: str) -> None:
        """Recursively delete all children of parent_id (leaves first)."""
        for child in await self.children(parent_id):
            await self.delete_children_recursive(child.node_id)
            await self.delete_node(child.node_id)

    async def create_baseline(
        self, baseline_id: str, baseline_type: str = "phase", description: str = ""
    ) -> GraphNode:
        """Create a RECORD node representing a baseline snapshot."""
        node = GraphNode(
            node_id=f"rec.baseline.{baseline_id}",
            node_type=NodeType.RECORD.value,
            title=f"Baseline: {baseline_id}",
            content=description,
            created_by="engineer",
            properties={"record_type": "baseline", "baseline_type": baseline_type},
        )
        return await self.add_node(node)


    async def _save_history(self, node: GraphNode, changed_by: str, change_reason: str) -> None:
        """Append the current node state to the append-only history table."""
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """INSERT INTO pg_node_history
                   (node_id, version, content_hash, content, properties,
                    lifecycle, changed_by, changed_at, change_reason)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (node.node_id, node.version, node.content_hash,
                 node.content, json.dumps(node.properties),
                 node.lifecycle.value, changed_by, now, change_reason),
            )
            await db.commit()
