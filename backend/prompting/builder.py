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
from backend.prompting.task_prompts import build_descriptions as _build_descriptions

_NO_PREFETCH_CONTEXT: frozenset[GapType] = frozenset(
    {
        GapType.STALE_TRACE_TO,
    }
)


def needs_prefetch(gap_type: GapType) -> bool:
    """Return True when the gap type requires ancestor context to be prefetched."""
    return gap_type not in _NO_PREFETCH_CONTEXT

# Node types whose content is skipped during ancestor walks.
# DOCUMENT can be 20k+ chars and is not needed once PARAs/HLRs exist.
_SKIP_ANCESTOR_CONTENT: frozenset[str] = frozenset({"DOCUMENT"})


def build_ancestor_context(graph: Any, node_id: str) -> str:
    """Walk parent chain and collect content for task context.

    Skips DOCUMENT content (included as a title-only breadcrumb) to avoid
    injecting the full whitepaper into every downstream dispatch.
    """
    import logging  # noqa: PLC0415

    _ctx_log = logging.getLogger("forge.context")

    parts: list[str] = []
    visited: set[str] = set()
    current_id: str | None = node_id

    _ctx_log.info("build_ancestor_context(%s) — starting walk", node_id)
    while current_id and current_id not in visited:
        visited.add(current_id)
        node = graph.node_sync(current_id)
        if node is None:
            _ctx_log.warning("  %s: node_sync returned None", current_id)
            break
        ntype = (node.node_type or "").upper()
        has_content = bool(node.content)
        _ctx_log.info(
            "  %s: type=%s parent=%s has_content=%s content_len=%d",
            current_id,
            ntype,
            node.parent_id,
            has_content,
            len(node.content) if node.content else 0,
        )
        if ntype in _SKIP_ANCESTOR_CONTENT:
            # Include as breadcrumb only — title, not content
            title = node.title or node.node_id
            parts.append(f"[{ntype} {node.node_id}] {title}")
        elif node.content:
            parts.append(f"[{ntype} {node.node_id}]\n{node.content}")
        current_id = getattr(node, "parent_id", None)

    _ctx_log.info("  result: %d parts, %d chars", len(parts), sum(len(p) for p in parts))
    parts.reverse()
    return "\n\n---\n\n".join(parts) if parts else ""


def build_trace_to_context(graph: Any, node_id: str) -> str:
    """Build context from a node's trace_to targets.

    Fails loud rather than silently falling back to an ancestor walk, so
    unresolved trace_to references surface as real errors. If trace_to is
    empty, returns an empty string (caller decides what to do). If trace_to
    is non-empty but resolves to nothing (missing refs or empty content),
    raises RuntimeError with the unresolved IDs.
    """
    node = graph.node_sync(node_id)
    if node is None:
        return ""
    trace_ids: list[str] = node.trace_to or []
    if not trace_ids:
        return ""
    parts: list[str] = []
    unresolved: list[str] = []
    for ref_id in trace_ids:
        ref = graph.node_sync(ref_id)
        if ref and ref.content:
            parts.append(f"[{ref.node_type.upper()} {ref.node_id}]\n{ref.content}")
        else:
            unresolved.append(ref_id)
    if not parts:
        raise RuntimeError(
            f"Node {node_id} has trace_to={trace_ids} but all references are "
            f"missing or empty: {unresolved}. Emit a STALE_TRACE_TO gap instead "
            f"of silently falling back."
        )
    return "\n\n---\n\n".join(parts)


def build_sibling_req_context(graph: Any, node_id: str) -> str:
    """Return a formatted list of sibling requirements for semantic duplicate checking."""
    node = graph.node_sync(node_id)
    if node is None or not node.parent_id:
        return ""
    siblings = graph.children_sync(node.parent_id)
    lines = []
    for s in siblings:
        if s.node_id != node_id and s.node_type == node.node_type and s.content:
            lines.append(f"  [{s.node_id}] {s.content.strip()}")
    if not lines:
        return ""
    return "SIBLING REQUIREMENTS (same parent — check for semantic duplicates):\n" + "\n".join(
        lines
    )


