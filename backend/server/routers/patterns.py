"""Patterns router — module health and interaction diagrams."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from backend.graph.engine import ProjectGraph
from backend.graph.models import NodeType
from backend.server.dependencies import get_project_graph

router = APIRouter(prefix="/patterns", tags=["patterns"])


@router.get("/health")
async def get_module_health(
    graph: ProjectGraph = Depends(get_project_graph),
) -> list[dict[str, Any]]:
    """Return health status for each MODULE/CLASS/FUNC node."""
    if graph is None:
        return []

    design_types = [NodeType.MODULE.value]
    nodes = []
    for node_type in design_types:
        nodes.extend(await graph.nodes_by_type(node_type))

    result = []
    for node in nodes:
        children = await graph.children(node.node_id)
        code_count = sum(1 for c in children if c.node_type == NodeType.CODE.value)
        result.append({
            "node_id": node.node_id,
            "title": node.title,
            "node_type": node.node_type,
            "lifecycle": node.lifecycle.value,
            "has_implementation": code_count > 0,
            "code_nodes": code_count,
        })
    return result


@router.get("/interactions/{module_id}")
async def get_module_interactions(
    module_id: str,
    graph: ProjectGraph = Depends(get_project_graph),
) -> dict[str, Any]:
    """Return outgoing/incoming edges for a module node as an interaction diagram."""
    if graph is None:
        return {"module_id": module_id, "diagram": "", "edges": []}

    outgoing = await graph.edges_from(module_id)
    incoming = await graph.edges_to(module_id)

    edges = [
        {"direction": "out", "edge_type": e.edge_type, "target": e.target_id}
        for e in outgoing
    ] + [
        {"direction": "in", "edge_type": e.edge_type, "source": e.source_id}
        for e in incoming
    ]

    # Simple mermaid-like text diagram
    lines = [f"  {module_id}"]
    for e in outgoing:
        lines.append(f"  {module_id} --{e.edge_type}--> {e.target_id}")
    for e in incoming:
        lines.append(f"  {e.source_id} --{e.edge_type}--> {module_id}")
    diagram = "\n".join(lines)

    return {"module_id": module_id, "diagram": diagram, "edges": edges}
