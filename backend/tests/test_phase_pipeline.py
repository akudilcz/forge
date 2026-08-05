"""Tests for the phase pipeline runner."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.crew.dispatch import DispatchQuotaError
from backend.crew.phase_pipeline import (
    _DEFAULT_STEPS,
    get_steps,
    run_phase_pipeline,
)
from backend.crew.phase_steps import StepResult

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_flow(tmp_path: Path) -> MagicMock:
    flow = MagicMock()
    flow.broadcaster = MagicMock()
    flow._request_approval = AsyncMock()
    flow._broadcast_loop_status = MagicMock()
    flow._run_phase = AsyncMock()
    flow.run_qual_check = AsyncMock(return_value=0)
    flow.run_semantic_check = AsyncMock(return_value=0)
    flow.run_design_consolidation = AsyncMock(return_value=0)
    flow.graph = MagicMock()
    flow.graph.all_nodes.return_value = []
    flow.graph.node_sync.return_value = None
    flow._graph_state_count = MagicMock(return_value=0)
    flow.config = MagicMock()
    return flow


# ── get_steps ─────────────────────────────────────────────────────────────────


def test_get_steps_default_for_unknown_phase() -> None:
    """Phases without custom pipelines get the default step list."""
    steps = get_steps(2)
    assert len(steps) == len(_DEFAULT_STEPS)
    assert [s.__name__ for s in steps] == [s.__name__ for s in _DEFAULT_STEPS]


def test_get_steps_custom_for_phase_10() -> None:
    """Phase 10 batches CASE authoring and runs case_trace_coverage after."""
    steps = get_steps(10)
    names = [s.__name__ for s in steps]
    assert "batch_phase10" in names
    assert "case_trace_coverage" in names


def test_get_steps_custom_for_phase_8() -> None:
    """Phase 8 uses batch_phase8 + design_consolidation."""
    steps = get_steps(8)
    names = [s.__name__ for s in steps]
    assert "batch_phase8" in names
    assert "design_consolidation" in names


def test_get_steps_custom_for_phase_3() -> None:
    """Phase 3 uses batch_phase3 + combined_quality."""
    steps = get_steps(3)
    names = [s.__name__ for s in steps]
    assert "batch_phase3" in names
    assert "combined_quality" in names


def test_get_steps_batch_for_phase_5() -> None:
    """Phase 5 uses batch_phase5."""
    steps = get_steps(5)
    names = [s.__name__ for s in steps]
    assert "batch_phase5" in names
    assert "structural" not in names


def test_get_steps_batch_for_phase_7() -> None:
    """Phase 7 uses batch_phase7 + combined_quality."""
    steps = get_steps(7)
    names = [s.__name__ for s in steps]
    assert "batch_phase7" in names
    assert "combined_quality" in names


def test_get_steps_returns_copy() -> None:
    """Modifying the returned list doesn't affect the registry."""
    async def _noop_step(flow: Any, phase: int) -> StepResult:
        return StepResult(step_name="noop", deletions=0)

    steps = get_steps(2)
    steps.append(_noop_step)
    assert len(get_steps(2)) == len(_DEFAULT_STEPS)


# ── run_phase_pipeline ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_runs_all_steps(mock_flow: MagicMock) -> None:
    """Pipeline runs each step and finalizes."""

    async def stub_step(flow: Any, phase: int) -> StepResult:
        return StepResult(step_name="test", deletions=0)

    with patch(
        "backend.crew.phase_pipeline.get_steps",
        return_value=[stub_step, stub_step],
    ):
        result = await run_phase_pipeline(mock_flow, 5)

    assert result["phase"] == 5
    assert result["total_deletions"] == 0
    mock_flow._request_approval.assert_awaited_once_with(5)


@pytest.mark.asyncio
async def test_pipeline_cycles_on_deletions(mock_flow: MagicMock) -> None:
    """Pipeline loops back when a step reports deletions."""
    call_count = [0]

    async def deletion_step(flow: Any, phase: int) -> StepResult:
        call_count[0] += 1
        # Report deletions only on first cycle
        deletions = 1 if call_count[0] == 1 else 0
        return StepResult(step_name="deleter", deletions=deletions)

    with patch(
        "backend.crew.phase_pipeline.get_steps",
        return_value=[deletion_step],
    ):
        result = await run_phase_pipeline(mock_flow, 5)

    assert result["cycles"] == 2
    assert result["total_deletions"] == 1
    assert call_count[0] == 2


@pytest.mark.asyncio
async def test_pipeline_stable_on_first_cycle(mock_flow: MagicMock) -> None:
    """Pipeline exits after one cycle when no deletions occur."""

    async def no_deletions(flow: Any, phase: int) -> StepResult:
        return StepResult(step_name="stable", deletions=0)

    with patch(
        "backend.crew.phase_pipeline.get_steps",
        return_value=[no_deletions],
    ):
        result = await run_phase_pipeline(mock_flow, 5)

    assert result["cycles"] == 1
    assert result["total_deletions"] == 0


@pytest.mark.asyncio
async def test_pipeline_forces_exit_at_max_cycles(mock_flow: MagicMock) -> None:
    """A step that always reports deletions stops after _max_cycles (12), not forever."""
    call_count = [0]

    async def always_deleting(flow: Any, phase: int) -> StepResult:
        call_count[0] += 1
        return StepResult(step_name="deleter", deletions=1)

    with patch(
        "backend.crew.phase_pipeline.get_steps",
        return_value=[always_deleting],
    ):
        result = await run_phase_pipeline(mock_flow, 5)

    assert result["cycles"] == 12
    assert call_count[0] == 12
    assert result["total_deletions"] == 12
    # Even a forced exit still finalizes via the approval audit.
    mock_flow._request_approval.assert_awaited_once_with(5)


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    reason=(
        "BUG: the per-step `except Exception` in run_phase_pipeline swallows "
        "DispatchQuotaError along with every other step failure, substituting an "
        "empty StepResult and continuing. Quota exhaustion should abort the "
        "pipeline (CLAUDE.md: no silent fallbacks). Remove this marker once the "
        "pipeline re-raises DispatchQuotaError."
    ),
)
async def test_pipeline_propagates_dispatch_quota_error(mock_flow: MagicMock) -> None:
    """DispatchQuotaError raised by a step must propagate out of run_phase_pipeline."""

    async def quota_step(flow: Any, phase: int) -> StepResult:
        raise DispatchQuotaError("API quota exhausted")

    with patch(
        "backend.crew.phase_pipeline.get_steps",
        return_value=[quota_step],
    ):
        with pytest.raises(DispatchQuotaError):
            await run_phase_pipeline(mock_flow, 5)
