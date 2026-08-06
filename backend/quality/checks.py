"""Quality-check helpers — semantic, requirement, and consistency checks.

Standalone functions extracted from ForgeFlow to keep flow.py under 500 lines.
ForgeFlow calls these directly, passing its own dependencies.

The phase ↔ node-type maps live in ``phase_map`` and the semantic
duplicate sweep in ``semantic_sweep``; both are re-exported here so
import sites remain stable.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.quality.phase_map import NODE_TYPE_TO_PHASE as NODE_TYPE_TO_PHASE
from backend.quality.phase_map import PHASE_TO_NODE_TYPES as PHASE_TO_NODE_TYPES
from backend.quality.semantic_sweep import run_semantic_check as run_semantic_check
from backend.quality.semantic_sweep import (
    semantic_gaps_for_type as semantic_gaps_for_type,
)
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

    from backend.agents.factory import build_llm
    from backend.quality.combined_check import (
        create_combined_quality_checker,
        quality_pass_key,
    )

    all_items = [
        (n.node_id, n.node_type, (n.title or "").strip(), (n.content or "").strip())
        for n in nodes
        if (n.title or "").strip() and (n.content or "").strip()
    ]
    # Sticky PASS verdicts (specs/12 §7.4): a node whose title+content is
    # unchanged since its last full PASS is not re-sent to the judge. FAIL is
    # never cached, so repaired nodes are always re-judged. The cache lives on
    # the flow — AttributeError here is a missing precondition, not a fallback.
    verdict_cache = flow._quality_verdict_cache
    items = [
        it for it in all_items
        if quality_pass_key(it[0], it[2], it[3]) not in verdict_cache
    ]
    if len(items) < len(all_items):
        forge_logger.emit(
            "INFO",
            "XQUAL",
            f"Phase {phase} combined quality check — "
            f"{len(all_items) - len(items)} node(s) skipped via sticky PASS verdicts",
        )
    if not items:
        return []

    # Chunked judging (specs/12 §7.4): one call over a whole large phase
    # truncates at the provider output-token limit (live evidence: 81 HLRs →
    # 62 unjudged after retry), so candidates are judged in chunks. Sets of
    # at most one chunk keep the original single-call behaviour.
    batch_size: int = flow.config.llm.quality_judge_batch_size
    checker = create_combined_quality_checker(build_llm(flow.config, cacheable=True))
    forge_logger.emit(
        "INFO",
        "XQUAL",
        f"Phase {phase} combined quality check — {len(items)} node(s) in "
        f"chunk(s) of ≤{batch_size}",
    )
    gaps: list[Gap] = []
    for start in range(0, len(items), batch_size):
        chunk = items[start : start + batch_size]
        gaps.extend(await _judge_chunk(checker, phase, chunk, verdict_cache))

    forge_logger.emit(
        "INFO",
        "XQUAL",
        f"Phase {phase} combined quality check complete — "
        f"{len(gaps)} issue(s) found across {len(items)} node(s)",
    )
    return gaps


async def _judge_chunk(
    checker: Any,
    phase: int,
    chunk: list[tuple[str, str, str, str]],
    verdict_cache: dict[tuple[str, str], str],
) -> list[Gap]:
    """Judge one chunk through the checker and stamp its sticky PASSes.

    The checker itself performs one partial-rejudge retry for unjudged
    nodes/axes; anything still unjudged raises UnjudgedQualityError, which
    propagates — swallowing it into an empty gap list would score the
    silence as a clean quality sweep. Transient (non-verdict) failures get
    exactly one retry of the chunk; a second failure propagates.
    """
    from backend.quality.combined_check import UnjudgedQualityError, quality_pass_key

    try:
        gaps: list[Gap] = await checker(chunk)
    except UnjudgedQualityError:
        raise
    except Exception as exc:
        forge_logger.emit(
            "WARN",
            "XQUAL",
            f"Combined quality chunk failed for phase {phase}: "
            f"{type(exc).__name__}: {exc} — retrying once",
        )
        gaps = await checker(chunk)

    # A returned result means every node in the chunk received a verdict on
    # every applicable axis (unjudged raises). Stamp PASS for nodes with no
    # gaps; nodes that failed any axis are deliberately NOT cached. Stamping
    # per chunk means a later chunk's failure never discards evidence
    # already paid for in earlier chunks.
    failed_ids = {g.node_id for g in gaps}
    for node_id, _ntype, title, content in chunk:
        if node_id not in failed_ids:
            verdict_cache[quality_pass_key(node_id, title, content)] = "PASS"
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
    from backend.quality.semantic_duplicate_check import create_semantic_checker

    # The verdict cache lives on the flow (initialised in ForgeFlow.__init__)
    # so sticky UNIQUE verdicts survive across pipeline cycles even though
    # each cycle builds a fresh checker. AttributeError here is deliberate:
    # a flow without the cache is a missing precondition, not a fallback case.
    # cacheable=False — deletion requires the same DUPLICATE verdict from two
    # *independent* LLM calls with byte-identical prompts. A response cache
    # would replay the first verdict and make the confirmation vacuous.
    return create_semantic_checker(
        build_llm(flow.config, cacheable=False), flow.graph, flow._semantic_verdict_cache
    )


def _build_design_consolidator(flow: Any) -> Any:
    from backend.agents.factory import build_llm
    from backend.quality.design_consolidation import create_design_consolidator

    return create_design_consolidator(build_llm(flow.config, cacheable=True), flow.graph)
