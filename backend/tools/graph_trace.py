"""graph_trace — traceability queries on the project graph."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from backend.tools.async_utils import run_async
from backend.tools.base import ForgeTool

_NAME = "graph_trace"
_DESCRIPTION = (
    "Query traceability relationships: trace chain for a node, "
    "which nodes trace to a target, or coverage report by type."
)


class _Args(BaseModel):
    operation: str = Field(
        description="Operation: 'chain' | 'traced_by' | 'coverage'.",
    )
    node_id: str = Field(
        default="",
        description="Node ID (required for chain / traced_by).",
    )
    node_type: str = Field(
        default="",
        description="Node type (required for coverage, e.g. 'HLR').",
    )


class GraphTraceTool(ForgeTool):
    """Traceability query tool supporting chain, reverse-lookup, and coverage report operations."""

    name: str = _NAME
    description: str = _DESCRIPTION
    args_schema: type[BaseModel] = _Args

    def __init__(self, graph: Any = None) -> None:
        """Args:
            graph: ProjectGraph instance to query (injected by the tool factory).
        """
        super().__init__(name=_NAME, description=_DESCRIPTION)
        self._graph = graph

    def _execute(self, *args: Any, **kwargs: Any) -> str:
        """Dispatch entry point — forwards schema-validated args to :meth:`_query`."""
        return self._query(*args, **kwargs)

    def _query(
        self, operation: str, node_id: str = "", node_type: str = "",
    ) -> str:
        """Dispatch to chain, traced_by, or coverage sub-operation and return JSON results."""
        graph = self._graph
        if graph is None:
            return "ERROR: Graph not available"

        op = operation.strip().lower()
        try:
            if op == "chain":
                chain_json: str = run_async(self._chain(graph, node_id))
                return chain_json
            elif op == "traced_by":
                return self._traced_by(graph, node_id)
            elif op == "coverage":
                return self._coverage(graph, node_type)
            else:
                return f"Unknown operation '{operation}'. Valid: chain, traced_by, coverage"
        except Exception as exc:  # noqa: BLE001
            return f"ERROR: {exc}"

    @staticmethod
    async def _chain(graph: Any, node_id: str) -> str:
        """Return the ancestry chain from node up to PROJECT root."""
        if not node_id:
            return "ERROR: node_id is required for 'chain'"
        chain = await graph.traceability_chain(node_id)
        return json.dumps({
            "node_id": chain.node_id,
            "ancestors": chain.ancestors,
        }, indent=2)

    @staticmethod
    def _traced_by(graph: Any, node_id: str) -> str:
        """Return all nodes whose trace_to references this node."""
        if not node_id:
            return "ERROR: node_id is required for 'traced_by'"
        tracer_ids = graph.nodes_tracing_to(node_id)
        results = []
        for tid in tracer_ids:
            n = graph.node_sync(tid)
            if n:
                results.append({
                    "node_id": n.node_id,
                    "node_type": n.node_type,
                    "title": n.title,
                })
        if not results:
            return f"No nodes trace to '{node_id}'"
        return json.dumps(results, indent=2)

    @staticmethod
    def _coverage(graph: Any, node_type: str) -> str:
        """Per-node trace coverage report for a given type."""
        if not node_type:
            return "ERROR: node_type is required for 'coverage'"
        nodes = [
            n for n in graph.all_nodes()
            if n.node_type.upper() == node_type.upper()
        ]
        if not nodes:
            return f"No nodes of type '{node_type}' found"
        results = []
        for n in nodes:
            tracers = graph.nodes_tracing_to(n.node_id)
            tracer_types = []
            for tid in tracers:
                tn = graph.node_sync(tid)
                if tn:
                    tracer_types.append(f"{tn.node_type}:{tn.node_id}")
            results.append({
                "node_id": n.node_id,
                "title": n.title,
                "traced_by": tracer_types or ["(none)"],
            })
        return json.dumps(results, indent=2)
