"""Contracts router — query CONTRACT nodes from the project graph."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from backend.graph.engine import ProjectGraph
from backend.graph.models import NodeType
from backend.server.dependencies import get_project_graph

router = APIRouter(prefix="/contracts", tags=["contracts"])


@router.get("")
async def list_contracts(
    graph: ProjectGraph = Depends(get_project_graph),
) -> list[dict[str, Any]]:
    """Return all CONTRACT nodes from the graph."""
    if graph is None:
        return []
    nodes = await graph.nodes_by_type(NodeType.CONTRACT.value)
    return [
        {
            "node_id": n.node_id,
            "title": n.title,
            "lifecycle": n.lifecycle.value,
            "content": n.content,
            "parent_id": n.parent_id,
            "properties": n.properties,
        }
        for n in nodes
    ]


@router.get("/{contract_id}")
async def get_contract(
    contract_id: str,
    graph: ProjectGraph = Depends(get_project_graph),
) -> dict[str, Any]:
    """Return a single contract node."""
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not ready")
    node = await graph.node(contract_id)
    if node is None or node.node_type != NodeType.CONTRACT.value:
        raise HTTPException(status_code=404, detail=f"Contract '{contract_id}' not found")
    return node.model_dump()
