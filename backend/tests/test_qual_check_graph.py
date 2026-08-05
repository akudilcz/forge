"""Tests for the QualCheckGraph stability loop (backend/crew/qual_check_graph.py)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.crew.dispatch import DispatchQuotaError
from backend.crew.flow import ForgeFlow
from backend.crew.qual_check_graph import (
    _MAX_QUAL_PASSES,
    _count_nodes,
    create_qual_check_graph,
)
from backend.quality.checks import PHASE_TO_NODE_TYPES

# (pool, graph, config, broadcaster, phase_store)
MockDeps = tuple[MagicMock, MagicMock, MagicMock, MagicMock, MagicMock]

MODULE_TYPE = PHASE_TO_NODE_TYPES[5][0]

# ── Fixtures / helpers ────────────────────────────────────────────────────────


@pytest.fixture
def mock_deps(tmp_path: Path) -> MockDeps:
    pool = MagicMock()
    graph = MagicMock()
    graph.all_nodes.return_value = []
    graph.node_sync.return_value = None
    config = MagicMock()
    config.project.workspace_dir = str(tmp_path)
    broadcaster = MagicMock()
    phase_store = MagicMock()
    phase_store.get_all.return_value = []
    return pool, graph, config, broadcaster, phase_store


@pytest.fixture
def flow(mock_deps: MockDeps) -> ForgeFlow:
    pool, graph, config, broadcaster, phase_store = mock_deps
    return ForgeFlow(pool, graph, config, broadcaster, phase_store)


def _initial_state(phase: int, node_types: list[str]) -> dict[str, Any]:
    """Mirror the initial state that ForgeFlow.run_qual_check passes to ainvoke."""
    return {
        "phase": phase,
        "node_types": node_types,
        "pass_num": 0,
        "pending_gaps": [],
        "count_before": 0,
        "total_checked": 0,
        "had_deletions": False,
        "pass_had_deletions": False,
    }


def _node(node_id: str, node_type: str) -> MagicMock:
    node = MagicMock()
    node.node_id = node_id
    node.node_type = node_type
    return node



def _gap(node_id: str) -> Gap:
    return Gap(
        type=GapType.EMPTY_CONTENT,
        priority=GapPriority.MAINTENANCE,
        node_id=node_id,
        description="empty",
    )


def _make_dispatch_capture() -> tuple[list[Gap], AsyncMock]:
    dispatched: list[Gap] = []

    async def _capture(gap: Gap, attempt: int = 1, **kwargs: Any) -> str:
        dispatched.append(gap)
        return ""

    return dispatched, AsyncMock(side_effect=_capture)


# ── _count_nodes ──────────────────────────────────────────────────────────────


def test_count_nodes_counts_matching_types(flow: ForgeFlow, mock_deps: MockDeps) -> None:
    """_count_nodes counts only nodes whose type is in the given set."""
    mock_deps[1].all_nodes.return_value = [
        _node("mod.a", "MODULE"),
        _node("mod.b", "MODULE"),
        _node("con.a", "CONTRACT"),
    ]
    assert _count_nodes(flow, {"MODULE"}) == 2
    assert _count_nodes(flow, {"MODULE", "CONTRACT"}) == 3


def test_count_nodes_empty_type_set_is_zero(flow: ForgeFlow, mock_deps: MockDeps) -> None:
    """An empty type set matches nothing."""
    mock_deps[1].all_nodes.return_value = [_node("mod.a", "MODULE")]
    assert _count_nodes(flow, set()) == 0


# ── Stability loop ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_gaps_finalizes_after_single_pass(
    flow: ForgeFlow, mock_deps: MockDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no quality gaps the graph finalizes after one scan, dispatching nothing."""
    mock_deps[1].all_nodes.return_value = [
        _node("mod.a", MODULE_TYPE),
        _node("mod.b", MODULE_TYPE),
    ]
    monkeypatch.setattr(flow, "_quality_gaps_for_types", lambda nt: {})
    dispatched, capture = _make_dispatch_capture()
    monkeypatch.setattr(flow, "_dispatch", capture)

    result = await create_qual_check_graph(flow).ainvoke(
        _initial_state(5, [MODULE_TYPE])
    )

    assert result["pass_num"] == 1
    assert result["total_checked"] == 0
    assert result["had_deletions"] is False
    assert dispatched == []
    mock_deps[4].set_status.assert_not_called()


