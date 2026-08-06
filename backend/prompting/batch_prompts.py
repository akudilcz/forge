"""Batch prompt builders for phases with competing gaps.

Each function assembles a single prompt that presents ALL gaps + ALL relevant
nodes so the agent can make a globally optimal assignment in one pass.
"""

from __future__ import annotations

from typing import Any

from backend.prompting.task_prompts_authoring import (
    CASE_CONTRACT_ENCODING,
    NORMATIVE_MUST_CAPTURE,
)


def build_batch_phase3_prompt(
    uncovered_paras: list[dict[str, Any]],
    all_hlrs: list[dict[str, Any]],
    all_llrs: list[dict[str, Any]] | None = None,
) -> str:
    """Phase 3: assign HLRs to uncovered PARAs.

    Prompt is laid out as [static graph snapshot] + [dynamic this-attempt
    uncovered PARAs] so retries benefit from prompt caching when the
    snapshot is unchanged.
    """
    all_llrs = all_llrs or []
    hlr_lines = _format_node_list(all_hlrs, ["node_id", "parent_id", "title", "content"])
    llr_lines = _format_node_list(all_llrs, ["node_id", "parent_id", "title", "content"])
    para_lines = _format_para_list(uncovered_paras)

    # ── Static prefix (cacheable across retries) ────────────────────────────
    static = (
        "You are assigning HLR requirements to paragraphs.\n\n"
        f"ALL EXISTING HLRs ({len(all_hlrs)}):\n"
        f"{hlr_lines}\n\n"
        f"ALL EXISTING LLRs ({len(all_llrs)} — DO NOT create an HLR that duplicates\n"
        f"an obligation already captured by an LLR; Phase 7 will reparent those):\n"
        f"{llr_lines}\n\n"
    )

    # ── Dynamic suffix (changes each attempt) ───────────────────────────────
    dynamic = (
        f"UNCOVERED PARAGRAPHS ({len(uncovered_paras)} — each needs at least one HLR):\n"
        f"{para_lines}\n\n"
        "FOR EACH uncovered PARA above, do ONE of:\n"
        "  A) Reparent an existing HLR that already captures the paragraph's\n"
        "     requirement:\n"
        "       graph_reparent_node(node_id=<hlr_id>, parent_id=<para_id>)\n"
        "  B) Create a new HLR using derive_requirement to generate the text:\n"
        "       derive_requirement(parent_content=<PARA content text>, level=hlr)\n"
        "     Then persist:\n"
        "       graph_add_node(node_type=HLR, parent_id=<para_id>,\n"
        "         content=<derived text>, title='3-5 words')\n\n"
        "RULES:\n"
        "- An HLR can only have ONE parent. Do NOT move an HLR if it would\n"
        "  leave its current PARA with zero HLRs — create a new HLR instead.\n"
        "- Each HLR content must be a single ATOMIC sentence starting with\n"
        "  'The system shall '. One testable obligation per HLR.\n"
        "- A PARA may contain SEVERAL obligations — create one HLR per\n"
        "  obligation, never a single summary HLR for the paragraph.\n"
        f"{NORMATIVE_MUST_CAPTURE}"
        "- Work through ALL uncovered PARAs before stopping.\n"
        "- Pass the actual PARA content text to derive_requirement, not a node ID."
    )

    return static + dynamic


