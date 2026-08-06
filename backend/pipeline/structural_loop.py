"""StructuralLoopGraph — per-phase structural gap resolution as a LangGraph StateGraph.

Topology:

    __start__
        ↓
    collect_gaps ──(no gaps)──────────────────────────────────→ finalize
        ↓ (has gaps)
    dispatch_gap ◀─(more in batch)─┐
        ├── (single_step_done) ─────────────────────────────→ finalize
        ├── (batch exhausted) ──────────────────────────────→ collect_gaps
        └── (more in batch) ───────────────────────────────→ dispatch_gap

Batch optimisation: gaps from a single collect are processed as a batch
without re-scanning the graph between each dispatch.  A full re-scan
(collect_gaps) only happens once the batch is exhausted.

``DispatchQuotaError`` propagates out of the graph — quota exhaustion halts
the run loudly rather than finalizing the phase.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from backend.analysis.gaps import Gap
from backend.core.work_queue import ActionRecord, work_queue
from backend.pipeline.dispatch import DispatchQuotaError
from backend.quality.micro_repair import apply_micro_repair_batches
from backend.server.forge_logger import forge_logger

if TYPE_CHECKING:
    from backend.pipeline.flow import ForgeFlow

logger = logging.getLogger(__name__)

# Broadcast the gap list every N dispatches within a batch (not after every one).
_BROADCAST_EVERY = 5

#: Consecutive no-progress dispatches before a gap is abandoned for this pass.
#:
#: ``gap_fail_counts`` was previously tracked and logged but never consulted, so
#: a gap the agent could not resolve was re-dispatched without limit — one test
#: run logged 3336 consecutive failures against a single gap. Every one of those
#: is a paid LLM call in a real build, and the loop only ever exited on quota
#: exhaustion.
#:
#: Abandoning the gap does not hide it: it stays open, so ``PhaseAuditor`` fails
#: the phase and the operator sees exactly which gap could not be closed. That is
#: the loud failure the "no silent fallbacks" rule asks for, instead of an
#: unbounded spend.
_MAX_GAP_ATTEMPTS = 3


class StructuralLoopState(TypedDict):
    """LangGraph state for the per-phase structural gap-resolution loop.

    ``current_gaps`` holds the active batch; it shrinks as gaps are dispatched.
    A full re-scan (collect_gaps) is triggered only when the batch is empty.
    """

    phase: int
    skip_approval: bool
    iteration: int
    gap_fail_counts: dict[str, int]  # {gap_key: attempt_count}
    current_gaps: list[Gap]  # gaps from the latest collect
    single_step_done: bool  # True when single_step=True and a gap resolved
    abandoned: set[str]  # gap keys given up on this pass — never re-collected


def _gap_already_resolved(flow: ForgeFlow, gap: Any) -> bool:
    """Quick check whether a gap has been resolved since the batch was collected.

    This prevents re-dispatching gaps that were partially resolved by a prior
    attempt (e.g. PARAs added before a transient API error).
    """
    from backend.analysis.gaps import GapType
    from backend.graph.models import NodeType

    if gap.type == GapType.UNCHUNKED_DOCUMENT:
        children = flow.graph.children_sync(gap.node_id)
        return any(c.node_type == NodeType.PARA.value for c in children)
    return False


def _gap_still_open(flow: ForgeFlow, gap: Gap) -> bool:
    """Per-gap resolution certificate (specs/12 §8.3).

    A gap is resolved only when a fresh analyser scan — a cheap in-memory
    pass — no longer reports its exact ``(type, node_id)`` key. This replaces
    the retired global version-sum signal, under which ANY write anywhere in
    the graph counted as progress: no-op re-stamps, wrong-typed nodes, and
    mutations of unrelated nodes all "resolved" gaps they never touched
    (the hostile-agent fake-progress incident).
    """
    return any(
        g.type == gap.type and g.node_id == gap.node_id
        for g in flow._analyser.analyse(flow.graph)
    )


def _find_queue_item(phase: int, node_id: str) -> Any:
    """Find a work queue item by phase and target node_id."""
    for item in work_queue.items_for_phase(phase):
        if item.target == node_id:
            return item
    return None


def _record_action(
    state: StructuralLoopState,
    gap: Gap,
    wq_item: Any,
    resolved: bool,
    attempt: int,
) -> None:
    """Append this dispatch's outcome to the work-queue history.

    ``ActionHistory`` existed with a full API and a Control Station panel, but
    nothing ever called ``record_action`` — so the history was permanently
    empty and the panel permanently blank. The outcome is the resolution
    certificate itself: the dispatched gap either cleared from the analyser
    output (improved) or it did not (no_change).
    """
    work_queue.record_action(
        ActionRecord(
            round=attempt,
            work_item_id=wq_item.id if wq_item else "",
            phase=state["phase"],
            category=gap.type.value,
            files_modified=[],
            tool_calls=0,
            gap_count_before=1,
            gap_count_after=0 if resolved else 1,
            outcome="improved" if resolved else "no_change",
            summary=f"{gap.type.value} on {gap.node_id}",
        )
    )


def create_structural_loop_graph(flow: ForgeFlow) -> Any:
    """Return a compiled LangGraph for the structural gap-resolution loop."""

    async def collect_gaps(state: StructuralLoopState) -> dict[str, Any]:
        """Scan for structural gaps in this phase."""
        # Excluding abandoned gaps is what makes the circuit breaker terminal.
        # Without it the loop is: collect -> abandon -> batch empty -> collect
        # the same gap again -> abandon again, forever.
        skipped: set[str] = set(state.get("abandoned") or set())
        gaps = flow._collect_phase_gaps(state["phase"], skipped)
        if gaps:
            # Batched micro-repair pre-pass (specs/12 §7.4): N>=3 same-family
            # title/wording gaps are fixed in one structured LLM call; only
            # gaps it could not certify-resolve continue to per-gap dispatch.
            gaps = await apply_micro_repair_batches(flow, gaps)
        if gaps:
            gap_summary = ", ".join(f"{g.type.value}:{g.node_id}" for g in gaps[:10])
            extra = f" (+{len(gaps) - 10} more)" if len(gaps) > 10 else ""
            forge_logger.emit(
                "INFO",
                "PHASE",
                f"Phase {state['phase']} iter={state['iteration']} — {len(gaps)} gap(s)",
                f"{gap_summary}{extra}",
            )
        else:
            forge_logger.phase_no_gaps(state["phase"], state["iteration"], 0)
        # Broadcast current gap list at the start of each batch
        flow._broadcast_gap_list(flow._analyser.analyse(flow.graph))

        # Populate work queue from gaps (clear previous batch first)
        work_queue.clear_phase(state["phase"])
        for g in gaps:
            work_queue.add(
                phase=state["phase"],
                category=g.type.value,
                description=g.description or f"{g.type.value} on {g.node_id}",
                target=g.node_id,
                effort="medium",
                rationale=f"Structural gap — phase {state['phase']}",
            )

        return {"current_gaps": gaps}

    async def dispatch_gap(state: StructuralLoopState) -> dict[str, Any]:
        """Dispatch the first gap in the current batch; track progress and skip counts."""
        gap = state["current_gaps"][0]
        remaining = state["current_gaps"][1:]
        gap_key = f"{gap.type}:{gap.node_id}"
        attempt = state["gap_fail_counts"].get(gap_key, 0) + 1

        # Circuit breaker: stop re-dispatching a gap that keeps making no
        # progress. Without this the loop retries it until the API quota runs
        # out (see _MAX_GAP_ATTEMPTS).
        if attempt > _MAX_GAP_ATTEMPTS:
            forge_logger.emit(
                "ERROR",
                "FLOW ",
                f"Abandoning {gap.type.value}:{gap.node_id} after "
                f"{_MAX_GAP_ATTEMPTS} failed attempts — gap stays open",
                gap_type=gap.type.value,
                node_id=gap.node_id,
            )
            logger.error(
                "forge.flow.gap_abandoned gap=%s node=%s attempts=%d",
                gap.type, gap.node_id, attempt - 1,
            )
            if abandoned_item := _find_queue_item(state["phase"], gap.node_id):
                work_queue.update_status(abandoned_item.id, "failed")
            return {
                "current_gaps": remaining,
                "gap_fail_counts": dict(state["gap_fail_counts"]),
                "iteration": state["iteration"] + 1,
                "single_step_done": False,
                "abandoned": {*(state.get("abandoned") or set()), gap_key},
            }

        # Quick staleness check: skip if the gap no longer exists (e.g. partial
        # work from a prior retry already resolved it).
        if _gap_already_resolved(flow, gap):
            forge_logger.emit(
                "INFO", "GAP  ", f"Skipping {gap.type.value}:{gap.node_id} — already resolved"
            )
            fail_counts = dict(state["gap_fail_counts"])
            fail_counts.pop(gap_key, None)
            return {
                "current_gaps": remaining,
                "gap_fail_counts": fail_counts,
                "iteration": state["iteration"],
                "single_step_done": False,
            }

        forge_logger.emit(
            "INFO",
            "GAP  ",
            f"Dispatching {gap.type.value}:{gap.node_id} (attempt {attempt})",
            gap.description,
        )

        # Update work queue item status
        wq_item = _find_queue_item(state["phase"], gap.node_id)
        if wq_item:
            work_queue.update_status(wq_item.id, "in_progress")

        try:
            crew_out = await flow._dispatch(gap, attempt=attempt)
        except DispatchQuotaError as exc:
            # Propagate: quota exhaustion must halt the run loudly instead
            # of finalizing the phase as if its gaps had been processed.
            forge_logger.emit(
                "ERROR",
                "FLOW ",
                f"API quota exhausted — aborting phase {state['phase']}",
                str(exc),
            )
            if wq_item:
                work_queue.update_status(wq_item.id, "failed")
            raise

        # Resolution certificate: only the gap's own analyser check clearing
        # proves resolution. A write anywhere else — however large — does not.
        resolved = not _gap_still_open(flow, gap)

        fail_counts = dict(state["gap_fail_counts"])
        single_step_done = False

        _record_action(state, gap, wq_item, resolved, attempt)

        # Every dispatch counts against _MAX_GAP_ATTEMPTS, certified or not.
        # A graph-state version-sum delta used to reset the counter, so an
        # agent that created wrong-typed or unrelated nodes on every call was
        # re-dispatched without bound — fake progress spun the loop until
        # quota exhaustion (proven by test_hostile_agent_convergence.py; the
        # LangGraph default recursion limit does not bound this graph). A gap
        # whose certificate clears never reappears in collect_gaps, so the
        # stale counter is never consulted for it.
        fail_counts[gap_key] = attempt

        if resolved:
            forge_logger.gap_resolved(gap.type.value, gap.node_id)
            flow.state.iteration += 1
            single_step_done = flow.state.single_step
            if wq_item:
                work_queue.update_status(wq_item.id, "done")
        else:
            logger.warning("forge.flow.no_progress gap=%s consecutive=%d", gap.type, attempt)
            forge_logger.gap_no_progress(gap.type.value, gap.node_id, attempt)
            if "OK:" in (crew_out or ""):
                forge_logger.emit(
                    "WARN",
                    "FLOW ",
                    f"Possible hallucination: gap still open after claimed "
                    f"success — {gap.type.value}:{gap.node_id}",
                )
            if wq_item:
                work_queue.update_status(wq_item.id, "pending")

        iteration = state["iteration"] + 1

        # Throttled broadcast: only every N dispatches within a batch
        if iteration % _BROADCAST_EVERY == 0 or not remaining:
            flow._broadcast_gap_list(flow._analyser.analyse(flow.graph))

        return {
            "current_gaps": remaining,
            "iteration": iteration,
            "gap_fail_counts": fail_counts,
            "single_step_done": single_step_done,
        }

    async def finalize(state: StructuralLoopState) -> dict[str, Any]:
        """Run phase audit unless approval was skipped (called from lifecycle graph)."""
        if not state["skip_approval"]:
            await flow._request_approval(state["phase"])
        return {}

    def route_after_collect(state: StructuralLoopState) -> str:
        """Route to dispatch if gaps remain, otherwise finalize."""
        return "dispatch_gap" if state["current_gaps"] else "finalize"

    def route_after_dispatch(state: StructuralLoopState) -> str:
        """Route to finalize on stop conditions, continue batch, or rescan when batch empty."""
        if state["single_step_done"]:
            return "finalize"
        if state["current_gaps"]:
            return "dispatch_gap"
        return "collect_gaps"

    builder = StateGraph(StructuralLoopState)
    builder.add_node("collect_gaps", collect_gaps)
    builder.add_node("dispatch_gap", dispatch_gap)
    builder.add_node("finalize", finalize)
    builder.set_entry_point("collect_gaps")
    builder.add_conditional_edges("collect_gaps", route_after_collect)
    builder.add_conditional_edges("dispatch_gap", route_after_dispatch)
    builder.add_edge("finalize", END)
    return builder.compile()