def build_all_peers_context(graph: Any, node_id: str, node_type: str) -> str:
    """Return same-type peer nodes as context for semantic dedup."""
    node = graph.node_sync(node_id)
    all_nodes = [
        n
        for n in graph.all_nodes()
        if n.node_type == node_type and n.node_id != node_id and n.content
    ]

    # CASE types: only compare within the same node_type
    if node_type in ("CASE_HLR", "CASE_LLR") and node is not None:
        all_nodes = [n for n in all_nodes if n.node_type == node.node_type]

    peers = sorted(all_nodes, key=lambda n: n.node_id)
    if not peers:
        return ""
    lines: list[str] = []
    for p in peers:
        trace = getattr(p, "trace_to", None) or []
        prefix = f"trace_to={trace} | " if trace else ""
        lines.append(f"  [{p.node_id}] {prefix}{p.content.strip()}")
    return f"ALL {node_type} REQUIREMENTS (check for semantic duplicates):\n" + "\n".join(lines)


def _build_shallow_req_context(graph: Any, node_id: str) -> str:
    """Build minimal context for UNTESTED gaps: requirement + its parent only.

    Avoids walking up to DOCUMENT (which can be 20k+ chars) since the agent
    only needs the requirement text and its parent HLR/PARA for test case
    authoring.
    """
    parts: list[str] = []
    node = graph.node_sync(node_id)
    if node is None:
        return ""
    if node.content:
        parts.append(f"[{node.node_type.upper()} {node.node_id}]\n{node.content}")
    # Include the parent (HLR for LLR, PARA for HLR) but stop there
    if node.parent_id:
        parent = graph.node_sync(node.parent_id)
        if parent and parent.content:
            parts.insert(0, f"[{parent.node_type.upper()} {parent.node_id}]\n{parent.content}")
    return "\n\n---\n\n".join(parts) if parts else ""


def find_suite_id(graph: Any) -> str:
    """Return the first SUITE node ID, or empty string if none exists."""
    for n in graph.all_nodes():
        if n.node_type == "SUITE":
            suite_id: str = n.node_id
            return suite_id
    return ""


def build_existing_cases_context(graph: Any, case_type: str) -> str:
    """Return full list of existing CASE_HLR or CASE_LLR nodes with full content.

    The agent must decide create-vs-reuse; that decision requires the full
    objective/steps, not just a title. No truncation.
    """
    expected_type = "CASE_HLR" if case_type == "hlr" else "CASE_LLR"
    cases = [n for n in graph.all_nodes() if n.node_type == expected_type]
    if not cases:
        return ""
    cases.sort(key=lambda n: n.node_id)
    lines: list[str] = []
    for c in cases:
        trace = c.trace_to or []
        title = (c.title or "").strip()
        content = (c.content or "").strip()
        lines.append(f"[{c.node_id}] trace_to={trace} title={title!r}\n{content}")
    header = f"EXISTING {case_type.upper()} CASE NODES ({len(cases)} total)"
    return f"\n\n{header}:\n" + "\n\n---\n\n".join(lines)


def build_existing_llrs_context(graph: Any) -> str:
    """Return full list of all LLR nodes with their parent HLR and full content.

    Provided inline so the agent can match an HLR to an existing LLR
    instead of always creating new ones. No truncation — reparent decisions
    hinge on the full requirement text.
    """
    llrs = [n for n in graph.all_nodes() if n.node_type == "LLR" and n.content]
    if not llrs:
        return ""
    llrs.sort(key=lambda n: n.node_id)
    lines: list[str] = []
    for n in llrs:
        content = n.content.strip()
        lines.append(f"[{n.node_id}] parent={n.parent_id}\n{content}")
    header = f"EXISTING LLR NODES ({len(llrs)} total)"
    return f"\n\n{header}:\n" + "\n\n---\n\n".join(lines)


def build_all_hlrs_context(graph: Any) -> str:
    """Return full list of all HLR nodes for the requirements landscape. No truncation."""
    hlrs = [n for n in graph.all_nodes() if n.node_type == "HLR" and n.content]
    if not hlrs:
        return ""
    hlrs.sort(key=lambda n: n.node_id)
    lines: list[str] = []
    for n in hlrs:
        content = n.content.strip()
        lines.append(f"[{n.node_id}] parent={n.parent_id}\n{content}")
    header = f"ALL HLR REQUIREMENTS ({len(hlrs)} total)"
    return f"\n\n{header}:\n" + "\n\n---\n\n".join(lines)


def build_architecture_context(graph: Any) -> str:
    """Return the ARCHITECTURE node content, or empty string if absent."""
    for n in graph.all_nodes():
        if n.node_type == "ARCHITECTURE" and n.content:
            return f"[ARCHITECTURE {n.node_id}]\n{n.content}"
    return ""