def build_batch_phase5_prompt(
    unassigned_hlrs: list[dict[str, Any]],
    modules: list[dict[str, Any]],
    architecture: dict[str, Any] | None,
    contracts: list[dict[str, Any]] | None = None,
) -> str:
    """Phase 5: assign HLRs to MODULEs.

    Includes MODULE content in full (responsibilities + class plan are the
    real signal) and any CONTRACTs that already exist (responsibilities are
    sharper in contracts). ARCHITECTURE content is included in full — no
    truncation, the packer drops whole sections if over budget.
    """
    contracts = contracts or []
    hlr_lines = _format_node_list(unassigned_hlrs, ["node_id", "title", "content"])
    mod_lines = _format_node_list(modules, ["node_id", "title", "trace_to", "content"])
    ctr_lines = _format_node_list(
        contracts, ["node_id", "parent_id", "title", "content"]
    )
    arch_block = ""
    if architecture:
        arch_block = (
            f"\nARCHITECTURE [{architecture['node_id']}]:\n"
            f"{architecture.get('content', '')}\n"
        )

    return (
        "You are assigning HLRs to MODULEs.\n\n"
        f"UNASSIGNED HLRs ({len(unassigned_hlrs)} — no MODULE traces to them):\n"
        f"{hlr_lines}\n\n"
        f"EXISTING MODULEs with full content ({len(modules)}):\n"
        f"{mod_lines}\n\n"
        f"EXISTING CONTRACTs — authoritative responsibility statements "
        f"({len(contracts)}):\n"
        f"{ctr_lines}\n"
        f"{arch_block}\n"
        "FOR EACH unassigned HLR above:\n"
        "  graph_add_traces(node_id=<module_id>, trace_to=[<hlr_id>])\n\n"
        "Only create a NEW MODULE when no existing MODULE is a semantically\n"
        "good fit for the HLR's concern. Use CONTRACT responsibilities and\n"
        "MODULE content (not just titles) to judge fit.\n"
        "Work through ALL unassigned HLRs before stopping."
    )


def build_batch_phase7_prompt(
    unrefined_hlrs: list[dict[str, Any]],
    all_llrs: list[dict[str, Any]],
    module_contracts: list[dict[str, Any]],
) -> str:
    """Phase 7: derive LLRs from HLRs.

    Sends MODULE and CONTRACT content in FULL — signatures and invariants
    are what LLRs must align to, so title-only would strand agents refining
    blind. LLRs are grouped by parent HLR for contextual reading.
    """
    # Group LLRs by parent_id for nested presentation.
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for llr in all_llrs:
        by_parent.setdefault(llr.get("parent_id", ""), []).append(llr)

    hlr_lines = _format_node_list(
        unrefined_hlrs, ["node_id", "title", "content"],
    )

    grouped_llr_blocks: list[str] = []
    for pid in sorted(by_parent):
        children = by_parent[pid]
        header = f"  LLRs under HLR {pid}:" if pid else "  LLRs with no parent:"
        block = _format_node_list(
            children, ["node_id", "title", "content"],
        )
        grouped_llr_blocks.append(f"{header}\n{block}")

    grouped_llrs = "\n\n".join(grouped_llr_blocks) if grouped_llr_blocks else "  (none)"

    mc_lines = _format_node_list(
        module_contracts,
        ["node_id", "node_type", "parent_id", "title", "trace_to", "content"],
    )

    return (
        "You are deriving LLRs from HLRs.\n\n"
        f"HLRs NEEDING LLRs ({len(unrefined_hlrs)}):\n"
        f"{hlr_lines}\n\n"
        f"EXISTING LLRs grouped by parent HLR ({len(all_llrs)} total — "
        f"available to re-parent):\n"
        f"{grouped_llrs}\n\n"
        f"MODULE + CONTRACT CONTEXT (full content — your LLRs must align with\n"
        f"each CONTRACT's public signatures and invariants):\n{mc_lines}\n\n"
        "FOR EACH HLR above, do ONE of:\n"
        "  A) Reparent existing LLR(s) that refine it:\n"
        "       graph_reparent_node(node_id=<llr_id>, parent_id=<hlr_id>)\n"
        "  B) Create new LLR(s) for uncovered aspects:\n"
        "       graph_add_node(node_type=LLR, parent_id=<hlr_id>,\n"
        "         content='The system shall ...', title='3-5 words')\n\n"
        "RULES:\n"
        "- Do NOT move an LLR if it is the ONLY child of its current parent.\n"
        "- Each LLR must be ATOMIC — one 'The system shall ...' sentence.\n"
        "- Create one LLR per distinct obligation. Do NOT under-decompose.\n"
        "- Align LLRs to CONTRACT signatures where they exist.\n"
        "- Work through ALL HLRs before stopping."
    )


