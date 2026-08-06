"""Phase step functions — building blocks for the phase pipeline.

Each step wraps an existing check/loop and returns a ``StepResult``.
Steps are composed into per-phase pipelines by ``phase_pipeline.py``.
"""

from __future__ import annotations

import logging
from typing import Any

from typing_extensions import TypedDict

from backend.quality.micro_repair import apply_micro_repair_batches
from backend.server.forge_logger import forge_logger

logger = logging.getLogger(__name__)


class StepResult(TypedDict):
    """Outcome of a single pipeline step."""

    step_name: str
    deletions: int


# ── Step: structural gap resolution ──────────────────────────────────────────


async def structural(flow: Any, phase: int) -> StepResult:
    """Run the StructuralLoopGraph to close structural gaps for this phase."""
    forge_logger.emit("INFO", "PIPE ", f"Phase {phase} · step: structural")
    await flow._run_structural_loop(phase, skip_approval=True)
    # Structural step adds nodes, doesn't typically delete
    return StepResult(step_name="structural", deletions=0)


# ── Step: combined quality check (req + title axes in one batched LLM call) ──


async def combined_quality(flow: Any, phase: int) -> StepResult:
    """One LLM call judges every authored node for atomicity + EARS + title
    match + title specificity, then dispatches fixes.

    Replaces the former per-node ``req_quality`` and ``title_quality`` steps.
    ``DispatchQuotaError`` propagates — quota exhaustion halts the run loudly
    instead of dropping the remaining fixes and completing the step.
    """
    from backend.analysis.gaps import GapType

    forge_logger.emit("INFO", "PIPE ", f"Phase {phase} · step: combined_quality")
    gaps = await flow.run_combined_quality_check(phase, _broadcast_status=False)
    if not gaps:
        return StepResult(step_name="combined_quality", deletions=0)

    # NON_ATOMIC first so requirement splits land before title retitles.
    gaps.sort(key=lambda g: 0 if g.type == GapType.NON_ATOMIC_REQUIREMENT else 1)
    # Batched micro-repair pre-pass (specs/12 §7.4): N>=3 same-family
    # title/wording gaps are fixed in one structured LLM call; only gaps it
    # could not certify-resolve continue to per-gap dispatch below.
    gaps = await apply_micro_repair_batches(flow, gaps)
    for gap in gaps:
        node = flow.graph.node_sync(gap.node_id)
        if node is None:
            continue
        await flow._dispatch(gap)

    return StepResult(step_name="combined_quality", deletions=0)


# ── Step: quality gap stability loop ─────────────────────────────────────────


async def quality_gaps(flow: Any, phase: int) -> StepResult:
    """Run the QualCheckGraph stability loop for quality gaps."""
    forge_logger.emit("INFO", "PIPE ", f"Phase {phase} · step: quality_gaps")
    await flow.run_qual_check(phase, _broadcast_status=False)
    return StepResult(step_name="quality_gaps", deletions=0)


# ── Step: semantic duplicate detection ───────────────────────────────────────


async def semantic(flow: Any, phase: int) -> StepResult:
    """Detect and remove semantic duplicate nodes.

    If a batch step recorded newly-created node IDs on ``flow._batch_new_node_ids``,
    only those nodes are candidates for deletion. This prevents deleting
    established nodes whose removal would uncover their parent PARA/HLR
    (the create-delete-recreate loop).
    """
    forge_logger.emit("INFO", "PIPE ", f"Phase {phase} · step: semantic")
    only_ids = getattr(flow, "_batch_new_node_ids", None)
    if only_ids is not None:
        forge_logger.emit(
            "INFO", "PIPE ", f"Phase {phase} · semantic restricted to {len(only_ids)} new node(s)"
        )
    deleted = await flow.run_semantic_check(
        phase,
        _broadcast_status=False,
        only_node_ids=only_ids,
    )
    # Clear after use so next cycle (if any) doesn't carry stale IDs
    flow._batch_new_node_ids = None
    return StepResult(step_name="semantic", deletions=deleted)


# ── Step: design consolidation (Phase 8) ─────────────────────────────────────


async def design_consolidation(flow: Any, phase: int) -> StepResult:
    """Merge DESIGN sprawl within each MODULE."""
    forge_logger.emit("INFO", "PIPE ", f"Phase {phase} · step: design_consolidation")
    deleted = await flow.run_design_consolidation(_broadcast_status=False)
    return StepResult(step_name="design_consolidation", deletions=deleted)


# ── Step: case trace coverage (Phase 10) ─────────────────────────────────────


async def case_trace_coverage(flow: Any, phase: int) -> StepResult:
    """Verify CASE↔requirement coverage and remove bad traces.

    On the first cycle, checks all CASEs.  On subsequent cycles (after
    deletions caused new CASEs to be created), only checks CASEs that
    were created since the last run — identified by tracking the highest
    CASE node ID before/after the structural step.
    """
    forge_logger.emit(
        "INFO",
        "PIPE ",
        f"Phase {phase} · step: case_trace_coverage",
    )

    from backend.agents.factory import build_llm  # noqa: PLC0415
    from backend.quality.case_trace_check import create_case_trace_checker  # noqa: PLC0415

    # Determine which CASEs are new since the last trace check
    only_ids: set[str] | None = getattr(flow, "_last_checked_case_ids", None)
    all_case_ids = {
        n.node_id for n in flow.graph.all_nodes() if n.node_type in ("CASE_HLR", "CASE_LLR")
    }

    if not all_case_ids:
        # Nothing to verify — don't build an LLM checker for zero cases.
        forge_logger.emit(
            "INFO", "PIPE ",
            f"Phase {phase} · case_trace_coverage: no CASE nodes — nothing to check",
        )
        flow._last_checked_case_ids = all_case_ids
        return StepResult(step_name="case_trace_coverage", deletions=0)

    if only_ids is not None:
        new_ids = all_case_ids - only_ids
        check_ids: set[str] | None = new_ids if new_ids else None
    else:
        check_ids = None  # first run — check everything

    checker = create_case_trace_checker(build_llm(flow.config, cacheable=True), flow.graph)
    removed = await checker(only_ids=check_ids)

    # Remember what we've checked for next cycle
    flow._last_checked_case_ids = all_case_ids

    return StepResult(step_name="case_trace_coverage", deletions=removed)
