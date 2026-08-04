"""Context bundle assembly for agent prompts (docs/04).

Extracts the priority-ordered context tiers (inner / middle / outer)
for a given graph node so agents receive the most relevant surrounding
information when resolving a gap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.graph.models import GraphNode, NodeType

if TYPE_CHECKING:
    from backend.graph.engine import ProjectGraph


def build_context_bundle(graph: ProjectGraph, node_id: str) -> dict[str, Any]:
    """Assemble a priority-ordered context bundle for a node.

    Priority order (most relevant first):
      inner  — parent (full), trace_to targets (full), sibling CONTRACTs
               (full, never dropped), children (full)
      middle — non-CONTRACT siblings (summary: 120 chars),
               inverse-trace nodes (summary)
      outer  — grandparent → PROJECT ancestors (label only),
               ancestors of trace_to targets (orientation, label only)

    A shared ``seen`` set prevents any node appearing in more than one tier.
    """
    node = graph.node_sync(node_id)
    if node is None:
        return {"node_id": node_id, "inner": [], "middle": [], "outer": []}

    seen: set[str] = {node_id}
    inner = _build_inner(graph, node, node_id, seen)
    middle = _build_middle(graph, node_id, seen)
    outer = _build_outer(graph, node_id, seen)
    return {"node_id": node_id, "inner": inner, "middle": middle, "outer": outer}


def _build_inner(
    graph: ProjectGraph, node: GraphNode, node_id: str, seen: set[str],
) -> list[dict[str, Any]]:
    """INNER tier: parent + children + elevated CONTRACTs + trace_to targets."""
    inner: list[dict[str, Any]] = []

    if node.parent_id and node.parent_id not in seen:
        parent = graph.node_sync(node.parent_id)
        if parent:
            inner.append(_full_entry("parent", parent))
            seen.add(node.parent_id)

    for child in graph.children_sync(node_id):
        if child.node_id not in seen:
            inner.append(_full_entry("child", child))
            seen.add(child.node_id)

    for ctr in _find_elevated_contracts(graph, node_id):
        if ctr.node_id not in seen:
            inner.append(_full_entry("contract", ctr))
            seen.add(ctr.node_id)

    for ref_id in (node.trace_to or []):
        if ref_id in seen:
            continue
        ref_node = graph.node_sync(ref_id)
        if ref_node:
            inner.append(_full_entry("trace_to", ref_node))
            seen.add(ref_id)

    return inner


def _find_elevated_contracts(graph: ProjectGraph, node_id: str) -> list[GraphNode]:
    """Return sibling CONTRACT nodes — the interface this node must honour."""
    return [
        s for s in graph.siblings_sync(node_id)
        if s.node_type == NodeType.CONTRACT.value
    ]


def _build_middle(
    graph: ProjectGraph, node_id: str, seen: set[str],
) -> list[dict[str, Any]]:
    """MIDDLE tier: non-CONTRACT siblings + inverse-trace nodes (summarised)."""
    middle: list[dict[str, Any]] = []

    for sib in graph.siblings_sync(node_id):
        if sib.node_type == NodeType.CONTRACT.value or sib.node_id in seen:
            continue
        summary = (
            f"[{sib.node_type}] {sib.node_id} — {sib.title}"
            f" | {sib.content[:120]}"
        )
        middle.append({
            "role": "sibling",
            "node_id": sib.node_id,
            "node_type": sib.node_type,
            "title": sib.title,
            "summary": summary,
        })
        seen.add(sib.node_id)

    for tracer_id in graph.nodes_tracing_to(node_id):
        if tracer_id in seen:
            continue
        tracer = graph.node_sync(tracer_id)
        if tracer is None:
            continue
        summary = (
            f"[{tracer.node_type}] {tracer.node_id} — {tracer.title}"
            f" | {tracer.content[:120]}"
        )
        middle.append({
            "role": "trace_from",
            "node_id": tracer.node_id,
            "node_type": tracer.node_type,
            "title": tracer.title,
            "summary": summary,
        })
        seen.add(tracer_id)

    return middle


def _build_outer(
    graph: ProjectGraph, node_id: str, seen: set[str],
) -> list[dict[str, Any]]:
    """OUTER tier: grandparent → PROJECT + ancestors of trace_to targets."""
    outer: list[dict[str, Any]] = []
    node = graph.node_sync(node_id)
    if node is None or node.parent_id is None:
        return outer

    current = graph.node_sync(node.parent_id)
    while current and current.parent_id:
        anc = graph.node_sync(current.parent_id)
        if anc is None:
            break
        if anc.node_id not in seen:
            outer.append({
                "role": "ancestor",
                "node_id": anc.node_id,
                "node_type": anc.node_type,
                "title": anc.title,
            })
            seen.add(anc.node_id)
        current = anc

    for ref_id in (node.trace_to or []):
        ref_node = graph.node_sync(ref_id)
        if ref_node is None:
            continue
        current_ref: GraphNode | None = ref_node
        while current_ref and current_ref.parent_id:
            anc = graph.node_sync(current_ref.parent_id)
            if anc is None:
                break
            if anc.node_id not in seen:
                outer.append({
                    "role": "trace_ancestor",
                    "node_id": anc.node_id,
                    "node_type": anc.node_type,
                    "title": anc.title,
                })
                seen.add(anc.node_id)
            current_ref = anc

    return outer


def _full_entry(role: str, node: GraphNode) -> dict[str, Any]:
    """Serialise a node to a full INNER-tier context entry."""
    return {
        "role": role,
        "node_id": node.node_id,
        "node_type": node.node_type,
        "title": node.title,
        "content": node.content,
        "layer": node.layer,
    }