def build_batch_phase8_prompt(
    module: dict[str, Any],
    contract: dict[str, Any] | None,
    undesigned_llrs: list[dict[str, Any]],
    designs: list[dict[str, Any]],
    suite: dict[str, Any] | None = None,
    parent_hlr_cases: list[dict[str, Any]] | None = None,
) -> str:
    """Phase 8: assign LLRs to DESIGNs within one MODULE.

    Sends full content for existing DESIGNs so reuse decisions are grounded
    in actual method signatures, not titles. Also includes SUITE strategy
    and any CASEs already on the parent HLRs so DESIGNs align with test
    coverage intent.
    """
    parent_hlr_cases = parent_hlr_cases or []
    llr_lines = _format_node_list(
        undesigned_llrs, ["node_id", "title", "content"],
    )
    # FULL content for existing DESIGNs — reuse decisions need method signatures,
    # not just titles. This is the single biggest quality lever in Phase 8.
    design_lines = _format_node_list(
        designs, ["node_id", "title", "trace_to", "content"],
    )
    suite_block = ""
    if suite and suite.get("content"):
        suite_block = (
            f"\nSUITE [{suite.get('node_id', '')}] — test strategy:\n"
            f"{suite['content']}\n"
        )
    cases_block = ""
    if parent_hlr_cases:
        cases_block = "\nCASES ALREADY ON PARENT HLRs (align DESIGNs to their coverage):\n"
        cases_block += _format_node_list(
            parent_hlr_cases, ["node_id", "node_type", "trace_to", "content"],
        )
        cases_block += "\n"
    contract_block = ""
    if contract:
        contract_block = (
            f"\nCONTRACT [{contract['node_id']}]:\n"
            f"{contract.get('content', '')}\n"
        )

    return (
        f"You are assigning LLRs to DESIGNs within MODULE [{module['node_id']}].\n\n"
        f"MODULE [{module['node_id']}] — {module.get('title', '')}:\n"
        f"{module.get('content', '')}\n"
        f"{contract_block}"
        f"{suite_block}"
        f"{cases_block}\n"
        f"UNDESIGNED LLRs ({len(undesigned_llrs)} — need a DESIGN):\n"
        f"{llr_lines}\n\n"
        f"EXISTING DESIGNs with full content ({len(designs)}):\n"
        f"{design_lines}\n\n"
        "FOR EACH LLR above:\n"
        "  PREFERRED: if an existing DESIGN above implements the class this LLR\n"
        "    maps to, add to its trace_to:\n"
        "    graph_add_traces(node_id=<design_id>, trace_to=[<llr_id>])\n"
        "  ONLY when the class plan names a class with no matching DESIGN:\n"
        "    graph_add_node(node_type=DESIGN, parent_id=<module_id>, ...)\n\n"
        "RULES:\n"
        "- Number of DESIGNs must NOT exceed classes in the MODULE's class plan.\n"
        "- Align method signatures with the CONTRACT and test steps in CASES.\n"
        "- Do NOT call graph_read. All context is above.\n"
        "- Work through ALL LLRs before stopping."
    )


