"""Task description builder — maps Gap types to agent task prompts.

Extracted from ForgeFlow to keep flow.py focused on orchestration and
comply with the 50-line function / 500-line file limits in CLAUDE.md.

Each `build_*` helper returns a `(description, expected_output)` tuple.
"""

from __future__ import annotations

from typing import Any

from backend.analysis.gaps import Gap, GapType
from backend.prompting.context_budget import (
    DEFAULT_BUDGET_TOKENS,
    P_ANCESTOR_CHAIN,
    P_EXISTING_PEERS,
    P_LANDSCAPE,
    P_PEER_ARTEFACT,
    P_SIBLING_FOR_DEDUP,
    P_TARGET_PARENT,
    P_TRACE_TO,
    P_WHITEPAPER_DIGEST,
    Section,
    pack,
)
from backend.prompting.graph_context import (
    _build_shallow_req_context as _build_shallow_req_context,
)
from backend.prompting.graph_context import (
    _find_architecture_node as _find_architecture_node,
)
from backend.prompting.graph_context import (
    build_all_hlrs_context as build_all_hlrs_context,
)
from backend.prompting.graph_context import (
    build_all_llrs_context as build_all_llrs_context,
)
from backend.prompting.graph_context import (
    build_all_modules_context as build_all_modules_context,
)
from backend.prompting.graph_context import (
    build_all_peers_context as build_all_peers_context,
)
from backend.prompting.graph_context import (
    build_ancestor_context as build_ancestor_context,
)
from backend.prompting.graph_context import (
    build_architecture_context as build_architecture_context,
)
from backend.prompting.graph_context import (
    build_cases_for_requirement as build_cases_for_requirement,
)
from backend.prompting.graph_context import (
    build_design_for_llr as build_design_for_llr,
)
from backend.prompting.graph_context import (
    build_document_digest as build_document_digest,
)
from backend.prompting.graph_context import (
    build_existing_cases_context as build_existing_cases_context,
)
from backend.prompting.graph_context import (
    build_existing_llrs_context as build_existing_llrs_context,
)
from backend.prompting.graph_context import (
    build_module_design_context as build_module_design_context,
)
from backend.prompting.graph_context import (
    build_peer_contracts_context as build_peer_contracts_context,
)
from backend.prompting.graph_context import (
    build_sibling_paras_context as build_sibling_paras_context,
)
from backend.prompting.graph_context import (
    build_sibling_req_context as build_sibling_req_context,
)
from backend.prompting.graph_context import (
    build_trace_to_context as build_trace_to_context,
)
from backend.prompting.graph_context import (
    build_traced_hlrs_for_module as build_traced_hlrs_for_module,
)
from backend.prompting.graph_context import find_suite_id as find_suite_id
from backend.prompting.task_prompts import build_descriptions as _build_descriptions

_NO_PREFETCH_CONTEXT: frozenset[GapType] = frozenset(
    {
        GapType.STALE_TRACE_TO,
    }
)


def needs_prefetch(gap_type: GapType) -> bool:
    """Return True when the gap type requires ancestor context to be prefetched."""
    return gap_type not in _NO_PREFETCH_CONTEXT


