"""Quality router — live gap analysis and code review metrics."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from backend.analysis.gap_analyser import GapAnalyser
from backend.graph.engine import ProjectGraph
from backend.graph.models import NodeType
from backend.server.dependencies import get_project_graph

router = APIRouter(prefix="/quality", tags=["quality"])

_analyser = GapAnalyser()


@router.get("/findings")
async def get_findings(
    graph: ProjectGraph = Depends(get_project_graph),
) -> list[dict[str, Any]]:
    """Return all current gaps as quality findings."""
    if graph is None:
        return []
    gaps = _analyser.analyse(graph)
    return [
        {
            "gap_id": f"{g.type.value}:{g.node_id}",
            "type": g.type.value,
            "priority": g.priority.value,
            "node_id": g.node_id,
            "description": g.description,
            "context": g.context,
        }
        for g in gaps
    ]


@router.get("/metrics")
async def get_quality_metrics(
    graph: ProjectGraph = Depends(get_project_graph),
) -> dict[str, Any]:
    """Return aggregated quality metrics from the graph."""
    if graph is None:
        return {"status": "not_started"}

    all_nodes = graph.all_nodes()
    gaps = _analyser.analyse(graph)

    # Node counts by type
    counts: dict[str, int] = {}
    for node in all_nodes:
        counts[node.node_type] = counts.get(node.node_type, 0) + 1

    # Gap counts by type
    gap_counts: dict[str, int] = {}
    for gap in gaps:
        gap_counts[gap.type.value] = gap_counts.get(gap.type.value, 0) + 1

    # Review records
    record_nodes = [n for n in all_nodes if n.node_type == NodeType.RECORD.value]
    review_count = sum(1 for r in record_nodes if r.properties.get("record_type") == "review")

    return {
        "status": "active" if all_nodes else "not_started",
        "total_nodes": len(all_nodes),
        "total_gaps": len(gaps),
        "node_counts": counts,
        "gap_counts": gap_counts,
        "review_records": review_count,
    }
