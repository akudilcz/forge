"""Batched micro-repair pre-pass — one LLM call fixes N small gaps.

When a cycle's gap list contains ``MIN_BATCH_SIZE`` or more gaps of the
same batchable repair family (title fixes or requirement-wording fixes),
one structured LLM call repairs the whole family instead of N per-gap
agent dispatches (design/01 §7.4).

Safety model:

* Fixes are applied through the graph engine only after passing the same
  write-time invariants the graph tools enforce
  (``backend.analysis.node_invariants``).
* Every failure is loud, never silent: a dropped/garbled node line, an
  invariant-rejected fix, or a transport failure of the batch call is
  logged and leaves the affected gap(s) OPEN for the normal per-gap
  dispatch path.
* Resolution is certified per-gap by the analyser re-check (design/01
  §8.3): a gap leaves the cycle only when its fix applied AND a fresh
  analyser scan no longer reports its exact ``(type, node_id)`` key.
  Judge-found types the analyser cannot re-detect are certified by the
  invariant-validated applied write.
"""

from __future__ import annotations

from typing import Any

from backend.analysis.gaps import Gap, GapType
from backend.analysis.node_invariants import (
    check_requirement_wording,
    check_sibling_content_unique,
    check_sibling_title_unique,
    check_title,
)
from backend.prompting.repair_batch import (
    MIN_BATCH_SIZE,
    TITLE_FAMILY,
    TITLE_REPAIR_SYSTEM_PROMPT,
    WORDING_FAMILY,
    WORDING_REPAIR_SYSTEM_PROMPT,
    RepairEntry,
    build_title_repair_payload,
    build_wording_repair_payload,
)
from backend.server.forge_logger import forge_logger

_CHANGED_BY = "micro-repair-batch"


def _build_repair_llm(flow: Any) -> Any:
    """Seam for tests; deferred import mirrors the other checker builders."""
    from backend.agents.factory import build_llm  # noqa: PLC0415

    return build_llm(flow.config, cacheable=True)


async def apply_micro_repair_batches(flow: Any, gaps: list[Gap]) -> list[Gap]:
    """Batch-repair title/wording families in *gaps*; return the still-open rest.

    Non-batchable gaps and below-threshold families pass through untouched
    (no LLM is built). The returned list preserves the input order.
    """
    applied: set[tuple[GapType, str]] = set()
    for family, is_title in ((TITLE_FAMILY, True), (WORDING_FAMILY, False)):
        family_gaps = [g for g in gaps if g.type in family]
        if len(family_gaps) < MIN_BATCH_SIZE:
            continue
        applied |= await _repair_family(flow, family_gaps, is_title)

    if not applied:
        return gaps

    # Per-gap resolution certificate (design/01 §8.3): only a fresh analyser
    # scan proves resolution for analyser-detectable gap types.
    open_keys = {(g.type, g.node_id) for g in flow._analyser.analyse(flow.graph)}
    remaining = [
        g
        for g in gaps
        if (g.type, g.node_id) not in applied or (g.type, g.node_id) in open_keys
    ]
    forge_logger.emit(
        "INFO",
        "REPR ",
        f"Micro-repair batch resolved {len(gaps) - len(remaining)}/{len(gaps)} "
        f"gap(s); {len(remaining)} remain for per-gap dispatch",
    )
    return remaining


def _entries_for(
    flow: Any, family_gaps: list[Gap], is_title: bool
) -> tuple[list[RepairEntry], dict[str, list[Gap]]]:
    """Build one RepairEntry per live node, merging multi-gap violations."""
    by_node: dict[str, list[Gap]] = {}
    for g in family_gaps:
        by_node.setdefault(g.node_id, []).append(g)

    entries: list[RepairEntry] = []
    live: dict[str, list[Gap]] = {}
    for node_id, node_gaps in by_node.items():
        node = flow.graph.node_sync(node_id)
        if node is None:
            continue  # node gone — the gap is moot and drops out downstream
        sibling_titles: tuple[str, ...] = ()
        if is_title and node.parent_id:
            sibling_titles = tuple(
                (s.title or "").strip()
                for s in flow.graph.children_sync(node.parent_id)
                if s.node_id != node_id and (s.title or "").strip()
            )
        entries.append(
            RepairEntry(
                node_id=node_id,
                node_type=node.node_type,
                title=node.title or "",
                content=node.content or "",
                violation="; ".join(g.description for g in node_gaps),
                sibling_titles=sibling_titles,
            )
        )
        live[node_id] = node_gaps
    return entries, live


async def _repair_family(
    flow: Any, family_gaps: list[Gap], is_title: bool
) -> set[tuple[GapType, str]]:
    """One batched call for one family; returns the applied gap keys."""
    from langchain_core.messages import HumanMessage, SystemMessage  # noqa: PLC0415

    entries, live = _entries_for(flow, family_gaps, is_title)
    if len(entries) < MIN_BATCH_SIZE:
        return set()

    family_name = "title" if is_title else "wording"
    system = TITLE_REPAIR_SYSTEM_PROMPT if is_title else WORDING_REPAIR_SYSTEM_PROMPT
    payload = (
        build_title_repair_payload(entries)
        if is_title
        else build_wording_repair_payload(entries)
    )
    forge_logger.emit(
        "INFO",
        "REPR ",
        f"Micro-repair batch — {len(entries)} {family_name} fix(es) in one call",
    )

    try:
        llm = _build_repair_llm(flow)
        response = await llm.ainvoke(
            [SystemMessage(content=system), HumanMessage(content=payload)]
        )
    except Exception as exc:  # noqa: BLE001 — loud fallback, never silent
        forge_logger.emit(
            "ERROR",
            "REPR ",
            f"Micro-repair {family_name} batch call failed — all "
            f"{len(entries)} gap(s) stay open for per-gap dispatch",
            f"{type(exc).__name__}: {exc}",
        )
        return set()

    text = response.content if hasattr(response, "content") else str(response)
    from backend.prompting.repair_batch import parse_repair_response  # noqa: PLC0415

    fixes, missing = parse_repair_response(text, [e.node_id for e in entries])
    for nid in missing:
        forge_logger.emit(
            "WARN",
            "REPR ",
            f"Micro-repair batch returned no usable fix for {nid} — "
            f"gap stays open for per-gap dispatch",
        )

    applied: set[tuple[GapType, str]] = set()
    for nid, value in fixes.items():
        if await _apply_fix(flow, nid, value, is_title):
            applied |= {(g.type, g.node_id) for g in live[nid]}
    return applied


async def _apply_fix(flow: Any, node_id: str, value: str, is_title: bool) -> bool:
    """Validate one fix against write-time invariants, then write it."""
    node = flow.graph.node_sync(node_id)
    if node is None:
        return False
    siblings = (
        flow.graph.children_sync(node.parent_id) if node.parent_id else []
    )
    if is_title:
        error = check_title(node.node_type, value) or check_sibling_title_unique(
            node.node_type, value, node_id, siblings
        )
    else:
        error = check_requirement_wording(
            node.node_type, value
        ) or check_sibling_content_unique(node.node_type, value, node_id, siblings)
    if error:
        forge_logger.emit(
            "WARN",
            "REPR ",
            f"Micro-repair fix for {node_id} rejected by write-time invariant "
            f"— gap stays open for per-gap dispatch",
            error,
        )
        return False

    kind = "title" if is_title else "wording"
    await flow.graph.update_node(
        node_id,
        content=None if is_title else value,
        properties=None,
        changed_by=_CHANGED_BY,
        change_reason=f"batched micro-repair ({kind} fix)",
        title=value if is_title else None,
    )
    return True
