"""Phase pipeline runner — sequential step execution per phase.

Replaces the monolithic ``PhaseLifecycleGraph`` with a composable, per-phase
step registry. Each phase maps to an ordered list of step functions from
``phase_steps.py``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from backend.pipeline.batch_steps import (
    batch_phase3,
    batch_phase7,
    batch_phase10,
)
from backend.pipeline.steps import (
    StepResult,
    case_trace_coverage,
    combined_quality,
    design_consolidation,
    deterministic_parse,
    evidence_integrity,
    oracle_check,
    quality_gaps,
    semantic,
    structural,
    suite_authoring,
)
from backend.server.forge_logger import forge_logger
from backend.workspace.result_recorder import record_results_step
from backend.workspace.sync import workspace_sync

logger = logging.getLogger(__name__)

StepFn = Callable[[Any, int], Awaitable[StepResult]]

# Default steps for phases without a custom pipeline.
_DEFAULT_STEPS: list[StepFn] = [structural, quality_gaps, combined_quality, semantic]

# Per-phase step overrides.
# Phases 3, 7 and 10 use batch authoring (one prompt writes multiple nodes).
# ``combined_quality`` is a single LLM call that judges every authored node
# on atomicity + EARS + title match + title specificity in one shot, replacing
# the former per-node req_quality and title_quality passes.
#
# Phase 5 deliberately has NO entry (U7, specs/03 Phase 5): HLR→MODULE
# allocation is authored in phase 4 as part of architecture creation, so
# phase 5 runs the default pipeline — the ``structural`` step is the
# deterministic every-HLR-lands verification (the UNMODULARISED analyser
# gap) plus per-gap dispatch for residual unassigned HLRs only. Fusing at
# the registry level preserves the phase identity: phase_store rows, resume,
# and completion criteria still key on phase number 5, and a resumed
# old-shape graph (MODULEs present, HLRs unassigned) completes through the
# same residual route.
#
# Phase 8 follows the same shape (U8, specs/03 Phases 7-8): DESIGNs are
# authored in phase 7's fused implementable-spec pass (LLR + DESIGN per
# MODULE, one quality/semantic boundary for both node types), so phase 8 is
# verification-only — design_consolidation merges DESIGN sprawl, then the
# default verification pipeline dispatches residual UNDESIGNED gaps per
# gap. Phase number 8 and its completion criterion (no UNDESIGNED) are
# preserved for phase_store/resume/auditor, and a resumed old-shape graph
# (LLRs authored, DESIGNs missing) completes through the residual route.
PHASE_STEPS: dict[int, list[StepFn]] = {
    # Phase 2: deterministic markdown → PARA split first (zero LLM calls for
    # qualifying documents); the structural loop remains the LLM chunking
    # route for documents without markdown structure (specs/03 §Phase 2).
    2: [deterministic_parse, structural, quality_gaps, combined_quality, semantic],
    3: [batch_phase3, quality_gaps, combined_quality, semantic],
    7: [batch_phase7, quality_gaps, combined_quality, semantic],
    8: [design_consolidation, structural, quality_gaps, combined_quality, semantic],
    # Phase 9 is merged into phase 10 at the registry level (U9, specs/03
    # Phases 9-10): nothing downstream ever *executes* SUITE prose — the
    # SUITE is structured input to case authoring — so phase 9 keeps only
    # the per-gap dispatch of its single UNSUITED gap. Its quality/semantic
    # boundary is phase 10's (SUITE is a phase-10 node type in phase_map).
    # Phase number 9 and its completion criterion (no UNSUITED) are
    # preserved for phase_store/resume/auditor.
    9: [structural],
    # Phase 10 opens with the suite_authoring guard (a resumed graph missing
    # its SUITE authors it before any CASE exists), batches CASE authoring
    # with the SUITE in the static prefix, then gates completion on the
    # independent CASE-oracle validation (specs/13 §Oracle validation).
    10: [
        suite_authoring,
        batch_phase10,
        quality_gaps,
        combined_quality,
        semantic,
        case_trace_coverage,
        oracle_check,
    ],
    # RESULT recording runs after sync so every RESULT has a TEST parent
    # (specs/03); the step also heals misparented RESULTs on resume. The
    # evidence_integrity gate runs last: recorded evidence is only accepted
    # once it is shown to be per-function, plausible, and well-formed
    # (specs/13 §Evidence integrity).
    13: [workspace_sync, record_results_step, evidence_integrity],
}


def get_steps(phase: int) -> list[StepFn]:
    """Return the ordered step list for a phase."""
    return PHASE_STEPS.get(phase, list(_DEFAULT_STEPS))


async def run_phase_pipeline(flow: Any, phase: int) -> dict[str, int]:
    """Execute the phase pipeline: run steps in order, cycle if deletions.

    Returns a summary dict with cycle count and total deletions.

    Failure semantics — the pipeline never fails open. Any step exception
    marks the phase ``awaiting_approval`` and re-raises; ``DispatchQuotaError``
    included, so quota exhaustion halts the run loudly. ``_SingleStepDone``
    is control flow, not a failure: it propagates untouched.
    """
    steps = get_steps(phase)
    step_names = [s.__name__ for s in steps]
    flow._set_phase_status(phase, "active")
    forge_logger.phase_start(phase)
    forge_logger.emit(
        "INFO",
        "PIPE ",
        f"Phase {phase} pipeline: {step_names}",
    )

    from backend.observability import log_context  # noqa: PLC0415
    from backend.pipeline.flow import _SingleStepDone  # noqa: PLC0415 — circular at module level

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
                except _SingleStepDone:
                    raise
                except Exception as exc:
                    # No fail-open: a failed step is not a passed step. Mark
                    # the phase as needing attention and halt loudly instead
                    # of substituting a vacuous StepResult and completing the
                    # phase on an unverified graph.
                    forge_logger.emit(
                        "ERROR",
                        "PIPE ",
                        f"Phase {phase} · {step.__name__} FAILED: "
                        f"{type(exc).__name__}: {exc}",
                        error_type=type(exc).__name__,
                    )
                    flow._set_phase_status(phase, "awaiting_approval")
                    raise
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
