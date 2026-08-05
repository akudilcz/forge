"""Quality-check helpers — semantic, requirement, and consistency checks.

Standalone functions extracted from ForgeFlow to keep flow.py under 500 lines.
ForgeFlow calls these directly, passing its own dependencies.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.crew.task_builder import build_all_peers_context
from backend.server.forge_logger import forge_logger

logger = logging.getLogger(__name__)

# Quality gap types — re-exported for use by other modules.
QUALITY_GAP_TYPES: frozenset[GapType] = frozenset(
    {
        GapType.STALE_NODE,
        GapType.ORPHAN_NODE,
        GapType.EMPTY_CONTENT,
        GapType.STALE_TRACE_TO,
        GapType.INCONSISTENT_CONTENT,
        GapType.MALFORMED_REQUIREMENT,
        GapType.NON_ATOMIC_REQUIREMENT,
        GapType.NON_EARS_REQUIREMENT,
        GapType.UNTITLED_NODE,
        GapType.TITLE_COLLIDES_WITH_PARENT,
        GapType.SIBLING_TITLE_DUPLICATE,
        GapType.STALE_TITLE,
        GapType.VAGUE_TITLE,
        GapType.DUPLICATE_NODE,
        GapType.INADEQUATE_CONTENT,
        GapType.VAGUE_REQUIREMENT,
        GapType.UNTESTABLE_REQUIREMENT,
        GapType.CONTRADICTORY_REQUIREMENTS,
        GapType.INCOMPLETE_DECOMPOSITION,
        GapType.CONTRACT_VIOLATION,
        GapType.CROSS_MODULE_COUPLING,
    }
)

# Phase in which each node type is created.
NODE_TYPE_TO_PHASE: dict[str, int] = {
    "PARA": 2,
    "HLR": 3,
    "ARCHITECTURE": 4,
    "MODULE": 5,
    "CONTRACT": 6,
    "LLR": 7,
    "DESIGN": 8,
    "SUITE": 9,
    "CASE_HLR": 10,
    "CASE_LLR": 10,
    "CODE": 13,
    "TEST": 13,
}

# Inverse: phase number → node types produced in that phase.
PHASE_TO_NODE_TYPES: dict[int, list[str]] = {}
for _nt, _ph in NODE_TYPE_TO_PHASE.items():
    PHASE_TO_NODE_TYPES.setdefault(_ph, []).append(_nt)


# ── Semantic duplicate detection ─────────────────────────────────────────────


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


# ── Requirement quality (atomicity + EARS) ───────────────────────────────────


_COMBINED_CHECK_TYPES: frozenset[str] = frozenset({
    "HLR", "LLR",
    "ARCHITECTURE", "MODULE", "CONTRACT", "DESIGN",
    "SUITE", "CASE_HLR", "CASE_LLR",
})


async def run_combined_quality_check(flow: Any, phase: int) -> list[Gap]:
    """One-shot batched quality check: atomicity + EARS + title-match + title-specific.

    Replaces the per-node loops of ``run_requirement_quality_check`` and
    ``run_title_quality_check`` with a single LLM call judging every
    candidate node for the current phase.

    Returns detected Gap objects. Does NOT modify the graph.
    """
    phase_types = PHASE_TO_NODE_TYPES.get(phase, [])
    check_types = [t for t in phase_types if t in _COMBINED_CHECK_TYPES]
    if not check_types:
        return []

    only_ids = getattr(flow, "_batch_new_node_ids", None)
    nodes = [n for n in flow.graph.all_nodes() if n.node_type in check_types]
    if only_ids:
        nodes = [n for n in nodes if n.node_id in only_ids]

    items = [
        (n.node_id, n.node_type, (n.title or "").strip(), (n.content or "").strip())
        for n in nodes
        if (n.title or "").strip() and (n.content or "").strip()
    ]
    if not items:
        return []

    from backend.agents.factory import build_llm
    from backend.crew.combined_quality_check import (
        UnjudgedQualityError,
        create_combined_quality_checker,
    )

    checker = create_combined_quality_checker(build_llm(flow.config))
    forge_logger.emit(
        "INFO",
        "XQUAL",
        f"Phase {phase} combined quality check — {len(items)} node(s)",
    )
    try:
        gaps: list[Gap] = await checker(items)
    except UnjudgedQualityError:
        # Unjudged nodes after the checker's single retry are a loud failure
        # — swallowing this into an empty gap list would score the silence
        # as a clean quality sweep.
        raise
    except Exception as exc:
        # One retry for transient failures. A second failure propagates —
        # returning [] here would be indistinguishable from a clean sweep,
        # silently disabling the quality gate for the phase.
        forge_logger.emit(
            "WARN",
            "XQUAL",
            f"Combined quality check failed for phase {phase}: "
            f"{type(exc).__name__}: {exc} — retrying once",
        )
        gaps = await checker(items)

    forge_logger.emit(
        "INFO",
        "XQUAL",
        f"Phase {phase} combined quality check complete — "
        f"{len(gaps)} issue(s) found across {len(items)} node(s)",
    )
    return gaps


# ── Design consolidation ────────────────────────────────────────────────────


def modules_needing_consolidation(
    graph: Any,
    modules: list[Any],
) -> list[tuple[Any, list[Any]]]:
    """Return (module, designs) pairs for MODULEs with >1 DESIGN child."""
    result = []
    for module in modules:
        children = graph.children_sync(module.node_id)
        designs = [c for c in children if c.node_type == "DESIGN"]
        if len(designs) > 1:
            result.append((module, designs))
    return result


def find_contract(graph: Any, module_id: str) -> str:
    """Return the CONTRACT content for a MODULE, or empty string."""
    children = graph.children_sync(module_id)
    for c in children:
        if c.node_type == "CONTRACT" and c.content:
            # graph is duck-typed (Any); GraphNode.content is a str.
            content: str = c.content
            return content
    return ""


async def run_design_consolidation(flow: Any) -> int:
    """Consolidate DESIGN sprawl within each MODULE.

    Returns the total number of DESIGNs deleted (merged away).
    """
    modules = [n for n in flow.graph.all_nodes() if n.node_type == "MODULE"]
    if not modules:
        return 0

    candidates = flow._modules_needing_consolidation(modules)
    if not candidates:
        forge_logger.emit("INFO", "CONS ", "No MODULEs need DESIGN consolidation")
        return 0

    consolidator = flow._build_design_consolidator()
    total_deleted = 0
    for module, designs in candidates:
        contract = flow._find_contract(module.node_id)
        design_dicts = [
            {
                "node_id": d.node_id,
                "content": d.content or "(no content)",
                "trace_to": d.trace_to or [],
            }
            for d in designs
        ]

        deleted = await consolidator(
            module_id=module.node_id,
            module_content=module.content or "(no content)",
            contract_content=contract,
            designs=design_dicts,
        )
        total_deleted += deleted

    if total_deleted > 0:
        for p in range(8, 14):
            flow._set_phase_status(p, "pending")
        forge_logger.emit(
            "INFO",
            "CONS ",
            f"Phases 8–13 reset to pending — {total_deleted} DESIGN(s) consolidated",
        )
    return total_deleted


# ── Detect-only scan ─────────────────────────────────────────────────────────


async def scan_qual_detect(flow: Any, phase: int) -> list[dict[str, Any]]:
    """LLM-based detect-only quality scan. Does NOT modify the graph."""
    node_types = PHASE_TO_NODE_TYPES.get(phase, [])
    if not node_types:
        logger.warning("quality.scan_qual_detect.no_node_type phase=%d", phase)
        return []

    type_set = set(node_types)
    nodes = [n for n in flow.graph.all_nodes() if n.node_type in type_set]
    if not nodes:
        return []

    quality_gap_map = flow._quality_gaps_for_types(node_types)
    total_qual = sum(len(v) for v in quality_gap_map.values())
    forge_logger.emit(
        "INFO",
        "QUAL ",
        f"Phase {phase} qual detect: {len(quality_gap_map)} node(s), "
        f"{total_qual} gap(s) for {node_types}",
    )
    findings: list[dict[str, Any]] = [
        {
            "node_id": gap.node_id,
            "gap_type": gap.type.value,
            "description": gap.description,
        }
        for gap_list in quality_gap_map.values()
        for gap in gap_list
    ]

    all_gaps = flow._analyser.analyse(flow.graph)
    structural = [g for g in all_gaps if g.type not in QUALITY_GAP_TYPES]
    qual_gaps = [
        Gap(
            type=GapType(f["gap_type"]),
            priority=GapPriority.MAINTENANCE,
            node_id=f["node_id"],
            description=f["description"],
        )
        for f in findings
    ]
    flow._broadcast_gap_list(structural + qual_gaps)

    forge_logger.emit(
        "INFO",
        "QUAL ",
        f"Phase {phase} scan-qual detect: {len(findings)} node(s) flagged for review",
    )
    return findings


# ── Quality gap collection ───────────────────────────────────────────────────


def quality_gaps_for_types(
    graph: Any,
    analyser: Any,
    node_types: list[str],
) -> dict[str, list[Gap]]:
    """Return quality gaps per node_id for nodes matching *node_types*.

    DUPLICATE_NODE is excluded — handled by run_semantic_check.
    """
    gaps = analyser.analyse(graph)
    qual_count = sum(1 for g in gaps if g.type in QUALITY_GAP_TYPES)
    atomic_count = sum(1 for g in gaps if g.type == GapType.NON_ATOMIC_REQUIREMENT)
    type_set = set(node_types)
    forge_logger.emit(
        "INFO",
        "QUAL ",
        f"_quality_gaps_for_types({node_types}): {len(gaps)} total gaps, "
        f"{qual_count} quality, {atomic_count} NON_ATOMIC",
    )
    result: dict[str, list[Gap]] = {}
    for gap in gaps:
        if gap.type not in QUALITY_GAP_TYPES or gap.type == GapType.DUPLICATE_NODE:
            continue
        node = graph.node_sync(gap.node_id)
        if not node or node.node_type not in type_set:
            continue
        result.setdefault(gap.node_id, []).append(gap)
    return result


# ── Private builders ─────────────────────────────────────────────────────────


def _build_semantic_checker(flow: Any) -> Any:
    from backend.agents.factory import build_llm
    from backend.crew.semantic_duplicate_check import create_semantic_checker

    # The verdict cache lives on the flow (initialised in ForgeFlow.__init__)
    # so sticky UNIQUE verdicts survive across pipeline cycles even though
    # each cycle builds a fresh checker. AttributeError here is deliberate:
    # a flow without the cache is a missing precondition, not a fallback case.
    return create_semantic_checker(
        build_llm(flow.config), flow.graph, flow._semantic_verdict_cache
    )


def _build_design_consolidator(flow: Any) -> Any:
    from backend.agents.factory import build_llm
    from backend.crew.design_consolidation import create_design_consolidator

    return create_design_consolidator(build_llm(flow.config), flow.graph)
