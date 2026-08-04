"""Graph router — full Project Graph query and mutation API.

Exposes REST endpoints for reading/patching nodes, traversing ancestors and
descendants, managing trace references, querying edges, and creating baselines.
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.config.models import ForgeConfig
from backend.graph.engine import ProjectGraph
from backend.graph.models import NodeType
from backend.server.dependencies import get_forge_config, get_project_graph

router = APIRouter(prefix="/graph", tags=["graph"])


class NodePatchBody(BaseModel):
    """Request body for PATCH /nodes/{node_id}: partial update of content, title, or properties."""

    content: str | None = None
    title: str | None = None
    properties: dict[str, Any] | None = None
    change_reason: str = ""


class UpdateTraceRequest(BaseModel):
    """Request body to replace the full trace_to list on a node."""

    trace_to: list[str]


class RemoveTracesRequest(BaseModel):
    """Request body to remove specific trace references from a node."""

    trace_refs: list[str]


class BaselineBody(BaseModel):
    """Request body for creating a new project baseline snapshot."""

    baseline_id: str
    baseline_type: str = "phase"
    description: str = ""


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


@router.get("/nodes")
async def list_nodes(
    type_prefix: str | None = None,
    lifecycle: str | None = None,
    review_required: bool | None = None,
    graph: ProjectGraph = Depends(get_project_graph),
) -> list[dict[str, Any]]:
    """Return nodes with optional type/lifecycle/review filters."""
    if graph is None:
        return []
    nodes = await graph.nodes(
        type_prefix=type_prefix,
        lifecycle=lifecycle,
        review_required=review_required,
    )

    return [
        {
            "node_id": n.node_id,
            "node_type": n.node_type,
            "layer": n.layer,
            "title": n.title,
            "lifecycle": n.lifecycle.value,
            "version": n.version,
            "content": n.content,
            "content_hash": n.content_hash,
            "parent_id": n.parent_id,
            "trace_to": n.trace_to,
            "properties": n.properties,
        }
        for n in nodes
    ]


@router.get("/nodes/{node_id}")
async def get_node(
    node_id: str,
    graph: ProjectGraph = Depends(get_project_graph),
) -> dict[str, Any]:
    """Return a single node by ID; raises 404 if not found."""
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not available")
    node = await graph.node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")
    return node.model_dump()


@router.patch("/nodes/{node_id}")
async def patch_node(
    node_id: str,
    body: NodePatchBody,
    graph: ProjectGraph = Depends(get_project_graph),
) -> dict[str, Any]:
    """Partially update a node's content, title, or properties and return the updated node plus stale impact count."""
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not available")
    node, impact = await graph.update_node(
        node_id,
        body.content,
        body.properties,
        "engineer",
        body.change_reason,
        title=body.title,
    )
    return {
        "node": node.model_dump(),
        "stale_count": len(impact.stale_nodes),
    }


@router.get("/nodes/{node_id}/ancestors")
async def get_ancestors(
    node_id: str,
    depth: int = 10,
    graph: ProjectGraph = Depends(get_project_graph),
) -> list[dict[str, Any]]:
    """Return ancestor nodes up to depth levels above node_id."""
    if graph is None:
        return []
    nodes = await graph.ancestors(node_id, depth=depth)
    return [{"node_id": n.node_id, "label": n.title} for n in nodes]


@router.get("/nodes/{node_id}/descendants")
async def get_descendants(
    node_id: str,
    depth: int = 10,
    graph: ProjectGraph = Depends(get_project_graph),
) -> list[dict[str, Any]]:
    """Return descendant nodes up to depth levels below node_id."""
    if graph is None:
        return []
    nodes = await graph.descendants(node_id, depth=depth)
    return [{"node_id": n.node_id, "label": n.title} for n in nodes]


@router.get("/nodes/{node_id}/impact")
async def get_impact(
    node_id: str,
    graph: ProjectGraph = Depends(get_project_graph),
) -> dict[str, Any]:
    """Return the impact set (nodes that would be marked stale) if node_id were changed."""
    if graph is None:
        return {}
    impact = await graph.impact_set(node_id)
    return impact.model_dump()


@router.get("/nodes/{node_id}/traceability")
async def get_traceability(
    node_id: str,
    graph: ProjectGraph = Depends(get_project_graph),
) -> dict[str, Any]:
    """Return the full traceability chain from node_id up to the PROJECT root."""
    if graph is None:
        return {}
    chain = await graph.traceability_chain(node_id)
    return chain.model_dump()


@router.get("/nodes/{node_id}/context")
async def get_node_context(
    node_id: str,
    graph: ProjectGraph = Depends(get_project_graph),
) -> dict[str, Any]:
    """Return the INNER/MIDDLE/OUTER context bundle assembled for a node."""
    if graph is None:
        return {"node_id": node_id, "inner": [], "middle": [], "outer": []}
    if not graph.node_sync(node_id):
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")
    return graph.context_bundle_sync(node_id)


