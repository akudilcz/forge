"""Structural-completeness gap checks for the Gap Analyser.

Mixin methods that verify each node has the children/coverage its type
demands (ARCHITECTURE under PROJECT, HLR under PARA, and so on).
Extracted from ``gap_analyser.py``; ``GapAnalyser`` mixes this in, so all
methods remain reachable as ``GapAnalyser._check_*``.
"""

from __future__ import annotations

from typing import Any

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.analysis.node_invariants import (
    check_non_normative_marking,
    is_marked_non_normative,
)
from backend.graph.models import GraphNode, NodeType


class StructuralCompletenessChecks:
    """Per-node checks for missing expected children or coverage."""

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
        """P2: Body PARA nodes must be covered OR explicitly non-normative.

        Skips paragraphs that are not requirement sources:
        - para_type == "heading" (from document parser)
        - Empty or whitespace-only content
        - Content that is a markdown heading (starts with #)
        - Content that is only a section number/title (e.g. "## 2.1 Path Planning")
        - Marked ``non_normative: true`` with a valid documented rationale
          (specs/03 Phase 3 cover-or-classify)

        A marking whose rationale is missing/invalid is a LOUD gap
        (INADEQUATE_CONTENT with the shared shape-check message), never a
        silent coverage exemption.
        """
        marking_err = check_non_normative_marking(NodeType.PARA.value, node.properties)
        if marking_err is not None:
            return [
                Gap(
                    type=GapType.INADEQUATE_CONTENT,
                    priority=GapPriority.MAINTENANCE,
                    node_id=node.node_id,
                    description=(
                        f"Paragraph {node.node_id} has an invalid "
                        f"non-normative marking: {marking_err}"
                    ),
                )
            ]
        if is_marked_non_normative(node.properties):
            return []
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

