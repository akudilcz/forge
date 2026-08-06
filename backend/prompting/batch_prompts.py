"""Batch prompt builders for phases with competing gaps.

Each function assembles a single prompt that presents ALL gaps + ALL relevant
nodes so the agent can make a globally optimal assignment in one pass.
"""

from __future__ import annotations

import json
from typing import Any

from backend.prompting.task_prompts_authoring import (
    CASE_CONTRACT_ENCODING,
    EARS_PATTERNS,
    IMPLEMENTABLE_SPEC_LITMUS,
    NORMATIVE_MUST_CAPTURE,
    REQUIREMENT_PROVENANCE_FIELDS,
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
        f"UNCOVERED PARAGRAPHS ({len(uncovered_paras)} — each must end up "
        f"COVERED by an HLR or CLASSIFIED non-normative):\n"
        f"{para_lines}\n\n"
        "FOR EACH uncovered PARA above — COVER OR CLASSIFY. Do exactly ONE of:\n"
        "  A) Reparent an existing HLR that already captures the paragraph's\n"
        "     requirement:\n"
        "       graph_reparent_node(node_id=<hlr_id>, parent_id=<para_id>)\n"
        "  B) Create a new HLR using derive_requirement to generate the text:\n"
        "       derive_requirement(parent_content=<PARA content text>, level=hlr)\n"
        "     Then persist the text AND the tool's verification_method /\n"
        "     derived / derived_rationale outputs:\n"
        "       graph_add_node(node_type=HLR, parent_id=<para_id>,\n"
        "         content=<derived req_text>, title='3-5 words',\n"
        "         properties='{\"verification_method\": \"<tool output>\",\n"
        "                      \"derived\": <tool output>,\n"
        "                      \"derived_rationale\": \"<tool output, when derived>\"}')\n"
        "  C) Classify a genuinely NON-NORMATIVE paragraph instead of forcing\n"
        "     an HLR onto it:\n"
        "       graph_update_node(node_id=<para_id>,\n"
        "         properties='{\"non_normative\": true,\n"
        "                      \"non_normative_rationale\": \"<reason>\"}')\n"
        "     <reason> must be ONE of:\n"
        "       background/context      — scene-setting prose, no obligation\n"
        "       duplicate-of-<PARA-id>  — restates a sibling paragraph whose\n"
        "                                 HLR already carries the obligation\n"
        "       example/illustration    — worked example of an obligation\n"
        "                                 stated elsewhere\n"
        "       meta/document-structure — text about the document itself\n\n"
        "RULES:\n"
        "- Duplicate requirements are a recognised requirements DEFECT class\n"
        "  (EARS guidance). NEVER invent a near-duplicate HLR for a paragraph\n"
        "  that merely restates a sibling — classify it duplicate-of-<PARA-id>\n"
        "  instead, naming the sibling PARA that owns the obligation.\n"
        "- Classify ONLY when the paragraph states no separately-testable\n"
        "  obligation. If it adds even one new obligation, cover it (A or B).\n"
        "- An HLR can only have ONE parent. Do NOT move an HLR if it would\n"
        "  leave its current PARA with zero HLRs — create a new HLR instead.\n"
        "- Each HLR content must be a single ATOMIC sentence in one of the\n"
        "  EARS patterns. One testable obligation per HLR.\n"
        f"{EARS_PATTERNS}"
        f"{REQUIREMENT_PROVENANCE_FIELDS}"
        "- A PARA may contain SEVERAL obligations — create one HLR per\n"
        "  obligation, never a single summary HLR for the paragraph.\n"
        f"{NORMATIVE_MUST_CAPTURE}"
        "- Work through ALL uncovered PARAs before stopping.\n"
        "- Pass the actual PARA content text to derive_requirement, not a node ID."
    )

    return static + dynamic


