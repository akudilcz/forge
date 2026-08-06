"""Node-integrity gap checks for the Gap Analyser.

Mixin methods that validate a single node's parentage, staleness against
its parent, trace_to references, requirement wording, titles, and exact
sibling duplication. Also hosts ``VALID_PARENT_TYPES``, the single source
of truth for parent-child type compatibility. Extracted from
``gap_analyser.py``; ``GapAnalyser`` mixes this in.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.analysis.node_invariants import (
    CASE_TRACE_TARGET,
    TITLE_EXEMPT_TYPES,
    check_case_trace_targets,
    check_requirement_wording,
    check_title,
    check_title_distinct_from_parent,
    normalise_content,
    normalise_title,
)
from backend.graph.models import GraphNode, NodeType
from backend.graph.provenance import DERIVED_FROM_HASH, provenance_hash
from backend.server.forge_logger import forge_logger

# Canonical parent-type constraints (specs/12-artifact-model-and-traceability.md §2).
# Maps child node_type → frozenset of valid parent node_types.
# Node types absent from this dict (PROJECT, DOCUMENT, RECORD) have no constraint.
#: The single source of truth for parent-child type compatibility.
#:
#: ``backend/tools/graph_write.py`` used to keep a second, divergent copy.
#: It permitted CASE_HLR under HLR and HLR under PROJECT, both of which this
#: analyser immediately reports as ORPHAN_NODE — so an agent could perform a
#: reparent the write tool accepted, watch the gap reappear, and repeat. It
#: also lacked entries for ARCHITECTURE and SUITE (permitting anything) while
#: forbidding the nested PARA this table allows.
VALID_PARENT_TYPES: dict[str, frozenset[str]] = {
    "PARA": frozenset({"DOCUMENT", "PARA"}),  # nested sections allowed
    "HLR": frozenset({"PARA"}),
    "LLR": frozenset({"HLR"}),
    "ARCHITECTURE": frozenset({"PROJECT"}),
    "MODULE": frozenset({"ARCHITECTURE"}),
    "CONTRACT": frozenset({"MODULE"}),
    "DESIGN": frozenset({"MODULE"}),
    "CODE": frozenset({"DESIGN"}),
    "SUITE": frozenset({"PROJECT"}),
    "CASE_HLR": frozenset({"SUITE"}),
    "CASE_LLR": frozenset({"SUITE"}),
    "TEST": frozenset({"CASE_HLR", "CASE_LLR"}),
    "RESULT": frozenset({"TEST"}),
}


class NodeIntegrityChecks:
    """Parent, trace, wording, title, and duplicate-sibling checks."""

    # Workspace-sync node types whose validity is governed by their
    # respective UNSYNCED_* gap checks, not by parent timestamp freshness.
    _SKIP_STALENESS_TYPES: frozenset[str] = frozenset(
        {
            NodeType.CODE.value,
            NodeType.TEST.value,
            NodeType.RESULT.value,
        }
    )

    def _check_staleness(self, graph: Any, node: GraphNode) -> list[Gap]:
        """Provenance-hash staleness (specs/12-artifact-model-and-traceability.md §2.6).

        STALE_NODE fires iff the child's ``properties.derived_from_hash``
        (stamped by the engine from the parent content the child was
        authored against) differs from the hash of the parent's CURRENT
        content. Because the stamp covers content only, metadata/trace/
        title touches of the parent can never cascade staleness — the
        timestamp-based predecessor of this check once cost a build 320
        LLM repair dispatches after DOCUMENT bookkeeping in phase 2.

        Unstamped nodes (created before the provenance migration ran) are
        never guessed at: no gap is emitted, and the anomaly is logged
        loudly — schema migration ``_migrate_derived_from_hash`` is the
        backfill path.

        Workspace-sync nodes (CODE, TEST, RESULT) are skipped — their
        parents are routinely updated with metadata (line_traces, trace
        coverage) that does not invalidate the child content.
        """
        if not node.parent_id:
            return []
        if node.node_type in self._SKIP_STALENESS_TYPES:
            return []
        parent = graph.node_sync(node.parent_id)
        if not parent:
            return []
        props = node.properties or {}
        if DERIVED_FROM_HASH not in props:
            forge_logger.emit(
                "WARNING", "GAP  ",
                f"Node {node.node_id} has no {DERIVED_FROM_HASH} stamp — "
                f"staleness unknown; schema migration should have "
                f"backfilled it. Skipping STALE_NODE for this node.",
                node_id=node.node_id,
            )
            return []
        current = provenance_hash(parent.content or "")
        if props[DERIVED_FROM_HASH] != current:
            return [
                Gap(
                    type=GapType.STALE_NODE,
                    priority=GapPriority.MAINTENANCE,
                    node_id=node.node_id,
                    description=(
                        f"Node {node.node_id} is stale — parent "
                        f"{parent.node_id} content changed since this node "
                        f"was authored (provenance hash mismatch). Re-derive "
                        f"from the current parent content, or call "
                        f"graph_refresh_provenance if it is still valid."
                    ),
                    context={
                        "parent_id": parent.node_id,
                        "stored_hash": props[DERIVED_FROM_HASH],
                        "current_hash": current,
                    },
                )
            ]
        return []

    def _check_orphan(self, graph: Any, node: GraphNode) -> list[Gap]:
        """ORPHAN_NODE: parent missing, or parent exists but is the wrong type."""
        if not node.parent_id:
            return []
        parent = graph.node_sync(node.parent_id)
        if not parent:
            return [
                Gap(
                    type=GapType.ORPHAN_NODE,
                    priority=GapPriority.MAINTENANCE,
                    node_id=node.node_id,
                    description=f"Node {node.node_id} references non-existent parent {node.parent_id}.",
                )
            ]
        valid = VALID_PARENT_TYPES.get(node.node_type)
        if valid and parent.node_type not in valid:
            return [
                Gap(
                    type=GapType.ORPHAN_NODE,
                    priority=GapPriority.MAINTENANCE,
                    node_id=node.node_id,
                    description=(
                        f"Node {node.node_id} ({node.node_type}) has invalid parent "
                        f"{node.parent_id} ({parent.node_type}). "
                        f"Expected parent type: {', '.join(sorted(valid))}."
                    ),
                    context={"parent_id": node.parent_id, "parent_type": parent.node_type},
                )
            ]
        return []

    def _check_integrity(self, graph: Any, node: GraphNode) -> list[Gap]:
        """Check for integrity violations: ORPHAN_NODE, EMPTY_CONTENT, STALE_TRACE_TO."""
        gaps: list[Gap] = []
        gaps.extend(self._check_orphan(graph, node))

        # Empty content check (skip container types and heading PARAs).
        #
        # Heading PARAs are section markers — their body is split into children
        # by design. Populating them from parent content creates duplicate
        # content which semantic dedup then deletes, cascading into orphans.
        container_types = {
            NodeType.PROJECT.value,
            NodeType.DOCUMENT.value,
            NodeType.ARCHITECTURE.value,
            NodeType.SUITE.value,
        }
        is_heading_para = (
            node.node_type == NodeType.PARA.value
            and (node.para_type or "") == "heading"
        )
        if (
            not node.content
            and node.node_type not in container_types
            and not is_heading_para
        ):
            gaps.append(
                Gap(
                    type=GapType.EMPTY_CONTENT,
                    priority=GapPriority.MAINTENANCE,
                    node_id=node.node_id,
                    description=f"Node {node.node_id} has empty content.",
                )
            )

        # Stale trace_to references (non-existent nodes)
        trace_to = node.trace_to or []
        if trace_to:
            stale = [ref for ref in trace_to if graph.node_sync(ref) is None]
            if stale:
                gaps.append(
                    Gap(
                        type=GapType.STALE_TRACE_TO,
                        priority=GapPriority.MAINTENANCE,
                        node_id=node.node_id,
                        description=(
                            f"Node {node.node_id} has stale trace_to reference(s) "
                            f"to non-existent node(s): {', '.join(stale)}."
                        ),
                        context={"stale_refs": stale},
                    )
                )

        # CASE trace_to type validity: CASE(hlr) must trace to HLR; CASE(llr) to LLR.
        # Any other node type in trace_to (e.g. SUITE parent_id) is wrong.
        gaps.extend(self._check_case_trace_types(graph, node))

        # Requirement wording: HLR and LLR content must start with 'The system shall '
        gaps.extend(self._check_requirement_wording(node))

        # Requirement atomicity + EARS: checked by LLM in run_combined_quality_check

        # Title check: every authored node must carry a short human-readable title
        gaps.extend(self._check_title(node))

        # Title collision: child title must not duplicate parent's title
        gaps.extend(self._check_title_collision(graph, node))

        return gaps

    def _check_case_trace_types(self, graph: Any, node: GraphNode) -> list[Gap]:
        """Flag CASE nodes with missing trace_to or wrong-type refs."""
        case_types = CASE_TRACE_TARGET
        if node.node_type not in case_types:
            return []
        expected = case_types[node.node_type]

        trace_refs = node.trace_to or []
        msg = check_case_trace_targets(node.node_type, trace_refs, graph.node_sync)
        if msg is None:
            return []
        if not trace_refs:
            context: dict[str, Any] = {"missing_trace": True, "expected_type": expected}
        else:
            wrong = [
                ref
                for ref in trace_refs
                if (target := graph.node_sync(ref)) and target.node_type != expected
            ]
            context = {"wrong_type_refs": wrong, "expected_type": expected}
        return [
            Gap(
                type=GapType.STALE_TRACE_TO,
                priority=GapPriority.MAINTENANCE,
                node_id=node.node_id,
                description=f"{node.node_type} {node.node_id}: {msg}",
                context=context,
            )
        ]

    def _check_duplicate_siblings(self, all_nodes: list[GraphNode]) -> list[Gap]:
        """Flag sibling nodes of the same type with identical normalised content.

        Groups nodes by (parent_id, node_type). Within each group, nodes whose
        content hashes match are duplicates — the lowest node_id is kept; extras
        get an INCONSISTENT_CONTENT gap so the Quality Auditor can delete them.

        PARA nodes are exempt (specs/12 §3.5): they mirror the source
        document, whose sections may legitimately repeat identical text,
        and heading PARAs are empty by design — deleting one reparents its
        child sections and flattens the document tree.
        """
        gaps: list[Gap] = []
        by_parent_type: dict[tuple[str | None, str], list[GraphNode]] = defaultdict(list)
        for node in all_nodes:
            if node.node_type == NodeType.PARA.value:
                continue
            key = (node.parent_id, node.node_type)
            by_parent_type[key].append(node)

        for siblings in by_parent_type.values():
            if len(siblings) < 2:
                continue
            by_hash: dict[str, list[GraphNode]] = defaultdict(list)
            for node in siblings:
                # Same normalisation the write tools enforce at add/update
                # time (node_invariants.check_sibling_content_unique).
                by_hash[normalise_content(node.content or "")].append(node)

            for dupes in by_hash.values():
                if len(dupes) < 2:
                    continue
                dupes.sort(key=lambda n: n.node_id)
                canonical = dupes[0]
                for dup in dupes[1:]:
                    gaps.append(
                        Gap(
                            type=GapType.DUPLICATE_NODE,
                            priority=GapPriority.MAINTENANCE,
                            node_id=dup.node_id,
                            description=(
                                f"{dup.node_type} {dup.node_id} has identical content to "
                                f"{canonical.node_id} — probable duplicate; consider deleting."
                            ),
                            context={"duplicate_of": canonical.node_id},
                        )
                    )
        return gaps

    def _check_requirement_wording(self, node: GraphNode) -> list[Gap]:
        """Flag HLR/LLR nodes with bad wording or placeholder content.

        Delegates to the shared write-time invariant in
        ``backend/analysis/node_invariants.py`` so tool enforcement and
        analyser backstop can never diverge.
        """
        msg = check_requirement_wording(node.node_type, node.content or "")
        if msg is None:
            return []
        return [
            Gap(
                type=GapType.MALFORMED_REQUIREMENT,
                priority=GapPriority.MAINTENANCE,
                node_id=node.node_id,
                description=f"{node.node_type} {node.node_id}: {msg}",
            )
        ]

    def _check_title(self, node: GraphNode) -> list[Gap]:
        """Flag authored nodes that lack a short (3-5 word) human-readable title.

        Delegates to the shared write-time invariant in
        ``backend/analysis/node_invariants.py``.
        """
        msg = check_title(node.node_type, node.title)
        if msg is None:
            return []
        return [
            Gap(
                type=GapType.UNTITLED_NODE,
                priority=GapPriority.MAINTENANCE,
                node_id=node.node_id,
                description=f"Node {node.node_id} ({node.node_type}): {msg}",
                context={"current_title": node.title.strip()},
            )
        ]

    def _check_sibling_title_duplicates(self, all_nodes: list[GraphNode]) -> list[Gap]:
        """Flag pairs of siblings under the same parent with identical titles.

        Emits one gap per duplicated title group, targeting the later-created node
        (so the canonical first node keeps its title). Case/whitespace-insensitive
        via the shared ``node_invariants.normalise_title`` used by the write tools.
        """
        by_parent: dict[str, dict[str, list[GraphNode]]] = {}
        for n in all_nodes:
            if n.node_type in TITLE_EXEMPT_TYPES or not n.parent_id:
                continue
            title_key = normalise_title(n.title or "")
            if not title_key:
                continue
            by_parent.setdefault(n.parent_id, {}).setdefault(title_key, []).append(n)

        gaps: list[Gap] = []
        for parent_id, groups in by_parent.items():
            for _title_key, nodes in groups.items():
                if len(nodes) < 2:
                    continue
                ordered = sorted(nodes, key=lambda n: n.node_id)
                canonical = ordered[0]
                for duplicate in ordered[1:]:
                    gaps.append(
                        Gap(
                            type=GapType.SIBLING_TITLE_DUPLICATE,
                            priority=GapPriority.MAINTENANCE,
                            node_id=duplicate.node_id,
                            description=(
                                f"Node {duplicate.node_id} shares title "
                                f"{duplicate.title!r} with sibling {canonical.node_id} "
                                f"under parent {parent_id}. Titles among siblings must be distinct."
                            ),
                            context={
                                "sibling_id": canonical.node_id,
                                "shared_title": duplicate.title,
                                "parent_id": parent_id,
                            },
                        )
                    )
        return gaps

    def _check_title_collision(self, graph: Any, node: GraphNode) -> list[Gap]:
        """Flag nodes whose title exactly matches their parent's title.

        A child whose title is identical (case/whitespace-insensitive) to its
        parent's signals that the child has not narrowed scope — a common
        drift pattern when agents mirror parent labels instead of describing
        the specific obligation. Delegates to the shared write-time invariant
        in ``backend/analysis/node_invariants.py`` (also enforced by the
        write tools) so the two layers can never diverge.
        """
        if not node.parent_id:
            return []
        parent = graph.node_sync(node.parent_id)
        if parent is None:
            return []
        msg = check_title_distinct_from_parent(node.node_type, node.title or "", parent)
        if msg is None:
            return []
        return [
            Gap(
                type=GapType.TITLE_COLLIDES_WITH_PARENT,
                priority=GapPriority.MAINTENANCE,
                node_id=node.node_id,
                description=(
                    f"Node {node.node_id} title {node.title!r} is identical to its "
                    f"parent {parent.node_id} ({parent.node_type}) title. "
                    f"Retitle the child to reflect its narrower scope."
                ),
                context={
                    "current_title": node.title,
                    "parent_id": parent.node_id,
                    "parent_title": parent.title,
                },
            )
        ]

