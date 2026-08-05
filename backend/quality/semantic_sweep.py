"""Phase-scoped semantic duplicate sweep.

Plans DUPLICATE_NODE candidates for a phase's node types and evaluates
them with the LLM semantic checker, guarding against deletions that
would orphan coverage (sole-child requirements, containers with
children, CASE nodes with unique traces). Extracted from ``checks.py``,
which re-exports both functions so import sites remain stable.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.prompting.builder import build_all_peers_context
from backend.quality.phase_map import NODE_TYPE_TO_PHASE, PHASE_TO_NODE_TYPES
from backend.server.forge_logger import forge_logger

logger = logging.getLogger(__name__)

def semantic_gaps_for_type(
    graph: Any,
    node_type: str,
    only_node_ids: set[str] | None = None,
) -> list[Gap]:
    """Build DUPLICATE_NODE gaps for non-canonical nodes of a given type.

    Nodes are partitioned into comparison groups. Within each group the
    lowest node_id is canonical; every other node becomes a candidate.

    If *only_node_ids* is provided, only those nodes are candidates —
    this restricts dedup to newly-created nodes after a batch step.
    """
    # Every node of this type is one comparison group. This was routed through
    # a `partition_for_semantic_check` helper that ignored its `node_type`
    # argument and always returned a single group, so the grouping loop only
    # ever ran once.
    candidates = sorted(
        (n for n in graph.all_nodes() if n.node_type == node_type),
        key=lambda n: n.node_id,
    )
    gaps: list[Gap] = []
    if len(candidates) >= 2:
        # The lowest node_id is canonical; the rest are dedup candidates.
        for node in candidates[1:]:
            if only_node_ids is not None and node.node_id not in only_node_ids:
                continue
            gaps.append(
                Gap(
                    type=GapType.DUPLICATE_NODE,
                    priority=GapPriority.MAINTENANCE,
                    node_id=node.node_id,
                    description=(
                        f"{node.node_type} {node.node_id} may be a semantic duplicate "
                        f"of another {node.node_type} — compare with all peers and "
                        f"delete if redundant."
                    ),
                )
            )
    return gaps


async def run_semantic_check(
    flow: Any,
    phase: int,
    only_node_ids: set[str] | None = None,
) -> int:
    """Evaluate non-canonical siblings for semantic duplicates.

    If *only_node_ids* is provided, only those nodes are candidates for
    deletion. This prevents deleting established nodes whose removal would
    uncover their parent (the create-delete-recreate loop).

    Returns the total number of nodes deleted.
    """
    phase_types = PHASE_TO_NODE_TYPES.get(phase, [])
    if not phase_types:
        logger.warning("quality.semantic_check.no_node_type phase=%d", phase)
        return 0

    planned: list[Any] = []
    for nt in phase_types:
        planned.extend(semantic_gaps_for_type(flow.graph, nt, only_node_ids))
    if not planned:
        forge_logger.emit(
            "INFO",
            "QUAL ",
            f"Phase {phase} semantic check — no sibling groups to evaluate",
        )
        return 0

    type_set = set(phase_types)
    forge_logger.emit(
        "INFO",
        "QUAL ",
        f"Phase {phase} semantic check — {len(planned)} candidate(s) for {phase_types}",
    )
    checker = flow._build_semantic_checker()
    count_before = sum(1 for n in flow.graph.all_nodes() if n.node_type in type_set)

    evaluated = 0
    skipped = 0
    for gap in planned:
        node = flow.graph.node_sync(gap.node_id)
        if node is None:
            forge_logger.emit("INFO", "SEMA ", f"Skip {gap.node_id} — already deleted")
            skipped += 1
            continue

        peers_text = build_all_peers_context(flow.graph, gap.node_id, node.node_type)
        if not peers_text:
            forge_logger.emit("INFO", "SEMA ", f"Skip {gap.node_id} — no peers found")
            skipped += 1
            continue
        # Hard guard: don't delete a node that is the sole coverage for its parent.
        # HLR sole-child of PARA → deleting creates UNCOVERED_PARA (infinite loop).
        # LLR sole-child of HLR → deleting creates UNREFINED_HLR (infinite loop).
        _sole_coverage: dict[str, str] = {"HLR": "PARA", "LLR": "HLR"}
        if node.node_type in _sole_coverage and node.parent_id:
            expected_parent_type = _sole_coverage[node.node_type]
            parent_node = flow.graph.node_sync(node.parent_id)
            if parent_node and parent_node.node_type == expected_parent_type:
                siblings = flow.graph.children_sync(node.parent_id)
                same_type_siblings = [
                    s for s in siblings
                    if s.node_type == node.node_type and s.node_id != node.node_id
                ]
                if not same_type_siblings:
                    forge_logger.emit(
                        "INFO",
                        "SEMA ",
                        f"Skip {gap.node_id} — sole {node.node_type} for {node.parent_id}",
                    )
                    skipped += 1
                    continue

        # Hard guard: never dedup a node that has children. A container node
        # whose content overlaps a peer is not semantically equivalent — its
        # *subtree* of distinct obligations is what matters, and deleting it
        # would require reparenting that entire subtree.
        if flow.graph.children_sync(gap.node_id):
            forge_logger.emit(
                "INFO",
                "SEMA ",
                f"Skip {gap.node_id} — has children (container; dedup would displace subtree)",
            )
            skipped += 1
            continue

        # Hard guard: CASE nodes with unique trace_to are never duplicates.
        # Two CASEs tracing to different requirements serve different purposes.
        node_traces = set(getattr(node, "trace_to", None) or [])
        if node_traces and node.node_type in ("CASE_HLR", "CASE_LLR"):
            peer_nodes = [
                n
                for n in flow.graph.all_nodes()
                if n.node_type == node.node_type and n.node_id != node.node_id
            ]
            peer_traces: set[str] = set()
            for p in peer_nodes:
                peer_traces.update(getattr(p, "trace_to", None) or [])
            if not node_traces & peer_traces:
                forge_logger.emit(
                    "INFO",
                    "SEMA ",
                    f"Skip {gap.node_id} — unique trace_to {list(node_traces)}",
                )
                skipped += 1
                continue

        content = node.content or "(no content)"
        if node_traces:
            content = f"trace_to={list(node_traces)}\n{content}"
        await checker(gap.node_id, content, peers_text)
        evaluated += 1

    count_after = sum(1 for n in flow.graph.all_nodes() if n.node_type in type_set)
    deleted = count_before - count_after
    forge_logger.emit(
        "INFO",
        "QUAL ",
        f"Phase {phase} semantic check complete — {evaluated} evaluated, "
        f"{skipped} skipped, {deleted} deleted",
    )
    if deleted > 0:
        # Reset only the phases that AUTHOR the deleted node types — not the
        # whole downstream chain. Downstream phases have their own gap
        # analysers that will re-surface any structural issues that arise
        # (e.g. STALE_TRACE_TO on a DESIGN pointing at a removed LLR).
        # Previously this reset phase..13, which during a late-phase deletion
        # would kick off full SUITE/DESIGN/CASE rework mid-codegen, eating
        # the phase-12 budget.
        owner_phases = {NODE_TYPE_TO_PHASE[nt] for nt in phase_types if nt in NODE_TYPE_TO_PHASE}
        for p in sorted(owner_phases):
            flow._set_phase_status(p, "pending")
        forge_logger.emit(
            "INFO",
            "QUAL ",
            f"Owner phases {sorted(owner_phases)} reset to pending — "
            f"{deleted} duplicate(s) removed from {phase_types}",
        )
    return deleted

