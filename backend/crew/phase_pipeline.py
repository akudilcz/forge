"""Phase pipeline runner — sequential step execution per phase.

Replaces the monolithic ``PhaseLifecycleGraph`` with a composable, per-phase
step registry. Each phase maps to an ordered list of step functions from
``phase_steps.py``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from backend.crew.batch_steps import (
    batch_phase3,
    batch_phase5,
    batch_phase7,
    batch_phase8,
    batch_phase10,
)
from backend.crew.phase_steps import (
    StepResult,
    case_trace_coverage,
    combined_quality,
    design_consolidation,
    quality_gaps,
    semantic,
    structural,
)
from backend.crew.workspace_sync import workspace_sync
from backend.server.forge_logger import forge_logger

logger = logging.getLogger(__name__)

StepFn = Callable[[Any, int], Awaitable[StepResult]]

# Default steps for phases without a custom pipeline.
_DEFAULT_STEPS: list[StepFn] = [structural, quality_gaps, combined_quality, semantic]

# Per-phase step overrides.
# Phases 3, 5, 7, 8 use batch authoring (one prompt writes multiple nodes).
# ``combined_quality`` is a single LLM call that judges every authored node
# on atomicity + EARS + title match + title specificity in one shot, replacing
# the former per-node req_quality and title_quality passes.
PHASE_STEPS: dict[int, list[StepFn]] = {
    3: [batch_phase3, quality_gaps, combined_quality, semantic],
    5: [batch_phase5, quality_gaps, combined_quality, semantic],
    7: [batch_phase7, quality_gaps, combined_quality, semantic],
    8: [batch_phase8, quality_gaps, combined_quality, semantic, design_consolidation],
    10: [batch_phase10, quality_gaps, combined_quality, semantic, case_trace_coverage],
    13: [workspace_sync],
}


def get_steps(phase: int) -> list[StepFn]:
    """Return the ordered step list for a phase."""
    return PHASE_STEPS.get(phase, list(_DEFAULT_STEPS))


async def run_phase_pipeline(flow: Any, phase: int) -> dict[str, int]:
    """Execute the phase pipeline: run steps in order, cycle if deletions.

    Returns a summary dict with cycle count and total deletions.
    """
    steps = get_steps(phase)
    step_names = [s.__name__ for s in steps]
    forge_logger.emit(
        "INFO",
        "PIPE ",
        f"Phase {phase} pipeline: {step_names}",
    )

    from backend.observability import log_context  # noqa: PLC0415

    total_deletions = 0
    cycle = 0
    _max_cycles = 12

    while cycle < _max_cycles:
        cycle += 1
        with log_context(cycle=cycle):
            forge_logger.emit(
                "INFO",
                "PIPE ",
                f"Phase {phase} · cycle {cycle}",
                cycle=cycle,
            )
            cycle_deletions = 0

            for step in steps:
                try:
                    result = await step(flow, phase)
                except Exception as exc:  # noqa: BLE001
                    forge_logger.emit(
                        "ERROR",
                        "PIPE ",
                        f"Phase {phase} · {step.__name__} FAILED: "
                        f"{type(exc).__name__}: {exc}",
                        error_type=type(exc).__name__,
                    )
                    result = StepResult(step_name=step.__name__, deletions=0)
                cycle_deletions += result["deletions"]
                forge_logger.emit(
                    "INFO",
                    "PIPE ",
                    f"Phase {phase} · {result['step_name']} done "
                    f"(deletions={result['deletions']})",
                    step=result["step_name"],
                    deletions=result["deletions"],
                )

        total_deletions += cycle_deletions

        if cycle_deletions == 0:
            forge_logger.emit(
                "INFO",
                "PIPE ",
                f"Phase {phase} · stable after cycle {cycle}",
            )
            break
        elif cycle >= _max_cycles:
            forge_logger.emit(
                "WARN",
                "PIPE ",
                f"Phase {phase} · max cycles ({_max_cycles}) reached — forcing exit",
            )
            break
        else:
            forge_logger.emit(
                "INFO",
                "PIPE ",
                f"Phase {phase} · {cycle_deletions} deletion(s) in cycle {cycle} — looping back",
            )

    # Finalize
    await flow._request_approval(phase)

    forge_logger.emit(
        "INFO",
        "PIPE ",
        f"Phase {phase} pipeline complete — {total_deletions} total deletion(s)",
    )
    return {
        "phase": phase,
        "cycles": cycle,
        "total_deletions": total_deletions,
    }
