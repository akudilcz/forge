"""Requirements router — query HLR and LLR nodes from the project graph."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from backend.graph.engine import ProjectGraph
from backend.graph.models import NodeType
from backend.server.dependencies import get_project_graph

router = APIRouter(prefix="/requirements", tags=["requirements"])


@router.get("")
async def get_requirements(
    level: str | None = None,
    lifecycle: str | None = None,
    graph: ProjectGraph = Depends(get_project_graph),
) -> list[dict[str, Any]]:
    """Return all HLR and LLR nodes, optionally filtered by level or lifecycle."""
    if graph is None:
        return []
    if level == "hlr":
        nodes = await graph.nodes_by_type(NodeType.HLR.value)
    elif level == "llr":
        nodes = await graph.nodes_by_type(NodeType.LLR.value)
    else:
        hlrs = await graph.nodes_by_type(NodeType.HLR.value)
        llrs = await graph.nodes_by_type(NodeType.LLR.value)
        nodes = hlrs + llrs
    if lifecycle:
        nodes = [n for n in nodes if n.lifecycle.value == lifecycle]
    return [
        {
            "node_id": n.node_id,
            "node_type": n.node_type,
            "title": n.title,
            "lifecycle": n.lifecycle.value,
            "content": n.content,
            "parent_id": n.parent_id,
            "properties": n.properties,
        }
        for n in nodes
    ]


@router.get("/gaps")
async def get_traceability_gaps(
    graph: ProjectGraph = Depends(get_project_graph),
) -> dict[str, Any]:
    """Return unimplemented and uncovered requirements."""
    if graph is None:
        return {"unimplemented": [], "uncovered": []}
    gaps = await graph.traceability_gaps()
    return {
        "unimplemented": gaps.unimplemented_requirements,
        "uncovered": gaps.uncovered_requirements,
        "untested_code": gaps.untested_code,
    }
