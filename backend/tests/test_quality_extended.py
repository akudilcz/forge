"""Coverage-focused tests for backend.crew.quality code paths that weren't
otherwise exercised. Mocks the flow object and its collaborators at the boundary."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.crew.quality import (
    run_design_consolidation,
    run_semantic_check,
    scan_qual_detect,
)


def _node(nid: str, ntype: str, **kw: Any) -> SimpleNamespace:
    return SimpleNamespace(
        node_id=nid, node_type=ntype,
        parent_id=kw.get("parent_id", ""),
        title=kw.get("title", ""),
        content=kw.get("content", ""),
        trace_to=kw.get("trace_to", []),
        properties=kw.get("properties", {}),
    )


def _flow(nodes: list[SimpleNamespace]) -> MagicMock:
    flow = MagicMock()
    flow.graph = MagicMock()
    flow.graph.all_nodes = MagicMock(return_value=nodes)
    flow.graph.node_sync = MagicMock(side_effect=lambda nid: next((n for n in nodes if n.node_id == nid), None))
    flow.graph.children_sync = MagicMock(side_effect=lambda pid: [n for n in nodes if n.parent_id == pid])
    flow._batch_new_node_ids = None
    flow.config = MagicMock()
    flow._analyser = MagicMock()
    flow._analyser.analyse = MagicMock(return_value=[])
    flow._broadcast_gap_list = MagicMock()
    flow._set_phase_status = MagicMock()
    flow._quality_gaps_for_types = MagicMock(return_value={})
    return flow


@pytest.mark.asyncio
async def test_run_semantic_check_no_candidates() -> None:
    flow = _flow([_node("HLR-1", "HLR", content="the system shall X.", parent_id="PARA-1")])
    result = await run_semantic_check(flow, phase=3)
    assert result == 0







@pytest.mark.asyncio
async def test_scan_qual_detect_no_nodes_for_phase() -> None:
    flow = _flow([])
    result = await scan_qual_detect(flow, phase=3)
    assert result == []


@pytest.mark.asyncio
async def test_scan_qual_detect_unknown_phase() -> None:
    flow = _flow([_node("HLR-1", "HLR", content="x")])
    # Phase 0/1 have no node types per PHASE_TO_NODE_TYPES
    result = await scan_qual_detect(flow, phase=0)
    assert result == []


def test_build_semantic_checker_wires_llm_and_graph() -> None:
    from backend.crew.quality import _build_semantic_checker

    flow = MagicMock()
    flow.config = MagicMock()
    flow.graph = MagicMock()
    with (
        patch("backend.agents.factory.build_llm", return_value="llm-sentinel"),
        patch(
            "backend.crew.semantic_duplicate_check.create_semantic_checker",
            return_value="checker-sentinel",
        ) as ctor,
    ):
        checker = _build_semantic_checker(flow)
    assert checker == "checker-sentinel"
    ctor.assert_called_once_with("llm-sentinel", flow.graph)


def test_build_design_consolidator_wires_llm_and_graph() -> None:
    from backend.crew.quality import _build_design_consolidator

    flow = MagicMock()
    flow.config = MagicMock()
    flow.graph = MagicMock()
    with (
        patch("backend.agents.factory.build_llm", return_value="llm-sentinel"),
        patch(
            "backend.crew.design_consolidation.create_design_consolidator",
            return_value="cons-sentinel",
        ) as ctor,
    ):
        consolidator = _build_design_consolidator(flow)
    assert consolidator == "cons-sentinel"
    ctor.assert_called_once_with("llm-sentinel", flow.graph)


@pytest.mark.asyncio
async def test_scan_qual_detect_emits_findings_and_broadcasts() -> None:
    """Exercises the scan_qual_detect body (lines 375-411)."""
    hlr = _node("HLR-1", "HLR", content="the system shall X.", parent_id="P-1")
    flow = _flow([hlr])
    flow._quality_gaps_for_types = MagicMock(
        return_value={
            "HLR-1": [
                Gap(
                    type=GapType.NON_EARS_REQUIREMENT,
                    priority=GapPriority.MAINTENANCE,
                    node_id="HLR-1",
                    description="non-ears",
                )
            ]
        }
    )
    findings = await scan_qual_detect(flow, phase=3)
    assert len(findings) == 1
    assert findings[0]["node_id"] == "HLR-1"
    assert findings[0]["gap_type"] == "NON_EARS_REQUIREMENT"
    flow._broadcast_gap_list.assert_called_once()


@pytest.mark.asyncio
async def test_run_design_consolidation_no_modules() -> None:
    flow = _flow([])
    flow.run_design_consolidation = AsyncMock(return_value=0)
    # Function scans for MODULEs; with none present, returns 0.
    result = await run_design_consolidation(flow)
    assert result == 0