@router.get("/nodes/{node_id}/siblings")
async def get_siblings(
    node_id: str,
    graph: ProjectGraph = Depends(get_project_graph),
) -> dict[str, Any]:
    """Return all structural siblings (same parent, different node_id)."""
    if graph is None:
        return {"nodes": []}
    nodes = graph.siblings_sync(node_id)
    return {"nodes": [n.model_dump() for n in nodes]}


@router.put("/nodes/{node_id}/trace")
async def update_trace(
    node_id: str,
    body: UpdateTraceRequest,
    graph: ProjectGraph = Depends(get_project_graph),
) -> dict[str, Any]:
    """Update the trace_to references on a node."""
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not available")
    existing = await graph.node(node_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")
    await graph.update_node(
        node_id, None, None, "engineer", "update_trace", trace_to=body.trace_to,
    )
    return {"ok": True, "node_id": node_id}


@router.delete("/nodes/{node_id}/trace")
async def remove_traces(
    node_id: str,
    body: RemoveTracesRequest,
    graph: ProjectGraph = Depends(get_project_graph),
) -> dict[str, Any]:
    """Remove specific trace_to references from a node."""
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not available")
    existing = await graph.node(node_id)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Node '{node_id}' not found")
    remove_set = set(body.trace_refs)
    new_trace = [t for t in (existing.trace_to or []) if t not in remove_set]
    removed = [t for t in (existing.trace_to or []) if t in remove_set]
    if removed:
        await graph.update_node(
            node_id, None, None, "engineer", "remove_traces", trace_to=new_trace,
        )
    return {"ok": True, "node_id": node_id, "removed": removed}


# ---------------------------------------------------------------------------
# Edges
# ---------------------------------------------------------------------------


@router.get("/edges")
async def list_edges(
    edge_type: str | None = None,
    source_id: str | None = None,
    target_id: str | None = None,
    graph: ProjectGraph = Depends(get_project_graph),
) -> list[dict[str, Any]]:
    """Return all supplementary traceability edges with optional filters."""
    if graph is None:
        return []
    edges = await graph.all_edges(
        edge_type=edge_type,
        source_id=source_id,
        target_id=target_id,
    )
    return [
        {
            "edge_id": e.edge_id,
            "edge_type": e.edge_type,
            "source_id": e.source_id,
            "target_id": e.target_id,
            "rationale": e.rationale,
            "confidence": e.confidence,
            "created_by": e.created_by,
            "created_at": e.created_at.isoformat(),
        }
        for e in edges
    ]


# ---------------------------------------------------------------------------
# Traceability + Compliance
# ---------------------------------------------------------------------------


@router.get("/traceability/gaps")
async def get_gaps(
    graph: ProjectGraph = Depends(get_project_graph),
) -> dict[str, Any]:
    """Return the traceability gap report for the current graph state."""
    if graph is None:
        return {}
    gaps = await graph.traceability_gaps()
    return gaps.model_dump()


@router.get("/compliance")
async def get_compliance(
    graph: ProjectGraph = Depends(get_project_graph),
    config: ForgeConfig = Depends(get_forge_config),
) -> dict[str, Any]:
    """Return DO-178C compliance summary from the graph."""
    if graph is None:
        return {}
    if config is None or not config.compliance.enabled:
        return {"enabled": False}

    gaps = await graph.traceability_gaps()
    req_nodes = await graph.nodes_by_type(NodeType.HLR.value)
    hlr_cases = await graph.nodes_by_type(NodeType.CASE_HLR.value)
    llr_cases = await graph.nodes_by_type(NodeType.CASE_LLR.value)
    tst_nodes = hlr_cases + llr_cases

    total_req = len(req_nodes)
    untraced = len(gaps.unimplemented_requirements)
    compliance_pct = round((total_req - untraced) / max(total_req, 1) * 100, 1)

    return {
        "enabled": True,
        "standard": config.compliance.standard,
        "dal": config.compliance.dal,
        "total_requirements": total_req,
        "total_tests": len(tst_nodes),
        "untraced_requirements": untraced,
        "compliance_percent": compliance_pct,
    }


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------


@router.get("/baselines")
async def list_baselines(
    graph: ProjectGraph = Depends(get_project_graph),
) -> list[dict[str, Any]]:
    """Return all baseline RECORD nodes stored in the graph."""
    if graph is None:
        return []
    all_records = await graph.nodes_by_type(NodeType.RECORD)
    baselines = [n for n in all_records if n.properties.get("record_type") == "baseline"]
    return [
        {"node_id": n.node_id, "label": n.title, "properties": n.properties}
        for n in baselines
    ]


@router.post("/baselines")
async def create_baseline(
    body: BaselineBody,
    graph: ProjectGraph = Depends(get_project_graph),
) -> dict[str, Any]:
    """Create a new project baseline snapshot node and return its node_id."""
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not available")
    node = await graph.create_baseline(body.baseline_id, body.baseline_type, body.description)
    return {"node_id": node.node_id, "status": "created"}
