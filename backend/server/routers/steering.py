"""Steering router — steering directives stored as RECORD nodes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.graph.engine import ProjectGraph
from backend.graph.models import GraphNode, NodeType
from backend.server.dependencies import get_project_graph

router = APIRouter(prefix="/steering", tags=["steering"])


class SteeringDirectiveBody(BaseModel):
    """Request body for creating a new steering directive.

    Attributes:
        content: The directive text.
        priority: Relative priority label (e.g. "normal", "high").
        category: Logical grouping (e.g. "general", "architecture").
    """

    content: str
    priority: str = "normal"
    category: str = "general"


@router.get("")
async def list_steering_directives(
    graph: ProjectGraph = Depends(get_project_graph),
) -> list[dict[str, Any]]:
    """Return all steering directives (RECORD nodes with record_type=steering)."""
    if graph is None:
        return []
    records = await graph.nodes_by_type(NodeType.RECORD.value)
    directives = [n for n in records if n.properties.get("record_type") == "steering"]
    return [
        {
            "node_id": n.node_id,
            "title": n.title,
            "content": n.content,
            "lifecycle": n.lifecycle.value,
            "properties": n.properties,
            "created_at": n.created_at.isoformat(),
        }
        for n in directives
    ]


@router.post("")
async def add_steering_directive(
    body: SteeringDirectiveBody,
    graph: ProjectGraph = Depends(get_project_graph),
) -> dict[str, Any]:
    """Add a new steering directive to the graph."""
    if graph is None:
        return {"status": "error", "detail": "Graph not available"}

    import uuid
    node_id = f"rec.steering.{uuid.uuid4().hex[:8]}"
    node = GraphNode(
        node_id=node_id,
        node_type=NodeType.RECORD.value,
        title=f"Steering: {body.content[:60]}",
        content=body.content,
        created_by="engineer",
        properties={
            "record_type": "steering",
            "priority": body.priority,
            "category": body.category,
        },
    )
    await graph.add_node(node)
    return {"status": "queued", "node_id": node_id}