def _find_architecture_node(graph: Any) -> Any:
    """Return the first ARCHITECTURE node (or None) — used for heading extraction."""
    for n in graph.all_nodes():
        if n.node_type == "ARCHITECTURE" and n.content:
            return n
    return None


def build_traced_hlrs_for_module(graph: Any, module_id: str) -> str:
    """Return HLRs that the MODULE traces to (its trace_to references)."""
    module = graph.node_sync(module_id)
    if module is None:
        return ""
    trace_ids: list[str] = module.trace_to or []
    parts: list[str] = []
    for ref_id in trace_ids:
        ref = graph.node_sync(ref_id)
        if ref and ref.node_type == "HLR" and ref.content:
            parts.append(f"[HLR {ref.node_id}]\n{ref.content}")
    if not parts:
        return ""
    header = f"TRACED HLR REQUIREMENTS ({len(parts)} total)"
    return f"\n\n{header}:\n" + "\n\n---\n\n".join(parts)


def build_all_llrs_context(graph: Any) -> str:
    """Full content of every LLR — for SUITE authoring and landscape views."""
    llrs = [n for n in graph.all_nodes() if n.node_type == "LLR" and n.content]
    if not llrs:
        return ""
    llrs.sort(key=lambda n: n.node_id)
    lines = [f"[{n.node_id}] parent={n.parent_id}\n{n.content.strip()}" for n in llrs]
    header = f"ALL LLR REQUIREMENTS ({len(llrs)} total)"
    return f"\n\n{header}:\n" + "\n\n---\n\n".join(lines)


def build_peer_contracts_context(graph: Any, exclude_module_id: str = "") -> str:
    """Full content of every CONTRACT except the one under ``exclude_module_id``.

    For use when writing a new CONTRACT — gives the author every sibling
    CONTRACT's signatures so API style stays consistent.
    """
    contracts = [n for n in graph.all_nodes() if n.node_type == "CONTRACT" and n.content]
    if exclude_module_id:
        contracts = [c for c in contracts if c.parent_id != exclude_module_id]
    if not contracts:
        return ""
    contracts.sort(key=lambda n: n.node_id)
    lines = [
        f"[CONTRACT {c.node_id}] module={c.parent_id}\n{c.content.strip()}"
        for c in contracts
    ]
    header = f"SIBLING CONTRACTS ({len(contracts)} total — for API-style consistency)"
    return f"\n\n{header}:\n" + "\n\n---\n\n".join(lines)


def build_design_for_llr(graph: Any, llr_id: str) -> str:
    """Full DESIGN content for the class that implements ``llr_id``.

    Reverse-lookup: LLR → parent HLR → MODULE (via nodes_tracing_to) → DESIGN
    whose trace_to includes this LLR, or any DESIGN sibling under the MODULE.
    """
    llr = graph.node_sync(llr_id)
    if llr is None or not llr.parent_id:
        return ""
    hlr_id = llr.parent_id
    module_ids = graph.nodes_tracing_to(hlr_id, source_type="MODULE")
    if not module_ids:
        return ""
    module_id = module_ids[0]
    children = graph.children_sync(module_id)
    designs = [c for c in children if c.node_type == "DESIGN" and c.content]
    if not designs:
        return ""
    owning = [d for d in designs if llr_id in (d.trace_to or [])]
    targets = owning if owning else designs
    lines = [
        f"[DESIGN {d.node_id}] module={module_id} trace_to={d.trace_to}\n{d.content.strip()}"
        for d in targets
    ]
    tag = "OWNING" if owning else "MODULE-SIBLING"
    header = f"{tag} DESIGN(s) FOR LLR {llr_id} ({len(targets)} total)"
    return f"\n\n{header}:\n" + "\n\n---\n\n".join(lines)


def build_cases_for_requirement(graph: Any, req_id: str) -> str:
    """Full content of every CASE that traces to ``req_id``."""
    cases = [
        n for n in graph.all_nodes()
        if n.node_type in ("CASE_HLR", "CASE_LLR")
        and req_id in (n.trace_to or [])
        and n.content
    ]
    if not cases:
        return ""
    cases.sort(key=lambda n: n.node_id)
    lines = [
        f"[{c.node_type} {c.node_id}] trace_to={c.trace_to}\n{c.content.strip()}"
        for c in cases
    ]
    header = f"EXISTING CASES FOR {req_id} ({len(cases)} total)"
    return f"\n\n{header}:\n" + "\n\n---\n\n".join(lines)


