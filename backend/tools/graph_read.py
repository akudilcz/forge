"""graph_read — query the Project Graph."""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from backend.tools.async_utils import run_async
from backend.tools.base import ForgeTool


class _Args(BaseModel):
    operation: str = Field(
        description=(
            "Operation to perform: "
            "node | nodes | children | ancestors | descendants | siblings"
        )
    )
    node_id: str = Field(default="", description="Node ID (required for most operations).")
    type_prefix: str = Field(default="", description="Filter nodes by ID prefix (e.g. 'LLR-' or 'HLR-').")
    node_type: str = Field(default="", description="Filter nodes by node_type (e.g. 'CONTRACT', 'MODULE').")
    edge_type: str = Field(default="", description="Filter edges by type (e.g. 'IMPLEMENTS').")
    depth: int = Field(default=5, description="Max traversal depth for ancestors/descendants.")


class GraphReadTool(ForgeTool):
    """Read-only query tool for the Project Graph.

    Supports traversal operations (ancestors, descendants, siblings) and
    filtered node listing.  Graph access is always async; sync callers are
    handled via a thread-pool executor.
    """

    name: str = "graph_read"
    description: str = (
        "Query the Project Graph for nodes, edges, ancestors, descendants. "
        "Use node_type='LLR' to list all LLR nodes, or type_prefix='LLR-' to filter by ID prefix. "
        "Use this to understand requirements, architecture, and compliance state."
    )
    args_schema: type[BaseModel] = _Args

    def __init__(self, graph: object = None) -> None:
        """Args:
            graph: ProjectGraph instance to query (injected by the tool factory).
        """
        super().__init__()
        self._graph = graph

    def _execute(
        self,
        operation: str,
        node_id: str = "",
        type_prefix: str = "",
        node_type: str = "",
        edge_type: str = "",
        depth: int = 5,
    ) -> str:
        """Dispatch the requested graph query operation and return JSON results.

        Returns an error string on failure; otherwise JSON-serialised node data.
        """
        graph = self._graph
        if graph is None:
            return "ERROR: Graph not available"

        try:
            coro = self._dispatch(graph, operation, node_id, type_prefix, node_type, edge_type, depth)
            return run_async(coro, timeout=30)
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    async def _dispatch(
        self,
        graph: object,
        operation: str,
        node_id: str,
        type_prefix: str,
        node_type: str,
        edge_type: str,
        depth: int,
    ) -> str:
        """Route operation string to the appropriate graph async method and return serialised results."""
        op = operation.strip().lower()

        if op == "node":
            node = await graph.node(node_id)  # type: ignore[attr-defined]
            if node is None:
                return f"Node not found: {node_id}"
            return node.model_dump_json(indent=2)

        elif op == "nodes":
            nodes = graph.all_nodes()  # type: ignore[attr-defined]
            if type_prefix:
                nodes = [n for n in nodes if n.node_id.startswith(type_prefix)]
            if node_type:
                nodes = [n for n in nodes if n.node_type.upper() == node_type.upper()]
            result = [
                {"node_id": n.node_id, "label": n.title,
                 "type": n.node_type, "state": n.lifecycle.value,
                 "trace_to": n.trace_to,
                 "properties": n.properties}
                for n in nodes
            ]
            return json.dumps(result, indent=2)

        elif op == "children":
            nodes = graph.children_sync(node_id)  # type: ignore[attr-defined]
            return json.dumps([n.model_dump() for n in nodes], indent=2, default=str)

        elif op == "ancestors":
            nodes = await graph.ancestors(node_id)  # type: ignore[attr-defined]
            return json.dumps([n.model_dump() for n in nodes], indent=2, default=str)

        elif op == "descendants":
            nodes = await graph.descendants(node_id)  # type: ignore[attr-defined]
            return json.dumps([n.model_dump() for n in nodes], indent=2, default=str)

        elif op == "siblings":
            nodes = graph.siblings_sync(node_id)  # type: ignore[attr-defined]
            return json.dumps([n.model_dump() for n in nodes], indent=2, default=str)

        else:
            return (
                f"Unknown operation '{operation}'. Valid: "
                "node, nodes (supports type_prefix and node_type filters), "
                "children, ancestors, descendants, siblings"
            )
