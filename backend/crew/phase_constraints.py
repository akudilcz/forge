"""Phase-level node-creation constraints for graph_write.

Each gap type maps to the set of node_types that may be created (add_node)
during that task.  Operations other than add_node (update_node, update_trace,
delete_node, etc.) are always permitted.

The active constraint is stored in a contextvars.ContextVar so it propagates
automatically through the coroutine chain and into ThreadPoolExecutor threads
(Python copies context on submit).
"""

from __future__ import annotations

import contextvars

from backend.analysis.gaps import GapType
from backend.graph.models import NodeType

# Per-gap allowlist: which node_types may be created in this task.
# Empty frozenset = no new nodes at all (updates / deletes only).
PHASE_CREATE_ALLOWLIST: dict[GapType, frozenset[str]] = {
    GapType.UNCHUNKED_DOCUMENT: frozenset({NodeType.PARA.value}),
    GapType.UNCOVERED_PARA: frozenset({NodeType.HLR.value}),
    GapType.UNARCHITECTED: frozenset({NodeType.ARCHITECTURE.value, NodeType.MODULE.value}),
    GapType.UNMODULARISED: frozenset({NodeType.MODULE.value}),
    GapType.UNCONTRACTED: frozenset({NodeType.CONTRACT.value}),
    GapType.UNREFINED_HLR: frozenset({NodeType.LLR.value}),
    GapType.UNDESIGNED: frozenset({NodeType.DESIGN.value}),
    GapType.UNSUITED: frozenset({NodeType.SUITE.value}),
    GapType.UNTESTED_HLR: frozenset({NodeType.CASE_HLR.value}),
    GapType.UNTESTED_LLR: frozenset({NodeType.CASE_LLR.value}),
    # UNSYNCED_DESIGN / UNSYNCED_TEST: handled by workspace_sync step (no agent)
    # Quality gaps: update / delete only — no new nodes
    GapType.STALE_NODE: frozenset(),
    GapType.ORPHAN_NODE: frozenset(),
    GapType.EMPTY_CONTENT: frozenset(),
    GapType.STALE_TRACE_TO: frozenset(),
    GapType.INCONSISTENT_CONTENT: frozenset(),
    GapType.MALFORMED_REQUIREMENT: frozenset(),
    GapType.NON_ATOMIC_REQUIREMENT: frozenset({NodeType.HLR.value, NodeType.LLR.value}),
    GapType.NON_EARS_REQUIREMENT: frozenset(),
    GapType.UNTITLED_NODE: frozenset(),
    GapType.TITLE_COLLIDES_WITH_PARENT: frozenset(),
    GapType.SIBLING_TITLE_DUPLICATE: frozenset(),
    GapType.STALE_TITLE: frozenset(),
    GapType.VAGUE_TITLE: frozenset(),
    GapType.DUPLICATE_NODE: frozenset(),
    # LLM-detected quality gaps
    GapType.INADEQUATE_CONTENT: frozenset(),
    GapType.VAGUE_REQUIREMENT: frozenset(),
    GapType.UNTESTABLE_REQUIREMENT: frozenset(),
    GapType.CONTRADICTORY_REQUIREMENTS: frozenset(),
    GapType.INCOMPLETE_DECOMPOSITION: frozenset({NodeType.LLR.value}),
    GapType.CONTRACT_VIOLATION: frozenset(),
    GapType.CROSS_MODULE_COUPLING: frozenset(),
    GapType.STALE_ARCHITECTURE: frozenset({NodeType.MODULE.value}),
    GapType.STALE_SUITE: frozenset(),
    GapType.STALE_CODE: frozenset(),
    GapType.MISSING_CODE: frozenset(),
    GapType.EMPTY_TRACE: frozenset(),
    GapType.CIRCULAR_TRACE: frozenset(),
}

_active: contextvars.ContextVar[frozenset[str] | None] = contextvars.ContextVar(
    "phase_allowed_create", default=None
)


def set_phase_constraints(gap_type: GapType) -> contextvars.Token[frozenset[str] | None]:
    """Activate the allowlist for gap_type. Call reset_phase_constraints() in finally."""
    return _active.set(PHASE_CREATE_ALLOWLIST.get(gap_type))


def set_phase_constraints_union(
    gap_types: list[GapType],
) -> contextvars.Token[frozenset[str] | None]:
    """Activate a union of allowlists for multiple gap types (batch steps).

    Used by steps like ``batch_phase10`` that legitimately create more than
    one node type in a single LLM turn (CASE_HLR + CASE_LLR).
    """
    union: set[str] = set()
    for gt in gap_types:
        union |= PHASE_CREATE_ALLOWLIST.get(gt, frozenset())
    return _active.set(frozenset(union) if union else None)


def reset_phase_constraints(token: contextvars.Token[frozenset[str] | None]) -> None:
    """Remove the active constraint (call in finally after set_phase_constraints)."""
    _active.reset(token)


def check_create_allowed(node_type: str) -> str | None:
    """Return an error string if creating node_type is blocked, else None."""
    allowed = _active.get()
    if allowed is None:
        return None  # no constraint active
    if node_type.upper() not in {t.upper() for t in allowed}:
        allowed_str = ", ".join(sorted(allowed)) if allowed else "none"
        return (
            f"Phase constraint: may only create [{allowed_str}] nodes in this task, "
            f"not '{node_type}'. Use update_trace or update_node for existing nodes."
        )
    return None