def build_batch_phase10_prompt(
    untested_hlrs: list[dict[str, Any]],
    untested_llrs: list[dict[str, Any]],
    suite: dict[str, Any] | None,
    existing_cases: list[dict[str, Any]],
) -> str:
    """Phase 10: write CASE_HLR / CASE_LLR in a single batch.

    Presents all HLRs + LLRs without a test case plus the SUITE strategy and
    existing CASEs so the agent can emit one ``multi_graph_write`` with every
    new case in a single tool call.
    """
    hlr_lines = _format_node_list(untested_hlrs, ["node_id", "title", "content"])
    llr_lines = _format_node_list(untested_llrs, ["node_id", "parent_id", "title", "content"])

    suite_block = ""
    suite_id = ""
    if suite:
        suite_id = suite.get("node_id", "")
        suite_block = (
            f"SUITE [{suite_id}] — test strategy (parent for every new CASE):\n"
            f"{suite.get('content', '')}\n\n"
        )

    existing_block = ""
    if existing_cases:
        existing_block = (
            f"EXISTING CASES ({len(existing_cases)}) — DO NOT duplicate; "
            f"they already cover these trace_to targets:\n"
            f"{_format_node_list(existing_cases, ['node_id', 'node_type', 'trace_to', 'title'])}\n\n"
        )

    return (
        "You are authoring test cases (CASE_HLR + CASE_LLR) for every untested\n"
        "requirement. Emit the new cases across a small number of "
        "multi_graph_write calls (one call is ideal; 2–5 is fine if it helps\n"
        "you reason about groups of cases). Do NOT fall back to per-case "
        "graph_add_node calls.\n\n"
        f"{suite_block}"
        f"{existing_block}"
        f"HLRs NEEDING A CASE_HLR ({len(untested_hlrs)}):\n{hlr_lines}\n\n"
        f"LLRs NEEDING A CASE_LLR ({len(untested_llrs)}):\n{llr_lines}\n\n"
        "For each HLR above, add one CASE_HLR:\n"
        f"  {{\"operation\":\"add_node\", \"node_type\":\"CASE_HLR\",\n"
        f"   \"parent_id\":\"{suite_id}\", \"trace_to\":[\"<hlr_id>\"],\n"
        f"   \"title\":\"<3-5 words>\", \"content\":\"<Given/When/Then steps "
        f"verifying the HLR behaviour end-to-end>\"}}\n\n"
        "For each LLR above, add one CASE_LLR:\n"
        f"  {{\"operation\":\"add_node\", \"node_type\":\"CASE_LLR\",\n"
        f"   \"parent_id\":\"{suite_id}\", \"trace_to\":[\"<llr_id>\"],\n"
        f"   \"title\":\"<3-5 words>\", \"content\":\"<Arrange/Act/Assert steps "
        f"exercising the LLR's specific invariant>\"}}\n\n"
        "RULES:\n"
        "- Exactly ONE CASE_HLR per untested HLR; exactly ONE CASE_LLR per untested LLR.\n"
        "- Every CASE must have trace_to pointing at its target requirement.\n"
        "- Every CASE's parent_id must be the SUITE node shown above.\n"
        "- Use multi_graph_write with an operations array — ONE tool call writes\n"
        "  every case at once. Do NOT make multiple graph_add_node calls.\n"
        "- Titles must be distinct, concrete 3-5 word noun phrases.\n"
        f"{CASE_CONTRACT_ENCODING}"
    )


def _format_para_list(paras: list[dict[str, Any]]) -> str:
    """Format PARAs with full content. No truncation — agent derives from full text."""
    if not paras:
        return "  (none)"
    lines: list[str] = []
    for p in paras:
        nid = p.get("node_id", "")
        content = p.get("content", "")
        lines.append(f"  [{nid}]\n    {content}")
    return "\n\n".join(lines)


def _format_node_list(nodes: list[dict[str, Any]], fields: list[str]) -> str:
    """Format a list of node dicts into readable lines. Full content — no truncation."""
    if not nodes:
        return "  (none)"
    lines: list[str] = []
    for n in nodes:
        parts: list[str] = []
        for f in fields:
            val = n.get(f, "")
            if val:
                parts.append(f"{f}={val}" if f != "node_id" else str(val))
        lines.append(f"  [{parts[0]}] {' | '.join(parts[1:])}" if parts else "")
    return "\n".join(lines)
