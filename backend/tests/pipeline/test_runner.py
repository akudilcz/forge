"""Tests for the phase pipeline runner."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.pipeline.dispatch import DispatchQuotaError
from backend.pipeline.runner import (
    _DEFAULT_STEPS,
    get_steps,
    run_phase_pipeline,
)
from backend.pipeline.steps import StepResult

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
    steps = get_steps(4)
    assert len(steps) == len(_DEFAULT_STEPS)
    assert [s.__name__ for s in steps] == [s.__name__ for s in _DEFAULT_STEPS]


def test_get_steps_phase_2_deterministic_parse_first() -> None:
    """Phase 2 runs the deterministic markdown split before any agent work;
    the structural loop remains as the LLM chunking route (specs/03 §Phase 2)."""
    names = [s.__name__ for s in get_steps(2)]
    assert names[0] == "deterministic_parse"
    assert "structural" in names


def test_get_steps_custom_for_phase_10() -> None:
    """Phase 10 batches CASE authoring and runs case_trace_coverage after."""
    steps = get_steps(10)
    names = [s.__name__ for s in steps]
    assert "batch_phase10" in names
    assert "case_trace_coverage" in names


def test_get_steps_phase_8_verification_only() -> None:
    """U8: DESIGN authoring is fused into phase 7's implementable-spec pass,
    so phase 8 has NO batch authoring step. It runs design_consolidation
    plus the default verification pipeline — ``structural`` is the residual
    per-gap dispatch route for leftover UNDESIGNED gaps (specs/03 Phase 8)."""
    names = [s.__name__ for s in get_steps(8)]
    assert "batch_phase8" not in names
    assert names == [
        "design_consolidation",
        "structural",
        "quality_gaps",
        "combined_quality",
        "semantic",
    ]


def test_get_steps_custom_for_phase_3() -> None:
    """Phase 3 uses batch_phase3 + combined_quality."""
    steps = get_steps(3)
    names = [s.__name__ for s in steps]
    assert "batch_phase3" in names
    assert "combined_quality" in names


def test_get_steps_phase_5_verification_residual_only() -> None:
    """U7: allocation is a phase-4 authoring output, so phase 5 has NO batch
    authoring step. It runs the default pipeline: the structural step is the
    deterministic every-HLR-lands check (UNMODULARISED analyser gap) plus
    per-gap dispatch for residual unassigned HLRs only, followed by the
    usual quality/semantic steps (specs/03 Phase 5)."""
    steps = get_steps(5)
    names = [s.__name__ for s in steps]
    assert "batch_phase5" not in names
    assert names[0] == "structural"
    assert names == [s.__name__ for s in _DEFAULT_STEPS]


def test_get_steps_phase_13_records_results_after_sync() -> None:
    """Phase 13 records RESULT nodes only after workspace sync creates TESTs.

    Regression: RESULTs recorded in phase 12 (before TEST nodes existed)
    were parented to CASE nodes — 230 ORPHAN_NODE gaps in a live build.
    """
    names = [s.__name__ for s in get_steps(13)]
    assert names == ["workspace_sync", "record_results_step"]


def test_get_steps_batch_for_phase_7() -> None:
    """Phase 7 uses the fused batch_phase7 (LLR + DESIGN authoring, U8) with
    one quality/semantic boundary covering both artifact types."""
    steps = get_steps(7)
    names = [s.__name__ for s in steps]
    assert "batch_phase7" in names
    assert "combined_quality" in names
    assert "semantic" in names


def test_get_steps_returns_copy() -> None:
    """Modifying the returned list doesn't affect the registry."""
    async def _noop_step(flow: Any, phase: int) -> StepResult:
        return StepResult(step_name="noop", deletions=0)

    steps = get_steps(4)
    steps.append(_noop_step)
    assert len(get_steps(4)) == len(_DEFAULT_STEPS)


# ── run_phase_pipeline ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_runs_all_steps(mock_flow: MagicMock) -> None:
    """Pipeline runs each step and finalizes."""

    async def stub_step(flow: Any, phase: int) -> StepResult:
        return StepResult(step_name="test", deletions=0)

    with patch(
        "backend.pipeline.runner.get_steps",
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
        "backend.pipeline.runner.get_steps",
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
        "backend.pipeline.runner.get_steps",
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
        "backend.pipeline.runner.get_steps",
        return_value=[always_deleting],
    ):
        result = await run_phase_pipeline(mock_flow, 5)

    assert result["cycles"] == 12
    assert call_count[0] == 12
    assert result["total_deletions"] == 12
    # Even a forced exit still finalizes via the approval audit.
    mock_flow._request_approval.assert_awaited_once_with(5)


async def test_pipeline_marks_phase_active_on_start(mock_flow: MagicMock) -> None:
    """Pipeline marks the phase active before running any step."""

    async def no_deletions(flow: Any, phase: int) -> StepResult:
        return StepResult(step_name="stable", deletions=0)

    with patch(
        "backend.pipeline.runner.get_steps",
        return_value=[no_deletions],
    ):
        await run_phase_pipeline(mock_flow, 5)

    mock_flow._set_phase_status.assert_any_call(5, "active")


# ── Failure semantics: no fail-open ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_pipeline_propagates_dispatch_quota_error(mock_flow: MagicMock) -> None:
    """DispatchQuotaError from a step halts the pipeline loudly.

    Quota exhaustion must never be converted into a vacuous 'step done
    (deletions=0)' result that lets the phase finish and report complete.
    """
    async def quota_step(flow: Any, phase: int) -> StepResult:
        raise DispatchQuotaError("quota exhausted")

    with patch(
        "backend.pipeline.runner.get_steps",
        return_value=[quota_step],
    ):
        with pytest.raises(DispatchQuotaError):
            await run_phase_pipeline(mock_flow, 5)

    # The phase never reaches finalization/approval.
    mock_flow._request_approval.assert_not_awaited()
    mock_flow._set_phase_status.assert_any_call(5, "awaiting_approval")


@pytest.mark.asyncio
async def test_pipeline_step_exception_fails_phase_loudly(mock_flow: MagicMock) -> None:
    """Any step exception marks the phase awaiting_approval and re-raises.

    Previously the failure was logged and replaced with StepResult(deletions=0),
    indistinguishable from a clean pass — the fail-open bug.
    """

    async def broken_step(flow: Any, phase: int) -> StepResult:
        raise RuntimeError("LLM call failed")

    async def later_step(flow: Any, phase: int) -> StepResult:  # pragma: no cover
        raise AssertionError("steps after a failed step must not run")

    with patch(
        "backend.pipeline.runner.get_steps",
        return_value=[broken_step, later_step],
    ):
        with pytest.raises(RuntimeError, match="LLM call failed"):
            await run_phase_pipeline(mock_flow, 5)

    mock_flow._set_phase_status.assert_any_call(5, "awaiting_approval")
    mock_flow._request_approval.assert_not_awaited()


@pytest.mark.asyncio
async def test_pipeline_single_step_done_propagates_without_failure(
    mock_flow: MagicMock,
) -> None:
    """_SingleStepDone is control flow, not a failure — it propagates without
    marking the phase awaiting_approval."""
    from backend.pipeline.flow import _SingleStepDone

    async def single_step(flow: Any, phase: int) -> StepResult:
        raise _SingleStepDone()

    with patch(
        "backend.pipeline.runner.get_steps",
        return_value=[single_step],
    ):
        with pytest.raises(_SingleStepDone):
            await run_phase_pipeline(mock_flow, 5)

    statuses = [c.args for c in mock_flow._set_phase_status.call_args_list]
    assert (5, "awaiting_approval") not in statuses
