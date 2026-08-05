"""Gap Analyser: The deterministic brain of the FORGE system.

This module implements the pure function `analyse(graph) -> list[Gap]`.
It inspects the Project Graph for structural holes and integrity violations.
All graph access uses synchronous in-memory methods for speed.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from typing import Any

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.graph.models import GraphNode, NodeType


def _log_summary(gaps: list[Gap]) -> None:
    """Emit one structured record per gap-type count for this analyse() run."""
    try:
        from backend.server.forge_logger import forge_logger  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return
    if not gaps:
        forge_logger.emit("INFO", "GAP  ", "analyse: 0 gaps", gap_total=0)
        return
    counts: dict[str, int] = {}
    for g in gaps:
        counts[g.type.value] = counts.get(g.type.value, 0) + 1
    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    forge_logger.emit(
        "INFO", "GAP  ",
        f"analyse: {len(gaps)} gaps — {summary}",
        gap_total=len(gaps),
        counts=counts,
    )


# Canonical parent-type constraints (design/01_architecture.md §2).
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


class GapAnalyser:
    """Detects gaps in the Project Graph using synchronous in-memory access."""

    def analyse(self, graph: Any) -> list[Gap]:
        """Run the full Gap Analysis on the given graph.

        Args:
            graph: The ProjectGraph instance (must support .all_nodes(),
                   .children_sync(), .node_sync()).

        Returns:
            A list of detected Gaps, sorted by priority and node ID.
        """
        all_nodes = graph.all_nodes()
        gaps: list[Gap] = []
        for node in all_nodes:
            gaps.extend(self._check_structural_completeness(graph, node))
            gaps.extend(self._check_staleness(graph, node))
            gaps.extend(self._check_integrity(graph, node))

        gaps.extend(self._check_duplicate_siblings(all_nodes))
        gaps.extend(self._check_sibling_title_duplicates(all_nodes))
        gaps.extend(self._check_empty_traces(all_nodes))
        gaps.extend(self._check_circular_traces(graph, all_nodes))
        gaps.extend(self._check_inadequate_content(all_nodes))
        gaps.extend(self._check_stale_architecture(all_nodes))
        gaps.extend(self._check_stale_suite(all_nodes))
        gaps.extend(self._check_stale_code(all_nodes))
        gaps.extend(self._check_design_contract_alignment(graph, all_nodes))

        # Sort by Priority (ASC) then Node ID (ASC) for deterministic order
        sorted_gaps = sorted(gaps, key=lambda g: (g.priority, g.node_id))
        _log_summary(sorted_gaps)
        return sorted_gaps

    def _check_structural_completeness(self, graph: Any, node: GraphNode) -> list[Gap]:
        """Check if a node has all expected children."""
        try:
            node_type = NodeType(node.node_type)
        except ValueError:
            return []

        children = graph.children_sync(node.node_id)
        checkers = {
            NodeType.PROJECT: lambda: (
                self._check_unarchitected(children, node) + self._check_unsuited(children, node)
            ),
            NodeType.HLR: lambda: self._check_hlr_gaps(graph, children, node),
            NodeType.LLR: lambda: self._check_llr_gaps(graph, node),
            NodeType.DOCUMENT: lambda: self._check_unchunked(children, node),
            NodeType.PARA: lambda: self._check_uncovered_para(children, node),
            NodeType.MODULE: lambda: self._check_uncontracted(children, node),
            NodeType.CASE_HLR: lambda: self._check_unsynced_test(children, node),
            NodeType.CASE_LLR: lambda: self._check_unsynced_test(children, node),
            NodeType.DESIGN: lambda: self._check_unsynced_design(children, node),
        }
        checker = checkers.get(node_type)
        return checker() if checker else []

    # ── Helper checks ─────────────────────────────────────────────────────────

    def _check_unarchitected(self, children: list[GraphNode], node: GraphNode) -> list[Gap]:
        """P3: PROJECT must have an ARCHITECTURE child."""
        if any(c.node_type == NodeType.ARCHITECTURE.value for c in children):
            return []
        return [
            Gap(
                type=GapType.UNARCHITECTED,
                priority=GapPriority.ARCHITECTURE,
                node_id=node.node_id,
                description=f"Project {node.node_id} has no ARCHITECTURE.",
            )
        ]

    def _check_unsuited(self, children: list[GraphNode], node: GraphNode) -> list[Gap]:
        """P8: PROJECT must have a SUITE child (test strategy document)."""
        if any(c.node_type == NodeType.SUITE.value for c in children):
            return []
        return [
            Gap(
                type=GapType.UNSUITED,
                priority=GapPriority.TEST_SUITE,
                node_id=node.node_id,
                description=f"Project {node.node_id} has no SUITE (test strategy document).",
            )
        ]

    def _check_unchunked(self, children: list[GraphNode], node: GraphNode) -> list[Gap]:
        """P1: DOCUMENT must have at least one PARA child."""
        if any(c.node_type == NodeType.PARA.value for c in children):
            return []
        return [
            Gap(
                type=GapType.UNCHUNKED_DOCUMENT,
                priority=GapPriority.DOCUMENT_STRUCTURE,
                node_id=node.node_id,
                description=f"Document {node.node_id} has no paragraphs.",
            )
        ]

    def _check_uncovered_para(self, children: list[GraphNode], node: GraphNode) -> list[Gap]:
        """P2: Body PARA nodes must have at least one HLR child.

        Skips paragraphs that are not requirement sources:
        - para_type == "heading" (from document parser)
        - Empty or whitespace-only content
        - Content that is a markdown heading (starts with #)
        - Content that is only a section number/title (e.g. "## 2.1 Path Planning")
        """
        para_type = node.para_type or "paragraph"
        content = node.content.strip()
        if para_type == "heading" or not content:
            return []
        # Skip nodes that are ONLY a heading with no body text after it.
        # Paragraphs whose content starts with a heading but contains
        # substantial body text ARE requirement sources.
        if content.startswith("#"):
            body = content.split("\n", 1)[1].strip() if "\n" in content else ""
            if len(body) < 20:
                return []
        if any(c.node_type == NodeType.HLR.value for c in children):
            return []
        return [
            Gap(
                type=GapType.UNCOVERED_PARA,
                priority=GapPriority.REQUIREMENTS_HLR,
                node_id=node.node_id,
                description=f"Paragraph {node.node_id} has no derived HLR requirements.",
            )
        ]

    def _check_uncontracted(self, children: list[GraphNode], node: GraphNode) -> list[Gap]:
        """P5: MODULE must have a CONTRACT child."""
        if any(c.node_type == NodeType.CONTRACT.value for c in children):
            return []
        return [
            Gap(
                type=GapType.UNCONTRACTED,
                priority=GapPriority.CONTRACT_DESIGN,
                node_id=node.node_id,
                description=f"MODULE {node.node_id} has no CONTRACT child.",
            )
        ]

    def _check_unsynced_test(self, children: list[GraphNode], node: GraphNode) -> list[Gap]:
        """P12: CASE nodes must have a TEST child linking to workspace."""
        if any(c.node_type == NodeType.TEST.value for c in children):
            return []
        return [
            Gap(
                type=GapType.UNSYNCED_TEST,
                priority=GapPriority.TEST_SYNC,
                node_id=node.node_id,
                description=f"Test Case {node.node_id} has no workspace TEST reference.",
            )
        ]

    def _check_unsynced_design(self, children: list[GraphNode], node: GraphNode) -> list[Gap]:
        """P11: DESIGN must have a CODE child linking to workspace."""
        if any(c.node_type == NodeType.CODE.value for c in children):
            return []
        return [
            Gap(
                type=GapType.UNSYNCED_DESIGN,
                priority=GapPriority.CODE_SYNC,
                node_id=node.node_id,
                description=f"DESIGN {node.node_id} has no workspace CODE reference.",
            )
        ]

    def _check_hlr_gaps(self, graph: Any, children: list[GraphNode], node: GraphNode) -> list[Gap]:
        """Checks for HLR nodes: UNMODULARISED (P4), UNREFINED_HLR (P6), UNTESTED_HLR (P9)."""
        gaps: list[Gap] = []
        if not graph.any_trace_to(node.node_id, source_type=NodeType.MODULE.value):
            gaps.append(
                Gap(
                    type=GapType.UNMODULARISED,
                    priority=GapPriority.MODULARISATION,
                    node_id=node.node_id,
                    description=f"HLR {node.node_id} is not addressed by any MODULE.",
                )
            )
        if not any(c.node_type == NodeType.LLR.value for c in children):
            gaps.append(
                Gap(
                    type=GapType.UNREFINED_HLR,
                    priority=GapPriority.REQUIREMENTS_LLR,
                    node_id=node.node_id,
                    description=f"HLR {node.node_id} has no LLR children.",
                )
            )
        if not self._has_case_of_type(graph, node.node_id, "hlr"):
            gaps.append(
                Gap(
                    type=GapType.UNTESTED_HLR,
                    priority=GapPriority.TEST_HLR,
                    node_id=node.node_id,
                    description=f"HLR {node.node_id} has no HLR-level test case.",
                )
            )
        return gaps

    def _check_llr_gaps(self, graph: Any, node: GraphNode) -> list[Gap]:
        """Checks for LLR nodes: UNDESIGNED (P7), UNTESTED_LLR (P10)."""
        gaps: list[Gap] = []
        if not graph.any_trace_to(node.node_id, source_type=NodeType.DESIGN.value):
            gaps.append(
                Gap(
                    type=GapType.UNDESIGNED,
                    priority=GapPriority.DESIGN,
                    node_id=node.node_id,
                    description=f"LLR {node.node_id} is not addressed by any DESIGN spec.",
                )
            )
        if not self._has_case_of_type(graph, node.node_id, "llr"):
            gaps.append(
                Gap(
                    type=GapType.UNTESTED_LLR,
                    priority=GapPriority.TEST_LLR,
                    node_id=node.node_id,
                    description=f"LLR {node.node_id} has no LLR-level test case.",
                )
            )
        return gaps

    def _has_case_of_type(self, graph: Any, node_id: str, case_type: str) -> bool:
        """Return True if a CASE_HLR or CASE_LLR traces to *node_id*."""
        expected = NodeType.CASE_HLR.value if case_type == "hlr" else NodeType.CASE_LLR.value
        return bool(graph.nodes_tracing_to(node_id, source_type=expected))

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
        """Check if a node is stale relative to its parent.

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
        if node.updated_at < parent.updated_at:
            return [
                Gap(
                    type=GapType.STALE_NODE,
                    priority=GapPriority.MAINTENANCE,
                    node_id=node.node_id,
                    description=f"Node {node.node_id} is stale (parent updated more recently).",
                    context={"parent_id": parent.node_id},
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
        case_types = {
            NodeType.CASE_HLR.value: NodeType.HLR.value,
            NodeType.CASE_LLR.value: NodeType.LLR.value,
        }
        if node.node_type not in case_types:
            return []
        expected = case_types[node.node_type]

        trace_refs = node.trace_to or []

        # No traceability links — requirement link missing
        if not trace_refs:
            return [
                Gap(
                    type=GapType.STALE_TRACE_TO,
                    priority=GapPriority.MAINTENANCE,
                    node_id=node.node_id,
                    description=(
                        f"{node.node_type} {node.node_id} has no trace_to "
                        f"— must trace to at least one {expected} node."
                    ),
                    context={"missing_trace": True, "expected_type": expected},
                )
            ]

        # Wrong-type refs in trace_to
        wrong = [
            ref
            for ref in trace_refs
            if (target := graph.node_sync(ref)) and target.node_type != expected
        ]
        if not wrong:
            return []
        return [
            Gap(
                type=GapType.STALE_TRACE_TO,
                priority=GapPriority.MAINTENANCE,
                node_id=node.node_id,
                description=(
                    f"{node.node_type} {node.node_id} has trace_to "
                    f"reference(s) to non-{expected} node(s): {', '.join(wrong)}. "
                    f"Remove them — trace_to must contain only {expected} node IDs."
                ),
                context={"wrong_type_refs": wrong, "expected_type": expected},
            )
        ]

    def _check_duplicate_siblings(self, all_nodes: list[GraphNode]) -> list[Gap]:
        """Flag sibling nodes of the same type with identical normalised content.

        Groups nodes by (parent_id, node_type). Within each group, nodes whose
        content hashes match are duplicates — the lowest node_id is kept; extras
        get an INCONSISTENT_CONTENT gap so the Quality Auditor can delete them.
        """
        gaps: list[Gap] = []
        by_parent_type: dict[tuple[str | None, str], list[GraphNode]] = defaultdict(list)
        for node in all_nodes:
            key = (node.parent_id, node.node_type)
            by_parent_type[key].append(node)

        for siblings in by_parent_type.values():
            if len(siblings) < 2:
                continue
            by_hash: dict[str, list[GraphNode]] = defaultdict(list)
            for node in siblings:
                norm = (node.content or "").strip().lower()
                content_hash = hashlib.sha256(norm.encode()).hexdigest()
                by_hash[content_hash].append(node)

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

        Catches:
        - Content not starting with 'The system shall '
        - Placeholder content referencing raw PARA/DOCUMENT node IDs
          (e.g. "The system shall PARA-0012." or "Handle PARA-0003 Content")
        """
        requirement_types = {NodeType.HLR.value, NodeType.LLR.value}
        if node.node_type not in requirement_types:
            return []
        content = (node.content or "").strip()
        if not content:
            return []

        # Check for placeholder content containing raw node IDs
        import re  # noqa: PLC0415

        if re.search(r"\bPARA-\d{4}\b", content):
            return [
                Gap(
                    type=GapType.MALFORMED_REQUIREMENT,
                    priority=GapPriority.MAINTENANCE,
                    node_id=node.node_id,
                    description=(
                        f"{node.node_type} {node.node_id} is a placeholder referencing "
                        f"a raw PARA node ID: {content[:80]!r}"
                    ),
                )
            ]

        if not content.lower().startswith("the system shall "):
            return [
                Gap(
                    type=GapType.MALFORMED_REQUIREMENT,
                    priority=GapPriority.MAINTENANCE,
                    node_id=node.node_id,
                    description=(
                        f"{node.node_type} {node.node_id} content does not start with "
                        f"'The system shall ': {content[:80]!r}"
                    ),
                )
            ]

        return []

    def _check_title(self, node: GraphNode) -> list[Gap]:
        """Flag authored nodes that lack a short (3-5 word) human-readable title."""
        # Skip node types that don't need authored titles
        skip_types = {
            NodeType.PROJECT.value,
            NodeType.DOCUMENT.value,
            NodeType.RESULT.value,
            NodeType.RECORD.value,
        }
        if node.node_type in skip_types:
            return []
        title = node.title.strip()
        word_count = len(title.split()) if title else 0
        if not title:
            msg = f"Node {node.node_id} ({node.node_type}) has no title."
        elif word_count > 7:
            msg = (
                f"Node {node.node_id} title is too long ({word_count} words): {title!r}. "
                f"Keep it to 3-5 words."
            )
        else:
            return []
        return [
            Gap(
                type=GapType.UNTITLED_NODE,
                priority=GapPriority.MAINTENANCE,
                node_id=node.node_id,
                description=msg,
                context={"current_title": title},
            )
        ]

    def _check_sibling_title_duplicates(self, all_nodes: list[GraphNode]) -> list[Gap]:
        """Flag pairs of siblings under the same parent with identical titles.

        Emits one gap per duplicated title group, targeting the later-created node
        (so the canonical first node keeps its title). Case/whitespace-insensitive.
        """
        skip_types = {
            NodeType.PROJECT.value,
            NodeType.DOCUMENT.value,
            NodeType.RESULT.value,
            NodeType.RECORD.value,
        }
        by_parent: dict[str, dict[str, list[GraphNode]]] = {}
        for n in all_nodes:
            if n.node_type in skip_types or not n.parent_id:
                continue
            title_key = (n.title or "").strip().lower()
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
        the specific obligation.
        """
        skip_types = {
            NodeType.PROJECT.value,
            NodeType.DOCUMENT.value,
            NodeType.RESULT.value,
            NodeType.RECORD.value,
        }
        if node.node_type in skip_types or not node.parent_id:
            return []
        parent = graph.node_sync(node.parent_id)
        if parent is None:
            return []
        my_title = (node.title or "").strip().lower()
        parent_title = (parent.title or "").strip().lower()
        if not my_title or not parent_title or my_title != parent_title:
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

    # ── Trace integrity checks ───────────────────────────────────────────────

    # Node types that must have non-empty trace_to.
    _MUST_TRACE: frozenset[str] = frozenset(
        {
            NodeType.MODULE.value,
            NodeType.DESIGN.value,
        }
    )

    def _check_empty_traces(self, all_nodes: list[GraphNode]) -> list[Gap]:
        """Flag MODULE/DESIGN nodes with empty trace_to."""
        gaps: list[Gap] = []
        for node in all_nodes:
            if node.node_type not in self._MUST_TRACE:
                continue
            if not node.trace_to:
                gaps.append(
                    Gap(
                        type=GapType.EMPTY_TRACE,
                        priority=GapPriority.MAINTENANCE,
                        node_id=node.node_id,
                        description=(
                            f"{node.node_type} {node.node_id} has empty trace_to "
                            f"— it should trace to at least one requirement."
                        ),
                    )
                )
        return gaps

    def _check_circular_traces(
        self,
        graph: Any,
        all_nodes: list[GraphNode],
    ) -> list[Gap]:
        """Detect cycles in trace_to references."""
        gaps: list[Gap] = []
        for node in all_nodes:
            if not node.trace_to:
                continue
            visited: set[str] = set()
            stack = list(node.trace_to)
            while stack:
                ref_id = stack.pop()
                if ref_id == node.node_id:
                    gaps.append(
                        Gap(
                            type=GapType.CIRCULAR_TRACE,
                            priority=GapPriority.MAINTENANCE,
                            node_id=node.node_id,
                            description=(
                                f"Node {node.node_id} has a circular trace_to "
                                f"chain that references itself."
                            ),
                        )
                    )
                    break
                if ref_id in visited:
                    continue
                visited.add(ref_id)
                ref = graph.node_sync(ref_id)
                if ref and ref.trace_to:
                    stack.extend(ref.trace_to)
        return gaps

    # Minimum content length (chars) for non-container, non-requirement nodes.
    # Requirements are checked separately (wording, atomicity, EARS).
    _MIN_CONTENT_LENGTH = 50
    _CONTENT_CHECK_TYPES: frozenset[str] = frozenset(
        {
            NodeType.ARCHITECTURE.value,
            NodeType.MODULE.value,
            NodeType.CONTRACT.value,
            NodeType.DESIGN.value,
            NodeType.SUITE.value,
            NodeType.CASE_HLR.value,
            NodeType.CASE_LLR.value,
        }
    )

    # Fraction of descendants added AFTER an ARCHITECTURE/SUITE node's created_at
    # beyond which it is considered stale and should be re-derived.
    _STALE_FRACTION_THRESHOLD = 0.20

    def _check_stale_architecture(self, all_nodes: list[GraphNode]) -> list[Gap]:
        """Flag ARCHITECTURE nodes created before a significant fraction of
        current HLRs. The architect should re-derive rather than patch in place.
        """
        archs = [n for n in all_nodes if n.node_type == "ARCHITECTURE"]
        hlrs = [n for n in all_nodes if n.node_type == "HLR"]
        if not archs or not hlrs:
            return []
        gaps: list[Gap] = []
        from datetime import datetime  # noqa: PLC0415

        def _is_dt(n: GraphNode) -> bool:
            return isinstance(getattr(n, "created_at", None), datetime)

        hlrs = [h for h in hlrs if _is_dt(h)]
        archs = [a for a in archs if _is_dt(a)]
        if not archs or not hlrs:
            return []
        for arch in archs:
            newer = [h for h in hlrs if h.created_at > arch.created_at]
            if not newer:
                continue
            fraction = len(newer) / len(hlrs)
            if fraction < self._STALE_FRACTION_THRESHOLD:
                continue
            gaps.append(
                Gap(
                    type=GapType.STALE_ARCHITECTURE,
                    priority=GapPriority.ARCHITECTURE,
                    node_id=arch.node_id,
                    description=(
                        f"ARCHITECTURE {arch.node_id} predates "
                        f"{len(newer)}/{len(hlrs)} HLRs "
                        f"({fraction:.0%}) — re-derive to cover them."
                    ),
                    context={
                        "newer_hlr_ids": [h.node_id for h in newer],
                        "stale_fraction": fraction,
                    },
                )
            )
        return gaps

    def _check_stale_suite(self, all_nodes: list[GraphNode]) -> list[Gap]:
        """Flag SUITE nodes created before a significant fraction of current
        HLRs or LLRs — scope needs to be revisited.
        """
        from datetime import datetime  # noqa: PLC0415

        def _is_dt(n: GraphNode) -> bool:
            return isinstance(getattr(n, "created_at", None), datetime)

        suites = [n for n in all_nodes if n.node_type == "SUITE" and _is_dt(n)]
        reqs = [n for n in all_nodes if n.node_type in ("HLR", "LLR") and _is_dt(n)]
        if not suites or not reqs:
            return []
        gaps: list[Gap] = []
        for suite in suites:
            newer = [r for r in reqs if r.created_at > suite.created_at]
            if not newer:
                continue
            fraction = len(newer) / len(reqs)
            if fraction < self._STALE_FRACTION_THRESHOLD:
                continue
            gaps.append(
                Gap(
                    type=GapType.STALE_SUITE,
                    priority=GapPriority.TEST_SUITE,
                    node_id=suite.node_id,
                    description=(
                        f"SUITE {suite.node_id} predates "
                        f"{len(newer)}/{len(reqs)} requirements "
                        f"({fraction:.0%}) — revise scope."
                    ),
                    context={
                        "newer_req_ids": [r.node_id for r in newer],
                        "stale_fraction": fraction,
                    },
                )
            )
        return gaps

    def _check_design_contract_alignment(
        self,
        graph: Any,
        all_nodes: list[GraphNode],
    ) -> list[Gap]:
        """Emit CONTRACT_VIOLATION when a DESIGN declares functions the
        owning MODULE's CONTRACT has never mentioned.
        """
        from backend.crew.signature_validator import (  # noqa: PLC0415
            find_design_contract_mismatches,
        )
        designs = [n for n in all_nodes if n.node_type == "DESIGN" and n.content]
        if not designs:
            return []
        gaps: list[Gap] = []
        for design in designs:
            if not design.parent_id:
                continue
            module = graph.node_sync(design.parent_id)
            if module is None or module.node_type != "MODULE":
                continue
            # Find the CONTRACT sibling under the same MODULE.
            contract = next(
                (
                    c for c in graph.children_sync(module.node_id)
                    if c.node_type == "CONTRACT" and c.content
                ),
                None,
            )
            if contract is None:
                continue
            extra = find_design_contract_mismatches(contract.content, design.content)
            if not extra:
                continue
            gaps.append(
                Gap(
                    type=GapType.CONTRACT_VIOLATION,
                    priority=GapPriority.MAINTENANCE,
                    node_id=design.node_id,
                    description=(
                        f"DESIGN {design.node_id} declares function(s) not "
                        f"present in CONTRACT {contract.node_id}: {extra}. "
                        f"Align the DESIGN with the CONTRACT or extend the "
                        f"CONTRACT to cover them."
                    ),
                    context={
                        "contract_id": contract.node_id,
                        "extra_functions": extra,
                    },
                )
            )
        return gaps

    def _check_stale_code(self, all_nodes: list[GraphNode]) -> list[Gap]:
        """Flag DESIGN/CASE nodes whose codegen is stale.

        Two stalenesses surface as STALE_CODE:
        * ``properties.codegen_error`` — last generation failed.
        * ``properties.codegen_hash`` differs from a freshly-computed hash
          of the current DESIGN content + owning CONTRACT content. This
          means inputs have changed since the last successful generation,
          so the existing workspace file is out-of-sync.
        """
        from backend.codegen.slice_gen import codegen_hash  # noqa: PLC0415

        # Index CONTRACT by MODULE for the hash-input recomputation.
        contracts_by_module: dict[str, str] = {}
        module_of_child: dict[str, str] = {}
        for n in all_nodes:
            if n.node_type == "CONTRACT" and n.parent_id and n.content:
                contracts_by_module[n.parent_id] = n.content
            if n.node_type == "DESIGN" and n.parent_id:
                module_of_child[n.node_id] = n.parent_id

        gaps: list[Gap] = []
        for node in all_nodes:
            if node.node_type not in ("DESIGN", "CASE_HLR", "CASE_LLR"):
                continue
            props = node.properties or {}
            err = props.get("codegen_error", "")
            if err:
                gaps.append(
                    Gap(
                        type=GapType.STALE_CODE,
                        priority=GapPriority.MAINTENANCE,
                        node_id=node.node_id,
                        description=(
                            f"{node.node_type} {node.node_id} last codegen failed: "
                            f"{err[:200]}"
                        ),
                        context={"codegen_error": err},
                    )
                )
                continue
            stored = props.get("codegen_hash", "")
            if not stored:
                continue
            if node.node_type == "DESIGN":
                module_id = module_of_child.get(node.node_id, "")
                contract_content = contracts_by_module.get(module_id, "")
            else:
                contract_content = ""
            current = codegen_hash(node.content or "", contract_content, "")
            if current != stored:
                gaps.append(
                    Gap(
                        type=GapType.STALE_CODE,
                        priority=GapPriority.MAINTENANCE,
                        node_id=node.node_id,
                        description=(
                            f"{node.node_type} {node.node_id} inputs have changed "
                            f"since last codegen — workspace file is out-of-sync. "
                            f"Regenerate."
                        ),
                        context={"stored_hash": stored, "current_hash": current},
                    )
                )
        return gaps

    def _check_inadequate_content(
        self,
        all_nodes: list[GraphNode],
    ) -> list[Gap]:
        """Flag nodes with content too short to be actionable."""
        gaps: list[Gap] = []
        for node in all_nodes:
            if node.node_type not in self._CONTENT_CHECK_TYPES:
                continue
            content = (node.content or "").strip()
            if not content:
                continue  # EMPTY_CONTENT already catches this
            if len(content) < self._MIN_CONTENT_LENGTH:
                gaps.append(
                    Gap(
                        type=GapType.INADEQUATE_CONTENT,
                        priority=GapPriority.MAINTENANCE,
                        node_id=node.node_id,
                        description=(
                            f"{node.node_type} {node.node_id} content is only "
                            f"{len(content)} chars — too short to be actionable. "
                            f"Minimum {self._MIN_CONTENT_LENGTH} chars expected."
                        ),
                        context={"content_length": len(content)},
                    )
                )
        return gaps
