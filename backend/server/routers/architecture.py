"""Architecture router — system design graph endpoints.

Exposes read endpoints for ARCHITECTURE and MODULE nodes and their connecting edges,
providing the data needed to render architecture diagrams in the UI.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from backend.graph.engine import ProjectGraph
from backend.graph.models import NodeType
from backend.server.dependencies import get_project_graph

router = APIRouter(prefix="/architecture", tags=["architecture"])


@router.get("")
async def get_architecture(
    graph: ProjectGraph = Depends(get_project_graph),
) -> dict[str, Any]:
    """Return the current architecture graph (ARCHITECTURE, MODULE nodes and edges)."""
    if graph is None:
        return {"nodes": [], "edges": []}

    arch_nodes = await graph.nodes_by_type(NodeType.ARCHITECTURE.value)
    module_nodes = await graph.nodes_by_type(NodeType.MODULE.value)
    nodes = arch_nodes + module_nodes

    edges = []
    for node in nodes:
        from_edges = await graph.edges_from(node.node_id)
        edges.extend(
            {
                "edge_id": e.edge_id,
                "edge_type": e.edge_type,
                "source_id": e.source_id,
                "target_id": e.target_id,
                "confidence": e.confidence,
            }
            for e in from_edges
        )

    return {
        "nodes": [
            {
                "node_id": n.node_id,
                "title": n.title,
                "node_type": n.node_type,
                "layer": n.layer,
                "lifecycle": n.lifecycle.value,
                "content": n.content,
                "properties": n.properties,
            }
            for n in nodes
        ],
        "edges": edges,
    }


@router.get("/modules/{module_id}")
async def get_module(
    module_id: str,
    graph: ProjectGraph = Depends(get_project_graph),
) -> dict[str, Any]:
    """Return a single architecture module node."""
    if graph is None:
        return {}
    node = await graph.node(module_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Module '{module_id}' not found")
    return {
        "node_id": node.node_id,
        "title": node.title,
        "content": node.content,
        "properties": node.properties,
    }
