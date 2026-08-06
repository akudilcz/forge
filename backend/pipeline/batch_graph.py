"""Graph helpers for batch steps — node serialisation, snapshots, grouping.

Supports the batch step functions in ``batch_steps.py``: converting
graph nodes to prompt-ready dicts, snapshotting node IDs so newly
created nodes can be tracked for the semantic step, and grouping
UNREFINED_HLR gaps by their owning MODULE for the fused
implementable-spec authoring pass (U8, specs/03 Phases 7-8).
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


def _group_unrefined_by_module(
    flow: Any,
    gaps: list[Gap],
) -> tuple[list[tuple[str, dict[str, Any]]], list[str]]:
    """Group UNREFINED_HLR gaps by the MODULE whose ``trace_to`` owns each HLR.

    Supports the U8 fused implementable-spec pass: each group's context
    carries the MODULE dict, its CONTRACT child (or None), its existing
    DESIGN children, and the module's uncovered HLR ids. HLRs no MODULE
    traces to cannot join a fused batch; they are returned separately so
    the step routes them to per-gap dispatch instead of dropping them.
    """
    graph = flow.graph
    module_groups: dict[str, dict[str, Any]] = {}
    ungrouped: list[str] = []

    for gap in gaps:
        hlr = graph.node_sync(gap.node_id)
        if hlr is None:
            continue
        module_ids = graph.nodes_tracing_to(gap.node_id, source_type="MODULE")
        if not module_ids:
            ungrouped.append(gap.node_id)
            continue
        mod_id = module_ids[0]
        if mod_id not in module_groups:
            mod = graph.node_sync(mod_id)
            if mod is None:
                ungrouped.append(gap.node_id)
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
                "designs": [_node_to_dict(d) for d in designs],
                "hlr_ids": [],
            }
        module_groups[mod_id]["hlr_ids"].append(gap.node_id)

    return list(module_groups.items()), ungrouped
