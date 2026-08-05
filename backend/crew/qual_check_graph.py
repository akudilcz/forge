"""QualCheckGraph — quality-gap stability loop as a LangGraph StateGraph.

Topology:

    __start__
        ↓
    scan_gaps ──(no gaps)───────────────────────────────→ finalize
        ↓ (has gaps)
    dispatch_gap ◀──────────────────────────────────────┐
        ↓ (pending empty)          (more pending) ──┘
    assess_stability
        ├── (unstable, passes remain) ──────────────→ scan_gaps
        └── (stable or max passes)  ────────────────→ finalize
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from backend.analysis.gaps import Gap
from backend.crew.dispatch import DispatchQuotaError
from backend.server.forge_logger import forge_logger
from backend.work_queue import work_queue

if TYPE_CHECKING:
    from backend.crew.flow import ForgeFlow

logger = logging.getLogger(__name__)

_MAX_QUAL_PASSES = 5


class QualCheckState(TypedDict):
    """LangGraph state for the quality-check stability loop.

    The loop re-scans until no nodes are deleted in a pass (stable) or
    ``_MAX_QUAL_PASSES`` is reached. ``pass_had_deletions`` drives the routing
    decision; ``had_deletions`` remembers whether *any* pass had deletions so
    downstream phases can be reset at finalization.
    """

    phase: int
    node_types: list[str]     # node types to check this phase
    pass_num: int
    pending_gaps: list[Gap]   # current pass queue
    count_before: int         # node count at start of current pass
    total_checked: int        # cumulative gaps dispatched
    had_deletions: bool       # True if any pass removed nodes
    pass_had_deletions: bool  # True if the current pass removed nodes


def _count_nodes(flow: ForgeFlow, type_set: set[str]) -> int:
    """Count nodes matching any of the given types."""
    return sum(1 for n in flow.graph.all_nodes() if n.node_type in type_set)


def create_qual_check_graph(flow: ForgeFlow) -> Any:
    """Return a compiled LangGraph for the quality-check stability loop."""
    from backend.crew.quality import QUALITY_GAP_TYPES

    async def scan_gaps(state: QualCheckState) -> dict[str, Any]:
        """Collect quality gaps for all matching nodes and queue them for dispatch."""
        pass_num = state["pass_num"] + 1
        node_types = state["node_types"]
        type_set = set(node_types)
        nodes = [n for n in flow.graph.all_nodes() if n.node_type in type_set]
        count_before = len(nodes)
        forge_logger.emit(
            "INFO", "QUAL ",
            f"Phase {state['phase']} qual check pass {pass_num} — "
            f"{count_before} {node_types} node(s)",
        )
        gap_map = flow._quality_gaps_for_types(node_types)
        planned = [
            g for n in nodes if n.node_id in gap_map for g in gap_map[n.node_id]
        ]
        # Populate work queue from quality gaps
        for g in planned:
            work_queue.add(
                phase=state["phase"],
                category=g.type.value,
                description=g.description or f"{g.type.value} on {g.node_id}",
                target=g.node_id,
                effort="low",
                rationale=f"Quality gap — phase {state['phase']} pass {pass_num}",
            )

        return {
            "pass_num": pass_num,
            "pending_gaps": planned,
            "count_before": count_before,
        }

    async def dispatch_gap(state: QualCheckState) -> dict[str, Any]:
        """Dispatch the next pending quality gap if its node still exists."""
        pending = list(state["pending_gaps"])
        gap = pending.pop(0)
        total = state["total_checked"]
        if flow.graph.node_sync(gap.node_id):
            # Update queue item status
            wq_item = next(
                (i for i in work_queue.items_for_phase(state["phase"]) if i.target == gap.node_id),
                None,
            )
            if wq_item:
                work_queue.update_status(wq_item.id, "in_progress")
            try:
                await flow._dispatch(gap)
            except DispatchQuotaError as exc:
                # Propagate: quota exhaustion must halt the run loudly.
                # Returning an empty queue here routed to finalize, which
                # logged "qual check complete" with the remaining quality
                # gaps silently dropped.
                forge_logger.emit(
                    "ERROR", "FLOW ",
                    f"API quota exhausted — aborting phase {state['phase']}",
                    str(exc),
                )
                if wq_item:
                    work_queue.update_status(wq_item.id, "failed")
                raise
            if wq_item:
                work_queue.update_status(wq_item.id, "done")
            all_gaps = flow._analyser.analyse(flow.graph)
            structural = [g for g in all_gaps if g.type not in QUALITY_GAP_TYPES]
            flow._broadcast_gap_list(structural + pending)
            total += 1
        return {"pending_gaps": pending, "total_checked": total}

    async def assess_stability(state: QualCheckState) -> dict[str, Any]:
        """Compare node count before and after the pass; flag if any nodes were deleted."""
        type_set = set(state["node_types"])
        count_after = _count_nodes(flow, type_set)
        pass_had_deletions = count_after < state["count_before"]
        if pass_had_deletions:
            forge_logger.emit(
                "INFO", "QUAL ",
                f"Phase {state['phase']} pass {state['pass_num']} removed "
                f"{state['count_before'] - count_after} node(s) — re-checking",
            )
        return {
            "pass_had_deletions": pass_had_deletions,
            "had_deletions": state["had_deletions"] or pass_had_deletions,
        }

    async def finalize(state: QualCheckState) -> dict[str, Any]:
        """Log completion and reset downstream phase statuses if nodes were deleted."""
        forge_logger.emit(
            "INFO", "QUAL ",
            f"Phase {state['phase']} qual check complete — "
            f"{state['total_checked']} check(s) dispatched",
        )
        if state["had_deletions"]:
            # Reset only the owner phases of the deleted node types. Downstream
            # phases' gap analysers will re-surface structural issues if any
            # cascade applies. Wholesale reset of phase..13 during a late phase
            # causes SUITE/DESIGN/CASE rework cascades mid-codegen.
            from backend.crew.quality import NODE_TYPE_TO_PHASE  # noqa: PLC0415
            owner_phases = {
                NODE_TYPE_TO_PHASE[nt]
                for nt in state["node_types"]
                if nt in NODE_TYPE_TO_PHASE
            }
            for p in sorted(owner_phases):
                flow._set_phase_status(p, "pending")
            forge_logger.emit(
                "INFO", "QUAL ",
                f"Owner phases {sorted(owner_phases)} reset to pending — "
                f"nodes deleted from {state['node_types']}",
            )
        return {}

    def route_after_scan(state: QualCheckState) -> str:
        """Route to dispatch if gaps were found, otherwise finalize."""
        return "dispatch_gap" if state["pending_gaps"] else "finalize"

    def route_after_dispatch(state: QualCheckState) -> str:
        """Continue dispatching remaining gaps or assess stability when batch is empty."""
        return "dispatch_gap" if state["pending_gaps"] else "assess_stability"

    def route_after_assess(state: QualCheckState) -> str:
        """Re-scan if deletions occurred and pass cap not reached, otherwise finalize."""
        if state["pass_had_deletions"] and state["pass_num"] < _MAX_QUAL_PASSES:
            return "scan_gaps"
        return "finalize"

    builder: StateGraph[QualCheckState] = StateGraph(QualCheckState)
    builder.add_node("scan_gaps",        scan_gaps)
    builder.add_node("dispatch_gap",     dispatch_gap)
    builder.add_node("assess_stability", assess_stability)
    builder.add_node("finalize",         finalize)
    builder.set_entry_point("scan_gaps")
    builder.add_conditional_edges("scan_gaps",        route_after_scan)
    builder.add_conditional_edges("dispatch_gap",     route_after_dispatch)
    builder.add_conditional_edges("assess_stability", route_after_assess)
    builder.add_edge("finalize", END)
    return builder.compile()
