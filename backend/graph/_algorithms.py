"""AlgorithmMixin — graph traversal and analysis for ProjectGraph."""

from __future__ import annotations

from typing import TYPE_CHECKING

import networkx as nx

from backend.graph.models import (
    EdgeType,
    GraphEdge,
    GraphNode,
    ImpactSet,
    NodeType,
    TraceabilityChain,
    TraceabilityGaps,
)


class AlgorithmMixin:
    """Graph algorithm methods mixed into ProjectGraph."""

    if TYPE_CHECKING:
        # State and query methods supplied by ProjectGraph / QueryMixin.
        # Declared for type-checking only — never defined at runtime, so the
        # real QueryMixin implementations are always the ones that run.
        _g: nx.DiGraph

        async def node(self, node_id: str) -> GraphNode | None: ...

        def children_sync(self, parent_id: str) -> list[GraphNode]: ...

        async def nodes_by_type(self, node_type: str) -> list[GraphNode]: ...

        async def all_edges(
            self,
            edge_type: str | None = None,
            source_id: str | None = None,
            target_id: str | None = None,
        ) -> list[GraphEdge]: ...

    async def ancestors(self, node_id: str, depth: int = 10) -> list[GraphNode]:
        """Return ancestor nodes: structural parents + NX edge-based ancestors."""
        seen: set[str] = {node_id}
        results: list[GraphNode] = []

        # Structural: walk parent_id chain
        current = node_id
        for _ in range(depth):
            if not self._g.has_node(current):
                break
            parent_id = self._g.nodes[current].get("parent_id")
            if not parent_id or parent_id in seen:
                break
            seen.add(parent_id)
            n = await self.node(parent_id)
            if n:
                results.append(n)
            current = parent_id

        # Graph: nodes with a directed edge path TO node_id
        if self._g.has_node(node_id):
            for nid in nx.ancestors(self._g, node_id):
                if nid not in seen:
                    seen.add(nid)
                    n = await self.node(nid)
                    if n:
                        results.append(n)
        return results

    async def descendants(self, node_id: str, depth: int = 10) -> list[GraphNode]:
        """Return descendant nodes: structural children + NX edge-based."""
        seen: set[str] = {node_id}
        results: list[GraphNode] = []

        # Structural: BFS via parent_id relationships
        queue: list[tuple[str, int]] = [(node_id, 0)]
        while queue:
            current, level = queue.pop(0)
            if level >= depth:
                continue
            for child in self.children_sync(current):
                if child.node_id not in seen:
                    seen.add(child.node_id)
                    results.append(child)
                    queue.append((child.node_id, level + 1))

        # Graph: nodes reachable FROM node_id via directed edges
        if self._g.has_node(node_id):
            for nid in nx.descendants(self._g, node_id):
                if nid not in seen:
                    seen.add(nid)
                    n = await self.node(nid)
                    if n:
                        results.append(n)
        return results

    async def impact_set(self, node_id: str) -> ImpactSet:
        """Return the set of nodes that may become stale if node_id changes."""
        if not self._g.has_node(node_id):
            return ImpactSet(root_node_id=node_id)
        stale_nodes = await self.descendants(node_id)
        stale = [n.node_id for n in stale_nodes]
        return ImpactSet(root_node_id=node_id, stale_nodes=stale, stale_count=len(stale))

    async def traceability_chain(self, node_id: str) -> TraceabilityChain:
        """Return the upward ancestry chain for a node."""
        ancestor_nodes = await self.ancestors(node_id)
        return TraceabilityChain(
            node_id=node_id,
            ancestors=[
                {"node_id": n.node_id, "node_type": n.node_type, "label": n.title}
                for n in ancestor_nodes
            ],
        )

    async def traceability_gaps(self) -> TraceabilityGaps:
        """Return requirements without implementation and without test coverage."""
        req_nodes = await self.nodes_by_type(NodeType.HLR.value)
        code_nodes = await self.nodes_by_type(NodeType.CODE.value)
        edges = await self.all_edges()

        implemented_reqs = {
            e.target_id for e in edges if e.edge_type == EdgeType.IMPLEMENTS.value
        }
        verified_reqs = {
            e.target_id for e in edges if e.edge_type == EdgeType.VERIFIES.value
        }
        tested_code = {
            e.target_id for e in edges if e.edge_type == EdgeType.EXERCISES.value
        }

        unimplemented = [n.node_id for n in req_nodes if n.node_id not in implemented_reqs]
        uncovered = [n.node_id for n in req_nodes if n.node_id not in verified_reqs]
        untested = [n.node_id for n in code_nodes if n.node_id not in tested_code]

        return TraceabilityGaps(
            unimplemented_requirements=unimplemented,
            uncovered_requirements=uncovered,
            untested_code=untested,
        )
