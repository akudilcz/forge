"""Write-time authoring-invariant enforcement for graph mutation tools.

Bridges the pure checks in ``backend/analysis/node_invariants.py`` (shared
with the Gap Analyser, so tool and analyser can never diverge) to the
graph-write tools. A violating write is rejected with an ``ERROR: ...``
string the agent can act on in the same turn, instead of landing in the
graph and costing a later paid repair dispatch
(design/01_architecture.md §3.6).

Graph-capability guards: these validators consult the live graph for
sibling and trace-target lookups. When the injected graph object cannot
answer (bare test doubles without real ``children_sync``/``node_sync``,
or ones returning non-``GraphNode`` stand-ins), that specific lookup is
skipped — same narrow precedent as ``reparent_node_op``. The pure,
graph-free checks (title, wording, length, CASE-must-trace) always run.
"""

from __future__ import annotations

from collections.abc import Callable

from backend.analysis.node_invariants import (
    check_case_trace_targets,
    check_min_content_length,
    check_requirement_wording,
    check_sibling_content_unique,
    check_sibling_title_unique,
    check_title,
)
from backend.graph.models import GraphNode


def _siblings(graph: object, parent_id: str, exclude_id: str) -> list[GraphNode] | None:
    """Children of ``parent_id`` minus ``exclude_id``; None if unanswerable."""
    try:
        children = graph.children_sync(parent_id)  # type: ignore[attr-defined]
        return [
            c
            for c in children
            if isinstance(c, GraphNode) and c.node_id != exclude_id
        ]
    except (AttributeError, TypeError):
        return None


def _resolver(graph: object) -> Callable[[str], GraphNode | None]:
    """Node lookup that yields None whenever the graph cannot truly answer."""

    def resolve(node_id: str) -> GraphNode | None:
        try:
            target = graph.node_sync(node_id)  # type: ignore[attr-defined]
        except (AttributeError, TypeError):
            return None
        return target if isinstance(target, GraphNode) else None

    return resolve


def validate_add_node(
    graph: object,
    node_type: str,
    node_id: str,
    parent_id: str,
    title: str,
    content: str,
    trace_to: list[str],
) -> str | None:
    """All authoring invariants for a prospective new node.

    Returns ``None`` when the write may proceed, otherwise an actionable
    ``ERROR: ...`` message for the agent.
    """
    for msg in (
        check_title(node_type, title),
        check_requirement_wording(node_type, content),
        check_min_content_length(node_type, content),
        check_case_trace_targets(node_type, trace_to, _resolver(graph)),
    ):
        if msg is not None:
            return f"ERROR: {msg}"

    if parent_id:
        siblings = _siblings(graph, parent_id, node_id)
        if siblings is not None:
            for msg in (
                check_sibling_title_unique(node_type, title, node_id, siblings),
                check_sibling_content_unique(node_type, content, node_id, siblings),
            ):
                if msg is not None:
                    return f"ERROR: {msg}"
    return None


def validate_update_node(
    graph: object,
    existing: object,
    title: str | None,
    content: str | None,
) -> str | None:
    """Invariants for the fields an update actually changes."""
    if not isinstance(existing, GraphNode):
        return None  # graph cannot answer (test double) — engine still guards
    node_type = existing.node_type
    if title is not None:
        msg = check_title(node_type, title)
        if msg is not None:
            return f"ERROR: {msg}"
    if content is not None:
        for msg in (
            check_requirement_wording(node_type, content),
            check_min_content_length(node_type, content),
        ):
            if msg is not None:
                return f"ERROR: {msg}"
    if existing.parent_id:
        siblings = _siblings(graph, existing.parent_id, existing.node_id)
        if siblings is not None:
            if title is not None:
                msg = check_sibling_title_unique(
                    node_type, title, existing.node_id, siblings
                )
                if msg is not None:
                    return f"ERROR: {msg}"
            if content is not None:
                msg = check_sibling_content_unique(
                    node_type, content, existing.node_id, siblings
                )
                if msg is not None:
                    return f"ERROR: {msg}"
    return None


def validate_trace_update(
    graph: object,
    existing: object,
    new_trace: list[str],
) -> str | None:
    """CASE trace_to membership for the prospective full trace list."""
    if not isinstance(existing, GraphNode):
        return None
    msg = check_case_trace_targets(existing.node_type, new_trace, _resolver(graph))
    if msg is not None:
        return f"ERROR: {msg}"
    return None