def build_context_for_gap(graph: Any, gap: Gap) -> str:
    """Return priority-ordered context for a gap, packed to the token budget.

    Sections are built at full fidelity (no mid-string truncation). If the
    total exceeds the budget, ``pack()`` drops lowest-priority whole
    sections and logs what was dropped.
    """
    if not needs_prefetch(gap.type):
        return ""
    node = graph.node_sync(gap.node_id)
    # CASE nodes: consistency is against the requirement they trace to, not their SUITE parent
    if (
        gap.type == GapType.INCONSISTENT_CONTENT
        and node is not None
        and node.node_type in ("CASE_HLR", "CASE_LLR")
    ):
        return build_trace_to_context(graph, gap.node_id)

    sections: list[Section] = []

    # UNCHUNKED_DOCUMENT: inline the full DOCUMENT content so the agent has the
    # source text directly (ancestor walk would only include a breadcrumb).
    if gap.type == GapType.UNCHUNKED_DOCUMENT:
        doc = graph.node_sync(gap.node_id)
        if doc is not None and doc.content:
            doc_section = f"[DOCUMENT {doc.node_id}] title={doc.title!r}\n{doc.content}"
            return pack(
                [Section(P_ANCESTOR_CHAIN, "document_full", doc_section)],
                budget_tokens=DEFAULT_BUDGET_TOKENS,
            )
        return ""

    ancestor = build_ancestor_context(graph, gap.node_id)
    if ancestor:
        sections.append(Section(P_ANCESTOR_CHAIN, "ancestor_chain", ancestor))

    if gap.type == GapType.UNARCHITECTED:
        hlrs_ctx = build_all_hlrs_context(graph)
        if hlrs_ctx:
            sections.append(Section(P_LANDSCAPE, "all_hlrs", hlrs_ctx))

    if gap.type == GapType.UNCONTRACTED:
        arch_ctx = build_architecture_context(graph)
        if arch_ctx and arch_ctx not in ancestor:
            sections.append(Section(P_PEER_ARTEFACT, "architecture", arch_ctx))
        # Pull-through: Tech Stack + Cross-Cutting Concerns sections, extracted
        # by heading and pinned at highest peer priority so they survive budget cuts.
        arch_node = _find_architecture_node(graph)
        if arch_node and arch_node.content:
            from backend.prompting.markdown_sections import extract_sections  # noqa: PLC0415
            stack = extract_sections(
                arch_node.content,
                ["Technology Stack", "Cross-Cutting Concerns"],
            )
            if stack:
                sections.append(
                    Section(P_PEER_ARTEFACT + 1, "arch_stack_xcut",
                            f"ARCHITECTURE EXCERPT — Tech Stack + Cross-Cutting:\n\n{stack}")
                )
        traced_ctx = build_traced_hlrs_for_module(graph, gap.node_id)
        if traced_ctx:
            sections.append(Section(P_TRACE_TO, "traced_hlrs", traced_ctx))
        peer_ctr = build_peer_contracts_context(graph, exclude_module_id=gap.node_id)
        if peer_ctr:
            sections.append(Section(P_PEER_ARTEFACT, "peer_contracts", peer_ctr))
        # Include LLRs if any exist under the traced HLRs — they sharpen signatures.
        module = graph.node_sync(gap.node_id)
        if module is not None:
            hlr_ids = module.trace_to or []
            llr_content: list[str] = []
            for hid in hlr_ids:
                for ch in graph.children_sync(hid):
                    if ch.node_type == "LLR" and ch.content:
                        llr_content.append(
                            f"[LLR {ch.node_id}] parent={hid}\n{ch.content.strip()}"
                        )
            if llr_content:
                sections.append(
                    Section(P_TRACE_TO - 1, "traced_llrs",
                            "LLRs UNDER TRACED HLRs (sharpen function signatures):\n\n"
                            + "\n\n---\n\n".join(llr_content))
                )

    if gap.type == GapType.UNSUITED:
        arch_ctx = build_architecture_context(graph)
        if arch_ctx:
            sections.append(Section(P_PEER_ARTEFACT, "architecture", arch_ctx))
        mods_ctx = build_all_modules_context(graph)
        if mods_ctx:
            sections.append(Section(P_LANDSCAPE, "all_modules", mods_ctx))
        hlrs_ctx = build_all_hlrs_context(graph)
        if hlrs_ctx:
            sections.append(Section(P_LANDSCAPE, "all_hlrs", hlrs_ctx))
        llrs_ctx = build_all_llrs_context(graph)
        if llrs_ctx:
            sections.append(Section(P_LANDSCAPE, "all_llrs", llrs_ctx))
        ctr_ctx = build_peer_contracts_context(graph)
        if ctr_ctx:
            sections.append(Section(P_PEER_ARTEFACT, "all_contracts", ctr_ctx))

    if gap.type == GapType.UNDESIGNED:
        mod_ctx = build_module_design_context(graph, gap.node_id)
        if mod_ctx:
            sections.append(Section(P_PEER_ARTEFACT, "module_design", mod_ctx))

    if gap.type == GapType.UNCOVERED_PARA:
        sib_ctx = build_sibling_paras_context(graph, gap.node_id)
        if sib_ctx:
            sections.append(Section(P_SIBLING_FOR_DEDUP, "sibling_paras", sib_ctx))

    if gap.type == GapType.UNREFINED_HLR:
        llrs_ctx = build_existing_llrs_context(graph)
        if llrs_ctx:
            sections.append(Section(P_EXISTING_PEERS, "existing_llrs", llrs_ctx))

    if gap.type in (GapType.UNTESTED_HLR, GapType.UNTESTED_LLR):
        shallow = _build_shallow_req_context(graph, gap.node_id)
        sections = [Section(P_TARGET_PARENT, "target+parent", shallow)] if shallow else []
        case_type = "hlr" if gap.type == GapType.UNTESTED_HLR else "llr"
        cases_ctx = build_existing_cases_context(graph, case_type)
        if cases_ctx:
            sections.append(
                Section(P_EXISTING_PEERS, f"existing_{case_type}_cases", cases_ctx)
            )
        # Peer: the DESIGN(s) for this LLR so test steps reference real methods.
        if gap.type == GapType.UNTESTED_LLR:
            design_ctx = build_design_for_llr(graph, gap.node_id)
            if design_ctx:
                sections.append(Section(P_PEER_ARTEFACT, "llr_design", design_ctx))
        # SUITE Scope — pinned at peer priority so it survives budget cuts.
        suite = next(
            (n for n in graph.all_nodes() if n.node_type == "SUITE" and n.content),
            None,
        )
        if suite is not None:
            sections.append(
                Section(P_PEER_ARTEFACT, "suite_strategy",
                        f"SUITE [{suite.node_id}] — test strategy:\n{suite.content}")
            )

    if gap.type == GapType.DUPLICATE_NODE and node is not None and node.node_type in {"LLR", "HLR"}:
        siblings_ctx = build_sibling_req_context(graph, gap.node_id)
        if siblings_ctx:
            sections.append(Section(P_SIBLING_FOR_DEDUP, "siblings", siblings_ctx))

    # Phase 4: whitepaper digest (rationale + constraint + non_functional PARAs)
    #
    # Deliberately a second UNARCHITECTED branch rather than being merged with
    # the one above: `pack` emits sections in append order (priority only
    # decides what gets dropped when over budget), so this must stay last in the
    # prompt. Merging the two branches would silently reorder what the agent
    # reads.
    if gap.type == GapType.UNARCHITECTED:
        digest = build_document_digest(graph)
        if digest:
            sections.append(Section(P_WHITEPAPER_DIGEST, "whitepaper_digest", digest))

    return pack(sections, budget_tokens=DEFAULT_BUDGET_TOKENS)


def build_task_description(
    gap: Gap,
    ancestor_context: str,
    attempt: int = 1,
    suite_id: str = "",
) -> tuple[str, str]:
    """Return (description, expected_output) tailored to the gap type."""
    ctx = f"\n\nContext:\n{ancestor_context}" if ancestor_context else ""
    nid = gap.node_id

    descriptions = _build_descriptions(nid, ctx, gap, suite_id=suite_id)
    description, expected_output = descriptions.get(
        gap.type,
        (
            f"Resolve gap '{gap.type.value}' on node '{nid}'.\n\n{gap.description}{ctx}",
            f"Gap '{gap.type.value}' on '{nid}' resolved in the graph.",
        ),
    )

    if attempt > 1:
        prefix = (
            f"ATTEMPT {attempt}: the previous attempt made no graph changes — "
            f"a text-only response is not acceptable. Call tools.\n\n"
        )
        description = prefix + description

    return description, expected_output