@pytest.mark.asyncio
async def test_stable_pass_no_deletions_exits(
    flow: ForgeFlow, mock_deps: MockDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pass that dispatches gaps but deletes nothing is stable — no second pass."""
    nodes = [_node("mod.a", MODULE_TYPE), _node("mod.b", MODULE_TYPE)]
    mock_deps[1].all_nodes.return_value = nodes
    mock_deps[1].node_sync.return_value = MagicMock()
    monkeypatch.setattr(
        flow,
        "_quality_gaps_for_types",
        lambda nt: {"mod.a": [_gap("mod.a")], "mod.b": [_gap("mod.b")]},
    )
    monkeypatch.setattr(flow._analyser, "analyse", lambda g: [])
    dispatched, capture = _make_dispatch_capture()
    monkeypatch.setattr(flow, "_dispatch", capture)

    result = await create_qual_check_graph(flow).ainvoke(
        _initial_state(5, [MODULE_TYPE])
    )

    assert result["pass_num"] == 1
    assert result["total_checked"] == 2
    assert result["had_deletions"] is False
    assert result["pass_had_deletions"] is False
    assert [g.node_id for g in dispatched] == ["mod.a", "mod.b"]
    mock_deps[4].set_status.assert_not_called()


@pytest.mark.asyncio
async def test_pass_with_deletions_triggers_second_pass(
    flow: ForgeFlow, mock_deps: MockDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A node deleted during a pass triggers a re-scan; the stable pass then exits."""
    node_a = _node("mod.a", MODULE_TYPE)
    node_b = _node("mod.b", MODULE_TYPE)
    node_c = _node("mod.c", MODULE_TYPE)

    call_num = [0]

    def all_nodes_side() -> list[MagicMock]:
        call_num[0] += 1
        # Pass 1 scan sees 3 nodes; every later look sees 2 (mod.b deleted).
        if call_num[0] == 1:
            return [node_a, node_b, node_c]
        return [node_a, node_c]

    mock_deps[1].all_nodes.side_effect = all_nodes_side
    mock_deps[1].node_sync.return_value = MagicMock()
    monkeypatch.setattr(
        flow,
        "_quality_gaps_for_types",
        lambda nt: {nid: [_gap(nid)] for nid in ["mod.a", "mod.b", "mod.c"]},
    )
    monkeypatch.setattr(flow._analyser, "analyse", lambda g: [])
    dispatched, capture = _make_dispatch_capture()
    monkeypatch.setattr(flow, "_dispatch", capture)

    result = await create_qual_check_graph(flow).ainvoke(
        _initial_state(5, [MODULE_TYPE])
    )

    assert result["pass_num"] == 2
    assert result["total_checked"] == 5
    assert result["had_deletions"] is True
    assert result["pass_had_deletions"] is False  # final pass was stable
    node_ids = [g.node_id for g in dispatched]
    assert node_ids.count("mod.a") == 2
    assert node_ids.count("mod.b") == 1
    assert node_ids.count("mod.c") == 2
    # Only the owner phase of the deleted node type is reset.
    reset_calls = {c.args for c in mock_deps[4].set_status.call_args_list}
    assert (5, "pending") in reset_calls
    assert (6, "pending") not in reset_calls


@pytest.mark.asyncio
async def test_deleted_nodes_mid_pass_not_dispatched(
    flow: ForgeFlow, mock_deps: MockDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A gap whose node vanished mid-pass is skipped and not counted."""
    mock_deps[1].all_nodes.return_value = [
        _node("mod.a", MODULE_TYPE),
        _node("mod.b", MODULE_TYPE),
    ]
    mock_deps[1].node_sync.side_effect = (
        lambda nid: None if nid == "mod.b" else MagicMock()
    )
    monkeypatch.setattr(
        flow,
        "_quality_gaps_for_types",
        lambda nt: {"mod.a": [_gap("mod.a")], "mod.b": [_gap("mod.b")]},
    )
    monkeypatch.setattr(flow._analyser, "analyse", lambda g: [])
    dispatched, capture = _make_dispatch_capture()
    monkeypatch.setattr(flow, "_dispatch", capture)

    result = await create_qual_check_graph(flow).ainvoke(
        _initial_state(5, [MODULE_TYPE])
    )

    assert result["total_checked"] == 1
    assert [g.node_id for g in dispatched] == ["mod.a"]


@pytest.mark.asyncio
async def test_max_passes_cap_forces_finalize(
    flow: ForgeFlow, mock_deps: MockDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Continuous deletions stop at _MAX_QUAL_PASSES rather than looping forever."""
    pool = [_node(f"mod.{i}", MODULE_TYPE) for i in range(6)]
    # Call order per pass: scan_gaps then assess_stability. Sizes shrink at each
    # assess so every pass registers deletions.
    sizes = iter([6, 5, 5, 4, 4, 3, 3, 2, 2, 1])
    mock_deps[1].all_nodes.side_effect = lambda: pool[: next(sizes)]
    mock_deps[1].node_sync.return_value = MagicMock()
    # Only mod.0 is gapped, keeping dispatch volume within LangGraph's
    # default recursion limit.
    monkeypatch.setattr(
        flow, "_quality_gaps_for_types", lambda nt: {"mod.0": [_gap("mod.0")]}
    )
    monkeypatch.setattr(flow._analyser, "analyse", lambda g: [])
    dispatched, capture = _make_dispatch_capture()
    monkeypatch.setattr(flow, "_dispatch", capture)

    result = await create_qual_check_graph(flow).ainvoke(
        _initial_state(5, [MODULE_TYPE])
    )

    assert result["pass_num"] == _MAX_QUAL_PASSES
    assert result["had_deletions"] is True
    assert result["pass_had_deletions"] is True  # still unstable at forced exit
    assert result["total_checked"] == _MAX_QUAL_PASSES
    reset_calls = {c.args for c in mock_deps[4].set_status.call_args_list}
    assert (5, "pending") in reset_calls


@pytest.mark.asyncio
async def test_dispatch_quota_error_propagates(
    flow: ForgeFlow, mock_deps: MockDeps, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DispatchQuotaError propagates out of the qual-check graph.

    Quota exhaustion mid-loop must halt the run loudly — never route to
    finalize and log 'qual check complete' with the remaining gaps dropped.
    """
    mock_deps[1].all_nodes.return_value = [
        _node("mod.a", MODULE_TYPE),
        _node("mod.b", MODULE_TYPE),
    ]
    mock_deps[1].node_sync.return_value = MagicMock()
    monkeypatch.setattr(
        flow,
        "_quality_gaps_for_types",
        lambda nt: {"mod.a": [_gap("mod.a")], "mod.b": [_gap("mod.b")]},
    )
    monkeypatch.setattr(flow._analyser, "analyse", lambda g: [])
    quota_dispatch = AsyncMock(side_effect=DispatchQuotaError("quota exhausted"))
    monkeypatch.setattr(flow, "_dispatch", quota_dispatch)

    with pytest.raises(DispatchQuotaError):
        await create_qual_check_graph(flow).ainvoke(_initial_state(5, [MODULE_TYPE]))

    quota_dispatch.assert_awaited_once()  # halts before the second gap
