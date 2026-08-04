"""Tests router — query test suite data from the project graph."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from backend.graph.engine import ProjectGraph
from backend.graph.models import NodeType
from backend.server.dependencies import get_project_graph

router = APIRouter(prefix="/tests", tags=["tests"])


@router.get("/summary")
async def get_tests_summary(
    graph: ProjectGraph = Depends(get_project_graph),
) -> dict[str, Any]:
    """Return aggregated test suite statistics from the graph."""
    if graph is None:
        return {"total": 0, "passed": 0, "failed": 0, "skipped": 0,
                "coverage_percent": None, "last_run": None}

    suites = await graph.nodes_by_type(NodeType.SUITE.value)
    hlr_cases = await graph.nodes_by_type(NodeType.CASE_HLR.value)
    llr_cases = await graph.nodes_by_type(NodeType.CASE_LLR.value)
    cases = hlr_cases + llr_cases
    results = await graph.nodes_by_type(NodeType.RESULT.value)

    passed = sum(1 for r in results if r.properties.get("status") == "passed")
    failed = sum(1 for r in results if r.properties.get("status") == "failed")
    skipped = sum(1 for r in results if r.properties.get("status") == "skipped")
    coverage = None
    if results:
        cov_values = [
            pct
            for r in results
            if (pct := r.properties.get("coverage_percent")) is not None
        ]
        if cov_values:
            coverage = round(sum(cov_values) / len(cov_values), 1)

    last_run = None
    if results:
        last_run = max(r.updated_at.isoformat() for r in results)

    return {
        "total": len(cases),
        "suites": len(suites),
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "coverage_percent": coverage,
        "last_run": last_run,
    }


@router.get("/results")
async def get_test_results(
    graph: ProjectGraph = Depends(get_project_graph),
) -> list[dict[str, Any]]:
    """Return all test RESULT nodes."""
    if graph is None:
        return []
    results = await graph.nodes_by_type(NodeType.RESULT.value)
    return [
        {
            "node_id": r.node_id,
            "title": r.title,
            "lifecycle": r.lifecycle.value,
            "content": r.content,
            "parent_id": r.parent_id,
            "properties": r.properties,
            "updated_at": r.updated_at.isoformat(),
        }
        for r in results
    ]


@router.get("/suites")
async def list_suites(
    graph: ProjectGraph = Depends(get_project_graph),
) -> list[dict[str, Any]]:
    """Return all SUITE nodes with their child CASE counts."""
    if graph is None:
        return []
    suites = await graph.nodes_by_type(NodeType.SUITE.value)
    output = []
    for suite in suites:
        cases = await graph.children(suite.node_id)
        output.append({
            "node_id": suite.node_id,
            "title": suite.title,
            "lifecycle": suite.lifecycle.value,
            "case_count": sum(1 for c in cases if c.node_type in (NodeType.CASE_HLR.value, NodeType.CASE_LLR.value)),
            "properties": suite.properties,
        })
    return output
