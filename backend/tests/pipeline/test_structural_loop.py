"""Tests for StructuralLoopGraph — per-phase structural gap resolution."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.pipeline.structural_loop import StructuralLoopState, create_structural_loop_graph


def _make_flow(
    gaps: list[Gap] | None = None,
    dispatch_changes_graph: bool = True,
    dispatch_return: str = "done",
) -> Any:
    """Return a MagicMock standing in for ForgeFlow (typed Any — it is a mock)."""
    flow = MagicMock()
    flow._collect_phase_gaps = MagicMock(return_value=gaps or [])
    flow._analyser.analyse.return_value = []
    flow._broadcast_gap_list = MagicMock()
    flow._request_approval = AsyncMock()
    flow.state.single_step = False
    flow.state.iteration = 0

    pre = [0]

    def graph_state_count() -> int:
        return pre[0]

    def dispatch_side_effect(*args: Any, **kwargs: Any) -> str:
        if dispatch_changes_graph:
            pre[0] += 1
        return dispatch_return

    flow._graph_state_count = graph_state_count
    flow._dispatch = AsyncMock(side_effect=dispatch_side_effect)
    return flow


def _initial_state(phase: int = 5) -> StructuralLoopState:
    return {
        "phase": phase,
        "skip_approval": False,
        "iteration": 0,
        "gap_fail_counts": {},
        "abandoned": set(),
        "current_gaps": [],
        "single_step_done": False,
    }


def _gap(node_id: str = "MOD-0001") -> Gap:
    return Gap(
        type=GapType.UNMODULARISED,
        priority=GapPriority.DESIGN,
        node_id=node_id,
        description="test gap",
    )


@pytest.mark.asyncio
async def test_structural_loop_graph_no_gaps_calls_approval() -> None:
    """When no structural gaps exist, finalize runs and requests approval."""
    flow = _make_flow(gaps=[])
    graph = create_structural_loop_graph(flow)
    await graph.ainvoke(_initial_state())
    flow._request_approval.assert_awaited_once_with(5)


@pytest.mark.asyncio
async def test_structural_loop_graph_batch_processes_multiple_gaps() -> None:
    """Multiple gaps from a single collect are dispatched without re-collecting."""
    gap1 = _gap("MOD-0001")
    gap2 = _gap("MOD-0002")
    gap3 = _gap("MOD-0003")
    call_num = [0]

    def collect_side(*args: Any, **kwargs: Any) -> list[Gap]:
        call_num[0] += 1
        return [gap1, gap2, gap3] if call_num[0] == 1 else []

    flow = _make_flow()
    flow._collect_phase_gaps.side_effect = collect_side

    graph = create_structural_loop_graph(flow)
    result = await graph.ainvoke(_initial_state())

    assert flow._dispatch.await_count == 3
    assert result["iteration"] == 3
    assert flow._collect_phase_gaps.call_count == 2


@pytest.mark.asyncio
async def test_gap_already_resolved_skips_unchunked_with_existing_paras() -> None:
    """UNCHUNKED_DOCUMENT gap is skipped if PARAs already exist (e.g. from partial retry)."""
    from backend.graph.models import GraphNode, NodeType
    from backend.pipeline.structural_loop import _gap_already_resolved

    flow: Any = MagicMock()
    para = GraphNode(
        node_id="PARA-0001", node_type=NodeType.PARA.value, title="Para 1", content="content"
    )
    flow.graph.children_sync.return_value = [para]

    gap = Gap(
        type=GapType.UNCHUNKED_DOCUMENT,
        priority=GapPriority.DOCUMENT_STRUCTURE,
        node_id="DOCUMENT-0001",
        description="needs chunking",
    )
    assert _gap_already_resolved(flow, gap) is True


@pytest.mark.asyncio
async def test_gap_already_resolved_false_for_no_paras() -> None:
    """UNCHUNKED_DOCUMENT gap is not skipped when no PARAs exist."""
    from backend.pipeline.structural_loop import _gap_already_resolved

    flow: Any = MagicMock()
    flow.graph.children_sync.return_value = []

    gap = Gap(
        type=GapType.UNCHUNKED_DOCUMENT,
        priority=GapPriority.DOCUMENT_STRUCTURE,
        node_id="DOCUMENT-0001",
        description="needs chunking",
    )
    assert _gap_already_resolved(flow, gap) is False


@pytest.mark.asyncio
async def test_gap_already_resolved_false_for_other_gap_types() -> None:
    """Non-UNCHUNKED_DOCUMENT gaps are never pre-resolved."""
    from backend.pipeline.structural_loop import _gap_already_resolved

    flow: Any = MagicMock()
    gap = Gap(
        type=GapType.UNMODULARISED,
        priority=GapPriority.DESIGN,
        node_id="HLR-0001",
        description="test",
    )
    assert _gap_already_resolved(flow, gap) is False


@pytest.mark.asyncio
async def test_quota_error_propagates_out_of_loop() -> None:
    """DispatchQuotaError propagates out of the loop — quota exhaustion halts
    the run loudly instead of finalizing as if the phase had been processed."""
    from backend.pipeline.dispatch import DispatchQuotaError

    gap1 = _gap("MOD-0001")
    gap2 = _gap("MOD-0002")
    call_num = [0]

    def collect_side(*args: Any, **kwargs: Any) -> list[Gap]:
        call_num[0] += 1
        return [gap1, gap2] if call_num[0] == 1 else []

    flow = _make_flow()
    flow._collect_phase_gaps.side_effect = collect_side
    flow._dispatch = AsyncMock(side_effect=DispatchQuotaError("quota exhausted"))

    graph = create_structural_loop_graph(flow)
    with pytest.raises(DispatchQuotaError):
        await graph.ainvoke(_initial_state())

    # Only one dispatch attempted — the second gap is never reached
    assert flow._dispatch.await_count == 1


@pytest.mark.asyncio
async def test_failed_dispatch_resets_wq_status_to_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When dispatch fails, work queue item is reset to pending."""
    gap = _gap()

    call_num = [0]

    def collect_side(*args: Any, **kwargs: Any) -> list[Gap]:
        call_num[0] += 1
        # Return the gap on first collect, then empty to stop the loop
        return [gap] if call_num[0] == 1 else []

    flow = _make_flow(dispatch_changes_graph=False)
    flow._collect_phase_gaps.side_effect = collect_side

    # Track work_queue.update_status calls
    from backend.core.work_queue import work_queue

    status_updates: list[tuple[str, str]] = []
    orig_update = work_queue.update_status

    def track_update(item_id: str, status: str) -> None:
        status_updates.append((item_id, status))
        orig_update(item_id, status)

    # monkeypatch restores the original bound method at teardown.
    monkeypatch.setattr(work_queue, "update_status", track_update)

    graph = create_structural_loop_graph(flow)
    await graph.ainvoke(_initial_state())

    # Check that items were reset to "pending" between attempts
    pending_updates = [(i, s) for i, s in status_updates if s == "pending"]
    assert len(pending_updates) >= 1, (
        f"Expected at least one 'pending' reset, got: {status_updates}"
    )
