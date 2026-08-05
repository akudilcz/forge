"""Deterministic deletion of byte-identical sibling duplicates.

The gap analyser (``analysis/gap_analyser_integrity.py``) emits a
``DUPLICATE_NODE`` gap with ``context.duplicate_of`` only when two
siblings share the same parent, the same node type, and identical
content after normalisation (``strip().lower()``). Asking an LLM agent
to re-verify and delete such a node is pure waste — byte-identity needs
no judgment. This resolver acts on that verified fact deterministically,
as a pre-dispatch fast path in ``pipeline/dispatch.py``.

This is not a silent fallback: the byte-identity precondition is
re-verified against the live graph at resolution time, the deletion is
logged loudly through ``forge_logger``, and a deletion that fails to
remove the node raises ``RuntimeError``. Near-duplicates (anything not
byte-identical when resolved) fall through to the existing LLM path,
where the semantic-dedup safety rules of design/01_architecture.md §7.4
apply.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.analysis.gaps import Gap, GapType
from backend.server.forge_logger import forge_logger

logger = logging.getLogger(__name__)


def _normalised(content: str | None) -> str:
    """Apply the same normalisation the analyser used to declare byte-identity."""
    return (content or "").strip().lower()


async def try_resolve_exact_duplicate(flow: Any, gap: Gap) -> bool:
    """Resolve a verified byte-identical duplicate without an LLM dispatch.

    Returns True when the gap is resolved (node deleted now, or already
    gone); False when the gap must take the LLM path (no ``duplicate_of``
    marker, canonical missing, or the pair is no longer byte-identical).
    Raises ``RuntimeError`` if the deletion precondition fails — the node
    survives ``delete_node``.
    """
    if gap.type != GapType.DUPLICATE_NODE:
        return False
    context = gap.context or {}
    if "duplicate_of" not in context:
        return False  # semantic near-duplicate — needs LLM judgment
    canonical_id = context["duplicate_of"]
    graph = flow.graph

    dup = graph.node_sync(gap.node_id)
    if dup is None:
        forge_logger.emit(
            "INFO", "DEDUP",
            f"Exact duplicate {gap.node_id} already deleted (canonical {canonical_id})",
        )
        return True

    canonical = graph.node_sync(canonical_id)
    if canonical is None:
        forge_logger.emit(
            "WARN", "DEDUP",
            f"Canonical {canonical_id} for exact duplicate {gap.node_id} "
            f"missing — deferring to LLM path",
        )
        return False

    # Re-verify byte-identity against the live graph — the pair may have
    # diverged (edit or reparent) since the gap was emitted.
    if (
        dup.parent_id != canonical.parent_id
        or dup.node_type != canonical.node_type
        or _normalised(dup.content) != _normalised(canonical.content)
    ):
        return False

    # Preserve traceability: merge the duplicate's unique trace_to refs
    # into the canonical before deleting.
    canon_traces = list(canonical.trace_to or [])
    extra = [t for t in (dup.trace_to or []) if t not in canon_traces]
    if extra:
        await graph.update_node(
            canonical_id,
            content=None,
            properties=None,
            changed_by="duplicate_resolver",
            change_reason=f"merge trace_to from exact duplicate {gap.node_id}",
            trace_to=canon_traces + extra,
        )

    # Engine deletion path — auto-reparents any children to dup's parent.
    await graph.delete_node(gap.node_id)
    if graph.node_sync(gap.node_id) is not None:
        raise RuntimeError(
            f"Deterministic duplicate deletion failed: {gap.node_id} still "
            f"present after delete_node (canonical {canonical_id})."
        )

    forge_logger.emit(
        "INFO", "DEDUP",
        f"Deterministically deleted exact duplicate {gap.node_id} "
        f"(canonical {canonical_id}, merged {len(extra)} trace_to ref(s)) "
        f"— no LLM dispatch",
        canonical_id=canonical_id,
        merged_traces=len(extra),
    )
    logger.info(
        "forge.dedup.exact_duplicate_deleted node=%s canonical=%s merged_traces=%d",
        gap.node_id, canonical_id, len(extra),
    )
    return True
