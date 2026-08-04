"""Compliance router — DO-178C / DO-254 traceability and compliance reporting."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from backend.graph.engine import ProjectGraph
from backend.graph.models import EdgeType, NodeType
from backend.server.dependencies import get_forge_config, get_project_graph

router = APIRouter(prefix="/compliance", tags=["compliance"])

# DO-178C objectives by DAL (subset — enough to demonstrate the pattern)
_DAL_OBJECTIVES: dict[str, list[dict[str, Any]]] = {
    "A": [
        {"id": "HLR-001", "title": "High-Level Requirements", "required": True},
        {"id": "LLR-001", "title": "Low-Level Requirements", "required": True},
        {"id": "SW-001", "title": "Source Code", "required": True},
        {"id": "VER-001", "title": "Verification Evidence", "required": True},
    ],
    "B": [
        {"id": "HLR-001", "title": "High-Level Requirements", "required": True},
        {"id": "LLR-001", "title": "Low-Level Requirements", "required": True},
        {"id": "SW-001", "title": "Source Code", "required": True},
        {"id": "VER-001", "title": "Verification Evidence", "required": True},
    ],
    "C": [
        {"id": "HLR-001", "title": "High-Level Requirements", "required": True},
        {"id": "SW-001", "title": "Source Code", "required": True},
        {"id": "VER-001", "title": "Verification Evidence", "required": False},
    ],
    "D": [
        {"id": "SW-001", "title": "Source Code", "required": True},
    ],
}


@router.get("/report")
async def get_compliance_report(
    dal: str = "B",
    graph: ProjectGraph = Depends(get_project_graph),
    config: Any = Depends(get_forge_config),
) -> dict[str, Any]:
    """Return a DO-178C compliance summary from the graph."""
    if graph is None:
        return {"status": "not_started", "dal": dal}

    dal = dal.upper()
    req_nodes = await graph.nodes_by_type(NodeType.HLR.value)
    code_nodes = await graph.nodes_by_type(NodeType.CODE.value)
    hlr_cases = await graph.nodes_by_type(NodeType.CASE_HLR.value)
    llr_cases = await graph.nodes_by_type(NodeType.CASE_LLR.value)
    case_nodes = hlr_cases + llr_cases
    result_nodes = await graph.nodes_by_type(NodeType.RESULT.value)
    edges = await graph.all_edges()

    implemented = {e.target_id for e in edges if e.edge_type == EdgeType.IMPLEMENTS.value}
    verified = {e.target_id for e in edges if e.edge_type == EdgeType.VERIFIES.value}

    total_req = len(req_nodes)
    untraced = sum(1 for r in req_nodes if r.node_id not in implemented)
    uncovered = sum(1 for r in req_nodes if r.node_id not in verified)
    compliance_pct = round((total_req - untraced) / max(total_req, 1) * 100, 1)

    return {
        "status": "active" if req_nodes else "not_started",
        "dal": dal,
        "standard": "DO-178C",
        "total_requirements": total_req,
        "total_code_nodes": len(code_nodes),
        "total_test_cases": len(case_nodes),
        "total_results": len(result_nodes),
        "untraced_requirements": untraced,
        "uncovered_requirements": uncovered,
        "compliance_percent": compliance_pct,
    }


@router.get("/objectives")
async def get_objectives(
    dal: str = "B",
    graph: ProjectGraph = Depends(get_project_graph),
) -> list[dict[str, Any]]:
    """Return DO-178C objectives for the given DAL with satisfaction status."""
    dal = dal.upper()
    objectives = _DAL_OBJECTIVES.get(dal, _DAL_OBJECTIVES["B"])

    if graph is None:
        return [{"dal": dal, "satisfied": False, **obj} for obj in objectives]

    hlr_nodes = await graph.nodes_by_type(NodeType.HLR.value)
    llr_nodes = await graph.nodes_by_type(NodeType.LLR.value)
    code_nodes = await graph.nodes_by_type(NodeType.CODE.value)
    result_nodes = await graph.nodes_by_type(NodeType.RESULT.value)

    def _satisfied(obj_id: str) -> bool:
        """Return True when the graph contains evidence satisfying the given objective prefix."""
        if obj_id.startswith("HLR"):
            return bool(hlr_nodes)
        if obj_id.startswith("LLR"):
            return bool(llr_nodes)
        if obj_id.startswith("SW"):
            return bool(code_nodes)
        if obj_id.startswith("VER"):
            return bool(result_nodes)
        return False

    return [
        {"dal": dal, "satisfied": _satisfied(obj["id"]), **obj}
        for obj in objectives
    ]