def build_batch_phase7_prompt(
    unrefined_hlrs: list[dict[str, Any]],
    module: dict[str, Any],
    contract: dict[str, Any] | None,
    all_llrs: list[dict[str, Any]],
    designs: list[dict[str, Any]],
) -> str:
    """Phase 7 (U8): fused implementable-spec authoring for one MODULE.

    One batch pass emits, for each uncovered HLR, its LLR(s) AND each LLR's
    DESIGN coverage in the same response — both trace edges written at
    creation (LLR→HLR, DESIGN→LLR with parent MODULE). HLR→LLR→DESIGN is a
    single refinement level (CAST-15), so splitting LLR derivation and
    DESIGN creation into two passes produced an artificial second
    refinement over the same material; phase 8 now only verifies.

    Sends the MODULE's CONTRACT in full — prose plus the structured
    ``public_api`` record (obligation fields included) — because the litmus
    for a valid LLR is direct implementability from its text + CONTRACT
    alone. Existing DESIGNs are sent with full content so reuse decisions
    are grounded in actual method signatures, not titles.
    """
    module_id = module["node_id"]
    hlr_lines = _format_node_list(unrefined_hlrs, ["node_id", "title", "content"])
    grouped_llrs = _group_llrs_by_parent(all_llrs)
    design_lines = _format_node_list(
        designs, ["node_id", "title", "trace_to", "content"],
    )
    contract_block = _contract_record_block(contract)

    # ── Static prefix (cacheable across chunk retries) ──────────────────────
    static = (
        f"You are authoring the implementable specification for MODULE "
        f"[{module_id}]: for each uncovered HLR you write its LLR(s) AND "
        f"each LLR's DESIGN coverage in this same response.\n\n"
        f"MODULE [{module_id}] — {module.get('title', '')}:\n"
        f"{module.get('content', '')}\n"
        f"{contract_block}"
        f"EXISTING LLRs grouped by parent HLR ({len(all_llrs)} total — "
        f"available to re-parent):\n"
        f"{grouped_llrs}\n\n"
        f"EXISTING DESIGNs with full content ({len(designs)}):\n"
        f"{design_lines}\n\n"
    )

    # ── Dynamic suffix (changes each attempt) ───────────────────────────────
    dynamic = (
        f"HLRs NEEDING REFINEMENT ({len(unrefined_hlrs)}):\n"
        f"{hlr_lines}\n\n"
        "FOR EACH HLR above, author BOTH artifact levels:\n"
        "STEP 1 — LLR(s). Do ONE of:\n"
        "  A) Reparent existing LLR(s) that refine it:\n"
        "       graph_reparent_node(node_id=<llr_id>, parent_id=<hlr_id>)\n"
        "  B) Create new LLR(s), one per distinct obligation:\n"
        '       graph_add_node(node_type=LLR, parent_id=<hlr_id>,\n'
        '         trace_to=["<hlr_id>"], content=\'The system shall ...\',\n'
        "         title='3-5 words', properties=<provenance, below>)\n"
        "STEP 2 — DESIGN coverage for EVERY LLR from step 1 (new or\n"
        "reparented), in this same response:\n"
        "  PREFERRED: an existing DESIGN above implements the class this LLR\n"
        "    maps to — append to its trace_to:\n"
        "    graph_add_traces(node_id=<design_id>, trace_to=[<llr_id>])\n"
        "  ONLY when the MODULE's class plan names a class with no matching\n"
        "  DESIGN yet:\n"
        f"    graph_add_node(node_type=DESIGN, parent_id={module_id},\n"
        "      trace_to=[<llr_id>, ...], title='3-5 words',\n"
        "      content=<class name + method signatures + responsibilities>)\n"
        "  Use the node_id RETURNED by each LLR graph_add_node call in the\n"
        "  DESIGN's trace_to — never invent LLR ids.\n\n"
        "RULES:\n"
        f"{IMPLEMENTABLE_SPEC_LITMUS}"
        "- Do NOT move an LLR if it is the ONLY child of its current parent.\n"
        "- Each LLR must be ATOMIC — ONE obligation in one EARS pattern.\n"
        f"{EARS_PATTERNS}"
        f"{REQUIREMENT_PROVENANCE_FIELDS}"
        "- Create one LLR per distinct obligation. Do NOT under-decompose.\n"
        "- Align LLRs and DESIGN method signatures to the CONTRACT's\n"
        "  public_api entries where they exist.\n"
        "- The number of DESIGNs must NOT exceed the classes in the MODULE's\n"
        "  class plan. Default is ONE class per MODULE — one DESIGN covering\n"
        "  all its LLRs. Creating a new DESIGN when a matching one exists is\n"
        "  WRONG.\n"
        "- Do NOT call graph_read. All context is above.\n"
        "- Work through ALL HLRs before stopping; leave NO step-1 LLR\n"
        "  without DESIGN coverage."
    )

    return static + dynamic


def _group_llrs_by_parent(all_llrs: list[dict[str, Any]]) -> str:
    """Render LLRs nested under their parent HLR for contextual reading."""
    by_parent: dict[str, list[dict[str, Any]]] = {}
    for llr in all_llrs:
        by_parent.setdefault(llr.get("parent_id", ""), []).append(llr)
    blocks: list[str] = []
    for pid in sorted(by_parent):
        header = f"  LLRs under HLR {pid}:" if pid else "  LLRs with no parent:"
        block = _format_node_list(by_parent[pid], ["node_id", "title", "content"])
        blocks.append(f"{header}\n{block}")
    return "\n\n".join(blocks) if blocks else "  (none)"


