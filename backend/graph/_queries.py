"""QueryMixin — read-only operations for ProjectGraph."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import aiosqlite
import networkx as nx

from backend.graph._conversions import nx_data_to_node, row_to_edge, row_to_node
from backend.graph.models import GraphEdge, GraphNode

if TYPE_CHECKING:
    from backend.graph.engine import ProjectGraph


class QueryMixin:
    """Read-only query methods mixed into ProjectGraph."""

    # State owned by ProjectGraph; declared here so the mixin type-checks.
    _db_path: str
    _g: nx.DiGraph

    # -- Async DB queries --------------------------------------------------

    async def node(self, node_id: str) -> GraphNode | None:
        """Fetch a single node by its ID."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM pg_nodes WHERE node_id=?", (node_id,)) as cur:
                row = await cur.fetchone()
        return row_to_node(dict(row)) if row else None

    async def children(self, parent_id: str) -> list[GraphNode]:
        """Fetch all structural children of a node."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM pg_nodes WHERE parent_id=? ORDER BY created_at",
                (parent_id,),
            ) as cur:
                rows = await cur.fetchall()
        return [row_to_node(dict(r)) for r in rows]

    async def nodes_by_type(self, node_type: str) -> list[GraphNode]:
        """Fetch all nodes of a specific type."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM pg_nodes WHERE node_type=?", (node_type,)) as cur:
                rows = await cur.fetchall()
        return [row_to_node(dict(r)) for r in rows]

    async def nodes(
        self,
        type_prefix: str | None = None,
        lifecycle: str | None = None,
        review_required: bool | None = None,
    ) -> list[GraphNode]:
        """Return nodes with optional type prefix / lifecycle filters."""
        query = "SELECT * FROM pg_nodes WHERE 1=1"
        params: list[Any] = []
        if type_prefix:
            query += " AND node_type LIKE ?"
            params.append(f"{type_prefix}%")
        if lifecycle:
            query += " AND lifecycle = ?"
            params.append(lifecycle)
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cur:
                rows = await cur.fetchall()
        results = [row_to_node(dict(r)) for r in rows]
        if review_required is not None:
            results = [
                n for n in results
                if bool(n.properties.get("review_required")) == review_required
            ]
        return results

    async def all_edges(
        self,
        edge_type: str | None = None,
        source_id: str | None = None,
        target_id: str | None = None,
    ) -> list[GraphEdge]:
        """Return all edges with optional filters."""
        query = "SELECT * FROM pg_edges WHERE 1=1"
        params: list[Any] = []
        if edge_type:
            query += " AND edge_type = ?"
            params.append(edge_type)
        if source_id:
            query += " AND source_id = ?"
            params.append(source_id)
        if target_id:
            query += " AND target_id = ?"
            params.append(target_id)
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cur:
                rows = await cur.fetchall()
        return [row_to_edge(dict(r)) for r in rows]

    async def edges_from(self, node_id: str) -> list[GraphEdge]:
        """Return all edges originating from a node."""
        return await self.all_edges(source_id=node_id)

    async def edges_to(self, node_id: str) -> list[GraphEdge]:
        """Return all edges pointing to a node."""
        return await self.all_edges(target_id=node_id)

    async def find_node_by_slug(self, slug: str) -> GraphNode | None:
        """Find a DOCUMENT node whose properties['slug'] matches slug."""
        from backend.graph.models import NodeType
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM pg_nodes WHERE node_type=?", (NodeType.DOCUMENT.value,)
            ) as cur:
                rows = await cur.fetchall()
        for row in rows:
            node = row_to_node(dict(row))
            if node.properties.get("slug") == slug:
                return node
        return None


    def all_nodes(self) -> list[GraphNode]:
        """Return all nodes in the graph (from memory)."""
        return [
            nx_data_to_node(nid, self._g.nodes[nid])
            for nid in self._g.nodes
        ]

    def node_sync(self, node_id: str) -> GraphNode | None:
        """Return a node from the in-memory NetworkX graph (synchronous)."""
        if not self._g.has_node(node_id):
            return None
        return nx_data_to_node(node_id, self._g.nodes[node_id])

    def children_sync(self, parent_id: str) -> list[GraphNode]:
        """Return all structural children from the in-memory graph (synchronous)."""
        children = [
            self.node_sync(nid)
            for nid in self._g.nodes
            if self._g.nodes[nid].get("parent_id") == parent_id
        ]
        return [child for child in children if child is not None]

    def siblings_sync(self, node_id: str) -> list[GraphNode]:
        """Return all children of this node's parent, excluding the node itself."""
        node = self.node_sync(node_id)
        if node is None or node.parent_id is None:
            return []
        return [n for n in self.children_sync(node.parent_id) if n.node_id != node_id]

    def context_bundle_sync(self, node_id: str) -> dict[str, Any]:
        """Assemble a priority-ordered context bundle for a node."""
        from backend.graph.context import build_context_bundle
        # This mixin is only ever mixed into ProjectGraph, which is what
        # build_context_bundle traverses.
        return build_context_bundle(cast("ProjectGraph", self), node_id)

    def any_trace_to(self, target_id: str, source_type: str) -> bool:
        """Return True if any node of source_type has target_id in its trace_to list."""
        for _nid, data in self._g.nodes(data=True):
            if data.get("node_type") == source_type:
                if target_id in (data.get("trace_to") or []):
                    return True
        return False

    def nodes_tracing_to(self, target_id: str, source_type: str | None = None) -> list[str]:
        """Return node_ids of all nodes that have target_id in their trace_to."""
        result: list[str] = []
        for nid, data in self._g.nodes(data=True):
            if source_type and data.get("node_type") != source_type:
                continue
            if target_id in (data.get("trace_to") or []):
                result.append(nid)
        return result

    def predecessors_sync(
        self, node_id: str, edge_type: str | None = None
    ) -> list[GraphNode]:
        """Return all nodes with directed edges pointing to node_id (synchronous)."""
        if not self._g.has_node(node_id):
            return []
        nodes: list[GraphNode] = []
        for pred_id in self._g.predecessors(node_id):
            if edge_type is not None:
                edge_data = self._g.edges.get((pred_id, node_id), {})
                if edge_data.get("edge_type") != edge_type:
                    continue
            node = self.node_sync(pred_id)
            if node is not None:
                nodes.append(node)
        return nodes