def build_sibling_paras_context(graph: Any, para_id: str) -> str:
    """Full content of every PARA sharing the same parent as ``para_id``.

    Rationale, constraint, and non_functional siblings often qualify a
    functional paragraph (e.g. a latency NFR next to a behaviour PARA).
    Excludes the target PARA itself.
    """
    target = graph.node_sync(para_id)
    if target is None or not target.parent_id:
        return ""
    siblings = [
        n for n in graph.children_sync(target.parent_id)
        if n.node_type == "PARA" and n.node_id != para_id and n.content
    ]
    if not siblings:
        return ""
    siblings.sort(key=lambda n: n.node_id)
    lines: list[str] = []
    for s in siblings:
        ptype = (s.properties or {}).get("para_type", "")
        lines.append(f"[PARA {s.node_id}] type={ptype}\n{s.content.strip()}")
    header = f"SIBLING PARAGRAPHS under {target.parent_id} ({len(siblings)} total)"
    return f"\n\n{header}:\n" + "\n\n---\n\n".join(lines)


def build_document_digest(graph: Any) -> str:
    """Whitepaper digest: full content of non-functional PARAs only.

    Includes PARAs where ``para_type`` is ``rationale``, ``constraint``, or
    ``non_functional``. Excludes ``functional`` PARAs (those are already
    summarised by HLRs) and ``heading`` PARAs. Full content throughout.
    """
    keep_types = {"rationale", "constraint", "non_functional"}
    paras = [
        n for n in graph.all_nodes()
        if n.node_type == "PARA"
        and n.content
        and (n.properties or {}).get("para_type", "") in keep_types
    ]
    if not paras:
        return ""
    paras.sort(key=lambda n: n.node_id)
    lines = []
    for p in paras:
        ptype = (p.properties or {}).get("para_type", "")
        lines.append(f"[PARA {p.node_id}] type={ptype}\n{p.content.strip()}")
    header = f"WHITEPAPER DIGEST — rationale/constraint/NFR PARAs ({len(paras)} total)"
    return f"\n\n{header}:\n" + "\n\n---\n\n".join(lines)


def build_all_modules_context(graph: Any) -> str:
    """Return full list of all MODULE nodes with full content. No truncation."""
    modules = [n for n in graph.all_nodes() if n.node_type == "MODULE" and n.content]
    if not modules:
        return ""
    modules.sort(key=lambda n: n.node_id)
    lines: list[str] = []
    for n in modules:
        trace = n.trace_to or []
        content = n.content.strip()
        lines.append(f"[{n.node_id}] trace_to={trace}\n{content}")
    header = f"ALL MODULE NODES ({len(modules)} total)"
    return f"\n\n{header}:\n" + "\n\n---\n\n".join(lines)


def build_module_design_context(graph: Any, llr_id: str) -> str:
    """Build MODULE + CONTRACT + existing DESIGN context for an LLR's UNDESIGNED gap.

    Finds the MODULE owning this LLR (via trace_to on the parent HLR),
    then collects the MODULE content (class plan), its CONTRACT, and
    all existing DESIGN siblings so the agent can consolidate.
    """
    llr = graph.node_sync(llr_id)
    if llr is None or not llr.parent_id:
        return ""
    hlr_id = llr.parent_id

    # Find the MODULE whose trace_to includes this HLR
    module_ids = graph.nodes_tracing_to(hlr_id, source_type="MODULE")
    if not module_ids:
        return ""
    module = graph.node_sync(module_ids[0])
    if module is None:
        return ""

    parts: list[str] = []
    parts.append(f"[MODULE {module.node_id}]\n{module.content or '(no content)'}")

    # Collect CONTRACT and existing DESIGNs under this MODULE
    children = graph.children_sync(module.node_id)
    for child in children:
        if child.node_type == "CONTRACT" and child.content:
            parts.append(f"[CONTRACT {child.node_id}]\n{child.content}")
    designs = [c for c in children if c.node_type == "DESIGN" and c.content]
    for d in designs:
        trace_str = f" (trace_to: {d.trace_to})" if d.trace_to else ""
        parts.append(f"[DESIGN {d.node_id}]{trace_str}\n{d.content}")

    header = f"OWNING MODULE AND EXISTING DESIGNS ({len(designs)} design(s) exist):"
    return header + "\n\n" + "\n\n---\n\n".join(parts)


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