def _contract_record_block(contract: dict[str, Any] | None) -> str:
    """Render the CONTRACT prose plus its structured public_api record."""
    if not contract:
        return "\n"
    block = (
        f"\nCONTRACT [{contract['node_id']}]:\n"
        f"{contract.get('content', '')}\n"
    )
    public_api = (contract.get("properties") or {}).get("public_api")
    if public_api:
        block += (
            "\nCONTRACT RECORD — structured public_api (obligation fields\n"
            "included; every LLR must be implementable against these\n"
            "signatures, and every raises/postcondition obligation must be\n"
            "carried by an LLR, never buried in a DESIGN):\n"
            f"{json.dumps(public_api, indent=2)}\n"
        )
    return block + "\n"


def build_batch_phase10_prompt(
    untested_hlrs: list[dict[str, Any]],
    untested_llrs: list[dict[str, Any]],
    suite: dict[str, Any] | None,
    existing_cases: list[dict[str, Any]],
    contract_records: list[dict[str, Any]],
) -> str:
    """Phase 10: write CASE_HLR / CASE_LLR in a single batch.

    Presents all HLRs + LLRs without a test case plus the SUITE strategy,
    existing CASEs, and every CONTRACT's structured ``public_api`` records
    (specs/13) so the agent can enumerate raises entries and postconditions
    into cases and emit one ``multi_graph_write`` with every new case.

    The SUITE is REQUIRED structured input (U9, specs/03 Phases 9-10): its
    content anchors the static prefix and its id parents every new CASE.
    Building the prompt without one raises — a degraded prompt with an
    empty parent id is never acceptable.

    Each requirement line carries its ``verification_method`` and derived
    status (U4, specs/13) so the author can honour the method — Test
    needs an executable case; Analysis / Inspection / Demonstration get
    a case documenting the obligation.
    """
    if not suite:
        raise ValueError(
            "build_batch_phase10_prompt requires the SUITE node — case "
            "authoring without its strategy parent is a missing precondition"
        )
    marking = ["verification_method", "derived", "derived_rationale"]
    hlr_lines = _format_node_list(
        [_flatten_requirement_marking(n) for n in untested_hlrs],
        ["node_id", "title", *marking, "content"],
    )
    llr_lines = _format_node_list(
        [_flatten_requirement_marking(n) for n in untested_llrs],
        ["node_id", "parent_id", "title", *marking, "content"],
    )

    suite_id = suite["node_id"]
    suite_block = (
        f"SUITE [{suite_id}] — test strategy (parent for every new CASE):\n"
        f"{suite['content']}\n\n"
    )

    existing_block = ""
    if existing_cases:
        existing_block = (
            f"EXISTING CASES ({len(existing_cases)}) — DO NOT duplicate; "
            f"they already cover these trace_to targets:\n"
            f"{_format_node_list(existing_cases, ['node_id', 'node_type', 'trace_to', 'title'])}\n\n"
        )

    records_block = ""
    if contract_records:
        rendered = "\n\n".join(
            f"[{r['node_id']}] module={r['module_id']}\n"
            f"{json.dumps(r['public_api'], indent=2)}"
            for r in contract_records
        )
        records_block = (
            "CONTRACT RECORDS — for the requirement's module, author ONE "
            "case per raises entry (If <when>, then raises <cls>) and ONE "
            "case per stated postcondition:\n"
            f"{rendered}\n\n"
        )

    return (
        "You are authoring test cases (CASE_HLR + CASE_LLR) for every untested\n"
        "requirement. Emit the new cases across a small number of "
        "multi_graph_write calls (one call is ideal; 2–5 is fine if it helps\n"
        "you reason about groups of cases). Do NOT fall back to per-case "
        "graph_add_node calls.\n\n"
        f"{suite_block}"
        f"{existing_block}"
        f"{records_block}"
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


def _flatten_requirement_marking(node: dict[str, Any]) -> dict[str, Any]:
    """Copy U4 marking properties up to top-level keys for line rendering.

    ``_format_node_list`` reads flat fields, so ``verification_method`` /
    ``derived`` / ``derived_rationale`` are lifted out of ``properties``
    when present. Node dicts without a ``properties`` key pass through
    unchanged (legacy callers).
    """
    if "properties" not in node:
        return node
    props = node["properties"] or {}
    flat = dict(node)
    for key in ("verification_method", "derived", "derived_rationale"):
        if key in props:
            flat[key] = props[key]
    return flat


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
