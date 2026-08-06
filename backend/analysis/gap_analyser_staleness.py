"""Corpus-level staleness and adequacy gap checks for the Gap Analyser.

Mixin methods that look across the whole node list: empty/circular trace
chains, ARCHITECTURE/SUITE nodes that predate too many requirements,
stale codegen outputs, DESIGN-vs-CONTRACT alignment, and content too
short to be actionable. Extracted from ``gap_analyser.py``;
``GapAnalyser`` mixes this in.
"""

from __future__ import annotations

from typing import Any

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.analysis.node_invariants import (
    MIN_CONTENT_LENGTH,
    MIN_CONTENT_TYPES,
    check_min_content_length,
)
from backend.graph.models import GraphNode, NodeType


class CorpusStalenessChecks:
    """Trace-chain, staleness-fraction, codegen, and adequacy checks."""

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

    # Minimum content length for non-container, non-requirement nodes.
    # Requirements are checked separately (wording, atomicity, EARS).
    # Values live in the shared write-time invariant module so the write
    # tools and this backstop can never diverge.
    _MIN_CONTENT_LENGTH = MIN_CONTENT_LENGTH
    _CONTENT_CHECK_TYPES: frozenset[str] = MIN_CONTENT_TYPES

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
        """Emit CONTRACT_VIOLATION when a DESIGN contradicts the CONTRACT.

        With a structured ``properties.public_api`` (design/16), a DESIGN
        is flagged ONLY when it declares an annotated signature reusing a
        public function's name that contradicts the public_api entry —
        internal helpers the CONTRACT never lists are legitimate design
        detail, not violations. Contracts without public_api keep the
        legacy token-subset check (documented fallback for graphs authored
        before design/16).
        """
        from backend.quality.signature_validator import (  # noqa: PLC0415
            find_design_contract_mismatches,
            find_public_api_conflicts,
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
            props = contract.properties or {}
            api = props["public_api"] if "public_api" in props else None
            if isinstance(api, list) and api:
                conflicts = find_public_api_conflicts(api, design.content)
                if conflicts:
                    gaps.append(_public_api_conflict_gap(design, contract, conflicts))
                continue
            # Legacy fallback: prose-only CONTRACT (pre-design/16 graphs).
            extra = find_design_contract_mismatches(contract.content, design.content)
            if extra:
                gaps.append(_legacy_mismatch_gap(design, contract, extra))
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
        """Flag nodes with content too short to be actionable.

        Delegates to the shared write-time invariant in
        ``backend/analysis/node_invariants.py``.
        """
        gaps: list[Gap] = []
        for node in all_nodes:
            msg = check_min_content_length(node.node_type, node.content or "")
            if msg is None:
                continue
            gaps.append(
                Gap(
                    type=GapType.INADEQUATE_CONTENT,
                    priority=GapPriority.MAINTENANCE,
                    node_id=node.node_id,
                    description=f"{node.node_type} {node.node_id}: {msg}",
                    context={"content_length": len((node.content or "").strip())},
                )
            )
        return gaps

def _public_api_conflict_gap(
    design: GraphNode,
    contract: GraphNode,
    conflicts: list[str],
) -> Gap:
    """CONTRACT_VIOLATION for a DESIGN contradicting structured public_api."""
    return Gap(
        type=GapType.CONTRACT_VIOLATION,
        priority=GapPriority.MAINTENANCE,
        node_id=design.node_id,
        description=(
            f"DESIGN {design.node_id} declares signature(s) contradicting "
            f"CONTRACT {contract.node_id}'s public_api: {conflicts}. "
            f"Restate each public symbol with its exact contract signature "
            f"(parameter names and return type), or rename the internal "
            f"helper so it no longer shadows the public surface."
        ),
        context={
            "contract_id": contract.node_id,
            "conflicting_functions": conflicts,
        },
    )


def _legacy_mismatch_gap(
    design: GraphNode,
    contract: GraphNode,
    extra: list[str],
) -> Gap:
    """CONTRACT_VIOLATION under the pre-design/16 token-subset fallback."""
    return Gap(
        type=GapType.CONTRACT_VIOLATION,
        priority=GapPriority.MAINTENANCE,
        node_id=design.node_id,
        description=(
            f"DESIGN {design.node_id} declares function(s) not present in "
            f"CONTRACT {contract.node_id}: {extra}. Align the DESIGN with "
            f"the CONTRACT or extend the CONTRACT to cover them."
        ),
        context={
            "contract_id": contract.node_id,
            "extra_functions": extra,
        },
    )
