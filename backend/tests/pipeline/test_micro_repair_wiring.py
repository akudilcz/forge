"""Wiring tests: the batched micro-repair pre-pass (design/01 §7.4).

Every loop that dispatches batchable title/wording repair gaps per-gap must
first offer the cycle's gap list to ``apply_micro_repair_batches`` and then
dispatch ONLY the gaps the batch could not certify-resolve — the loud
per-gap fallback path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.pipeline.flow import ForgeFlow
from backend.pipeline.quality_loop import create_qual_check_graph
from backend.pipeline.steps import combined_quality
from backend.pipeline.structural_loop import create_structural_loop_graph
from backend.quality.checks import PHASE_TO_NODE_TYPES

MODULE_TYPE = PHASE_TO_NODE_TYPES[5][0]


def _gap(gap_type: GapType, node_id: str) -> Gap:
    return Gap(
        type=gap_type,
        priority=GapPriority.MAINTENANCE,
        node_id=node_id,
        description=f"{gap_type.value} on {node_id}",
    )


def _capture_batcher(remaining: list[Gap]) -> tuple[list[list[Gap]], Any]:
    calls: list[list[Gap]] = []

    async def fake(flow: Any, gaps: list[Gap]) -> list[Gap]:
        calls.append(list(gaps))
        return remaining

    return calls, fake


@pytest.fixture
def flow(tmp_path: Path) -> ForgeFlow:
    pool = MagicMock()
    graph = MagicMock()
    graph.all_nodes.return_value = []
    graph.node_sync.return_value = None
    config = MagicMock()
    config.project.workspace_dir = str(tmp_path)
    broadcaster = MagicMock()
    phase_store = MagicMock()
    phase_store.get_all.return_value = []
    return ForgeFlow(pool, graph, config, broadcaster, phase_store)


def _node(node_id: str, node_type: str) -> MagicMock:
    node = MagicMock()
    node.node_id = node_id
    node.node_type = node_type
    return node


@pytest.mark.asyncio
async def test_quality_loop_dispatches_only_batch_survivors(
    flow: ForgeFlow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """QualCheckGraph offers scanned gaps to the batcher; survivors go per-gap."""
    gaps = [_gap(GapType.SIBLING_TITLE_DUPLICATE, f"MOD-000{i}") for i in (1, 2, 3)]
    nodes = [_node(g.node_id, MODULE_TYPE) for g in gaps]
    flow.graph.all_nodes.return_value = nodes
    flow.graph.node_sync.side_effect = lambda nid: next(
        (n for n in nodes if n.node_id == nid), None
    )
    monkeypatch.setattr(
        flow, "_quality_gaps_for_types", lambda nt: {g.node_id: [g] for g in gaps}
    )
    monkeypatch.setattr(flow._analyser, "analyse", lambda g: [])

    calls, fake = _capture_batcher(remaining=[gaps[2]])
    monkeypatch.setattr(
        "backend.pipeline.quality_loop.apply_micro_repair_batches", fake
    )
    dispatched: list[Gap] = []

    async def capture(gap: Gap, attempt: int = 1) -> str:
        dispatched.append(gap)
        return ""

    monkeypatch.setattr(flow, "_dispatch", AsyncMock(side_effect=capture))

    graph = create_qual_check_graph(flow)
    await graph.ainvoke(
        {
            "phase": 5,
            "node_types": [MODULE_TYPE],
            "pass_num": 0,
            "pending_gaps": [],
            "count_before": 0,
            "total_checked": 0,
            "had_deletions": False,
            "pass_had_deletions": False,
        }
    )

    assert calls == [gaps]
    assert [g.node_id for g in dispatched] == ["MOD-0003"]


@pytest.mark.asyncio
async def test_structural_loop_offers_collected_gaps_to_batcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The structural loop's collect passes its cycle gap list through the batcher."""
    g1 = Gap(
        type=GapType.UNMODULARISED,
        priority=GapPriority.DESIGN,
        node_id="HLR-0001",
        description="unmodularised",
    )
    flow = MagicMock()
    collected = [[g1], []]
    flow._collect_phase_gaps = MagicMock(side_effect=lambda *a, **k: collected.pop(0))
    flow._open_gaps = [g1]
    flow._analyser.analyse = MagicMock(side_effect=lambda _g: list(flow._open_gaps))
    flow._broadcast_gap_list = MagicMock()
    flow._request_approval = AsyncMock()
    flow.state.single_step = False
    flow.state.iteration = 0

    async def dispatch(gap: Gap, *args: Any, **kwargs: Any) -> str:
        flow._open_gaps.remove(gap)
        return "ok"

    flow._dispatch = AsyncMock(side_effect=dispatch)

    calls, fake = _capture_batcher(remaining=[g1])
    monkeypatch.setattr(
        "backend.pipeline.structural_loop.apply_micro_repair_batches", fake
    )

    graph = create_structural_loop_graph(flow)
    await graph.ainvoke(
        {
            "phase": 5,
            "skip_approval": True,
            "iteration": 0,
            "gap_fail_counts": {},
            "abandoned": set(),
            "current_gaps": [],
            "single_step_done": False,
        }
    )

    assert calls == [[g1]]
    assert flow._dispatch.await_count == 1


@pytest.mark.asyncio
async def test_combined_quality_step_dispatches_only_batch_survivors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """combined_quality routes judge findings through the batcher first."""
    title_gaps = [_gap(GapType.VAGUE_TITLE, f"LLR-000{i}") for i in (1, 2, 3)]
    atomic_gap = _gap(GapType.NON_ATOMIC_REQUIREMENT, "LLR-0009")
    flow = MagicMock()
    flow.run_combined_quality_check = AsyncMock(
        return_value=[*title_gaps, atomic_gap]
    )
    flow.graph.node_sync = MagicMock(side_effect=lambda nid: _node(nid, "LLR"))
    flow._dispatch = AsyncMock(return_value="")

    calls, fake = _capture_batcher(remaining=[atomic_gap, title_gaps[1]])
    monkeypatch.setattr("backend.pipeline.steps.apply_micro_repair_batches", fake)

    result = await combined_quality(flow, 7)

    assert result["step_name"] == "combined_quality"
    # NON_ATOMIC sorted first, then the batcher sees the full sorted list.
    assert len(calls) == 1
    assert calls[0][0].type == GapType.NON_ATOMIC_REQUIREMENT
    assert {g.node_id for g in calls[0]} == {
        "LLR-0001", "LLR-0002", "LLR-0003", "LLR-0009",
    }
    dispatched = [c.args[0].node_id for c in flow._dispatch.await_args_list]
    assert dispatched == ["LLR-0009", "LLR-0002"]
