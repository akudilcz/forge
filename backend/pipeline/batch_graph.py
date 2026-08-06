"""Graph helpers for batch steps — node serialisation, snapshots, grouping.

Supports the batch step functions in ``batch_steps.py``: converting
graph nodes to prompt-ready dicts, snapshotting node IDs so newly
created nodes can be tracked for the semantic step, and grouping
UNDESIGNED LLR gaps by their owning MODULE.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.server.forge_logger import forge_logger

if TYPE_CHECKING:
    from backend.analysis.gaps import Gap


def _node_to_dict(node: Any) -> dict[str, Any]:
    """Convert a GraphNode to a plain dict for prompt building."""
    return {
        "node_id": node.node_id,
        "node_type": node.node_type,
        "title": node.title or "",
        "content": node.content or "",
        "parent_id": node.parent_id or "",
        "trace_to": node.trace_to or [],
        "properties": node.properties or {},
    }


def _snapshot_node_ids(flow: Any, node_type: str) -> set[str]:
    """Return the set of node IDs of a given type currently in the graph."""
    return {n.node_id for n in flow.graph.all_nodes() if n.node_type == node_type}


def _track_new_nodes(flow: Any, node_type: str, before: set[str]) -> set[str]:
    """Record newly created node IDs on flow for the semantic step to use.

    Always sets ``_batch_new_node_ids`` — an empty set means "no new nodes,
    so semantic should skip checking this type entirely."  ``None`` means
    "no batch ran, check everything" (the default before any batch step).
    """
    after = _snapshot_node_ids(flow, node_type)
    new_ids = after - before
    existing = getattr(flow, "_batch_new_node_ids", None) or set()
    flow._batch_new_node_ids = existing | new_ids
    if new_ids:
        forge_logger.emit(
            "INFO", "BATCH", f"Tracked {len(new_ids)} new {node_type} node(s) for semantic check"
        )
    return new_ids


def _group_undesigned_by_module(
    flow: Any,
    gaps: list[Gap],
) -> list[tuple[str, dict[str, Any]]]:
    """Group UNDESIGNED LLR gaps by their owning MODULE, enriched with SUITE
    and the CASEs already on parent HLRs so DESIGNs align with test intent.
    """
    graph = flow.graph
    module_groups: dict[str, dict[str, Any]] = {}

    suite = next(
        (n for n in graph.all_nodes() if n.node_type == "SUITE" and n.content),
        None,
    )
    suite_dict = _node_to_dict(suite) if suite else None

    all_cases = [
        n for n in graph.all_nodes()
        if n.node_type in ("CASE_HLR", "CASE_LLR") and n.content
    ]

    for gap in gaps:
        llr = graph.node_sync(gap.node_id)
        if llr is None or not llr.parent_id:
            continue
        module_ids = graph.nodes_tracing_to(llr.parent_id, source_type="MODULE")
        if not module_ids:
            continue
        mod_id = module_ids[0]
        if mod_id not in module_groups:
            mod = graph.node_sync(mod_id)
            if mod is None:
                continue
            children = graph.children_sync(mod_id)
            contract = next(
                (c for c in children if c.node_type == "CONTRACT"),
                None,
            )
            designs = [c for c in children if c.node_type == "DESIGN"]
            module_groups[mod_id] = {
                "module": _node_to_dict(mod),
                "contract": _node_to_dict(contract) if contract else None,
                "undesigned_llrs": [],
                "designs": [_node_to_dict(d) for d in designs],
                "suite": suite_dict,
                "parent_hlr_cases": [],
                "_parent_hlr_ids": set(),
            }
        module_groups[mod_id]["undesigned_llrs"].append(_node_to_dict(llr))
        module_groups[mod_id]["_parent_hlr_ids"].add(llr.parent_id)

    # Populate parent_hlr_cases per module group.
    for group in module_groups.values():
        hlr_ids = group.pop("_parent_hlr_ids")
        group["parent_hlr_cases"] = [
            _node_to_dict(c)
            for c in all_cases
            if any(hid in (c.trace_to or []) for hid in hlr_ids)
        ]

    return list(module_groups.items())
