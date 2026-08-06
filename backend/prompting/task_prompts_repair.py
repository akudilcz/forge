"""Prompt templates that repair or refine existing graph nodes.

Per-gap-type helpers for quality gaps — malformed/non-atomic requirements,
titles, duplicates, staleness, contract violations, and trace repairs.
Extracted from ``task_prompts.py``, which re-exports every helper so
existing import sites and patch targets keep working.
Each helper returns a ``(description, expected_output)`` tuple.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.analysis.gaps import Gap


def _malformed_requirement(nid: str, ctx: str) -> tuple[str, str]:
    return (
        f"Requirement node '{nid}' does not follow the mandatory wording format.\n\n"
        f"STEP 1: graph_read(operation=node, node_id={nid}) — read the current content.\n"
        f"STEP 2: rewrite the content as a single, atomic sentence that starts with "
        f"'The system shall ' and captures the same requirement intent.\n"
        f"  Place any conditions (when/while/if/where) AFTER the shall-clause.\n"
        f"  • One requirement per node — no bullet points, no sub-clauses.\n"
        f"  • Keep it testable and unambiguous.\n"
        f"STEP 3: graph_update_node(node_id={nid}, content=<corrected content>)."
        f"{ctx}",
        f"Requirement '{nid}' rewritten to 'The system shall …' form.",
    )


def _non_atomic_requirement(nid: str, ctx: str, gap: Gap | None = None) -> tuple[str, str]:
    obligations = ""
    if gap and gap.context.get("obligations"):
        obs = gap.context["obligations"]
        obligations = "\n\nLLM-identified obligations:\n" + "\n".join(
            f"  {i + 1}. {o}" for i, o in enumerate(obs)
        )
    return (
        f"Requirement '{nid}' is NON-ATOMIC — it contains multiple distinct obligations "
        f"that must each be a separate requirement node.{obligations}\n\n"
        f"STEP 1: graph_read(operation=node, node_id={nid}) — read current content.\n"
        f"STEP 2: graph_update_node(node_id={nid}, content=<first obligation>) — rewrite the "
        f"ORIGINAL node to contain ONLY the first obligation. Keep same parent_id and trace_to.\n"
        f"STEP 3: for EACH additional obligation, graph_add_node — create "
        f"a NEW sibling node of the SAME type (HLR or LLR) with:\n"
        f"   parent_id = same parent as '{nid}'\n"
        f"   content = single sentence starting with 'The system shall '\n"
        f"   title = 3–5 words describing this specific obligation\n\n"
        f"RULES: every resulting node must be a SINGLE testable obligation. "
        f"Do NOT delete the original — rewrite it."
        f"{ctx}",
        f"'{nid}' split into separate atomic requirements.",
    )


def _non_ears_requirement(nid: str, ctx: str, gap: Gap | None = None) -> tuple[str, str]:
    reasoning = ""
    if gap and gap.context.get("reasoning"):
        reasoning = f"\n\nAudit note: {gap.context['reasoning']}"
    return (
        f"Requirement '{nid}' does not follow the required format.{reasoning}\n\n"
        f"Required format — every requirement MUST start with 'The system shall'.\n"
        f"Place any conditions AFTER the shall-clause:\n"
        f"  The system shall <action>.\n"
        f"  The system shall <action> when <condition>.\n"
        f"  The system shall <action> if <condition>.\n"
        f"  The system shall <action> while <state>.\n"
        f"  The system shall <action> where <feature> is configured.\n\n"
        f"STEP 1: graph_read(operation=node, node_id={nid}) — read current content.\n"
        f"STEP 2: Determine how to express the requirement starting with 'The system shall'.\n"
        f"STEP 3: graph_update_node(node_id={nid}, content=<rewritten content>) — rewrite "
        f"starting with 'The system shall'. Use exactly ONE 'shall'.\n"
        f"{ctx}",
        f"'{nid}' rewritten to start with 'The system shall'.",
    )


def _stale_title(nid: str, ctx: str, gap: Gap) -> tuple[str, str]:
    reasoning = (gap.context or {}).get("reasoning", "")
    return (
        f"Node '{nid}' has a title that no longer matches its content scope.\n"
        f"Reason: {reasoning}\n\n"
        f"STEP 1: graph_read(operation=node, node_id={nid}) — read the current title and content.\n"
        f"STEP 2: Choose a 3-5 word title that accurately summarises ONLY the current content.\n"
        f'STEP 3: graph_update_node(node_id={nid}, title="<new title>").'
        f"{ctx}",
        f"Node '{nid}' title updated to match current content scope.",
    )


def _vague_title(nid: str, ctx: str, gap: Gap) -> tuple[str, str]:
    reasoning = (gap.context or {}).get("reasoning", "")
    return (
        f"Node '{nid}' has a vague/generic title. Replace it with a concrete noun phrase.\n"
        f"Reason: {reasoning}\n\n"
        f"Bad: 'Handle Cases', 'Misc Rules', 'General Behavior'.\n"
        f"Good: 'Return Empty List', 'Reject Boolean Values', 'Parse CSV Row'.\n\n"
        f"STEP 1: graph_read(operation=node, node_id={nid}) — read content to identify the concrete concept.\n"
        f'STEP 2: graph_update_node(node_id={nid}, title="<concrete 3-5 word phrase>").'
        f"{ctx}",
        f"Node '{nid}' retitled with a concrete, specific phrase.",
    )


def _sibling_title_duplicate(nid: str, ctx: str, gap: Gap) -> tuple[str, str]:
    sibling_id = (gap.context or {}).get("sibling_id", "<sibling>")
    shared_title = (gap.context or {}).get("shared_title", "")
    return (
        f"Node '{nid}' shares an identical title {shared_title!r} with sibling "
        f"'{sibling_id}' under the same parent. Pick one to retitle.\n\n"
        f"STEP 1: graph_read(operation=node, node_id={nid}) — read this node's content.\n"
        f"STEP 2: graph_read(operation=node, node_id={sibling_id}) — read the sibling's content.\n"
        f"STEP 3: Decide which node's scope the shared title fits best; retitle the other "
        f"to a 3-5 word phrase that distinguishes its scope.\n"
        f'STEP 4: graph_update_node(node_id=<chosen>, title="<new title>").'
        f"{ctx}",
        f"One of '{nid}' / '{sibling_id}' retitled so sibling titles are distinct.",
    )


def _title_collides_with_parent(nid: str, ctx: str, gap: Gap) -> tuple[str, str]:
    parent_id = (gap.context or {}).get("parent_id", "<parent>")
    parent_title = (gap.context or {}).get("parent_title", "")
    return (
        f"Node '{nid}' has a title identical to its parent '{parent_id}' "
        f"(parent title: {parent_title!r}). A child title should narrow scope.\n\n"
        f"STEP 1: graph_read(operation=node, node_id={nid}) — read the child's content.\n"
        f"STEP 2: graph_read(operation=node, node_id={parent_id}) — read the parent for context.\n"
        f"STEP 3: Choose a 3-5 word title that names the child's specific obligation, "
        f"distinct from and narrower than the parent's.\n"
        f'STEP 4: graph_update_node(node_id={nid}, title="<new title>").'
        f"{ctx}",
        f"Node '{nid}' retitled to reflect narrower scope than parent '{parent_id}'.",
    )


def _untitled_node(nid: str, ctx: str) -> tuple[str, str]:
    return (
        f"Node '{nid}' is missing a human-readable title or its title is too long.\n\n"
        f"STEP 1: graph_read(operation=node, node_id={nid}) — read the node content.\n"
        f"STEP 2: Write a title: 3-5 words, plain English, that summarises what this "
        f"node represents. Examples: 'User Login Flow', 'Auth Module Interface', "
        f"'Parse CSV Data'.\n"
        f'STEP 3: graph_update_node(node_id={nid}, title="<your title>") — set the title.'
        f"{ctx}",
        f"Node '{nid}' updated with a 3-5 word title.",
    )


def _stale_node(nid: str, ctx: str, gap: Gap) -> tuple[str, str]:
    """Self-sufficient staleness repair prompt (design/02).

    The ancestor-chain context starts at the node itself, so when *ctx* is
    present it already carries BOTH the node's own content and the parent's
    current content (token-budget packed) — the prompt says so and forbids
    redundant ``graph_read`` round-trips. An empty *ctx* is stated loudly:
    explicit read steps, never silence.
    """
    gctx = gap.context or {}
    parent_id = gctx.get("parent_id", "<parent>")
    reason = gap.description or (
        f"Node '{nid}' is stale — parent '{parent_id}' content changed "
        f"since this node was authored (provenance hash mismatch)."
    )
    if ctx:
        source_note = (
            f"The Context below already contains this node's current content "
            f"(block [… {nid}]) AND the parent's current content "
            f"(block [… {parent_id}]) — do NOT call graph_read; decide "
            f"directly from the Context."
        )
    else:
        source_note = (
            f"STEP 0: graph_read(operation=node, node_id={nid}) and "
            f"graph_read(operation=node, node_id={parent_id}) — read both "
            f"contents first (no packed context was assembled for this gap)."
        )
    return (
        f"Node '{nid}' is STALE relative to its parent '{parent_id}'.\n"
        f"Reason: {reason}\n\n"
        f"{source_note}\n\n"
        f"Compare this node's content against the parent's CURRENT content, "
        f"then do exactly ONE of:\n"
        f"* content needs re-deriving → graph_update_node(node_id={nid}, "
        f"content=<content re-derived from the parent's current content>) "
        f"(this re-stamps provenance automatically), or\n"
        f"* content is still valid as-is → call "
        f"graph_refresh_provenance(node_id={nid}) to record the review "
        f"without touching content."
        f"{ctx}",
        f"Node '{nid}' re-derived or its provenance refreshed.",
    )


def _orphan_node(nid: str, ctx: str) -> tuple[str, str]:
    return (
        f"Node '{nid}' is an orphan — its declared parent does not exist.\n\n"
        f"Inspect the node and either reconnect it to a valid parent "
        f"via graph_reparent_node, or delete it via graph_delete_node."
        f"{ctx}",
        f"Orphan node '{nid}' resolved (deleted or reparented) in the graph.",
    )


def _empty_content(nid: str, ctx: str) -> tuple[str, str]:
    return (
        f"Node '{nid}' has empty content.\n\n"
        f"Generate meaningful content appropriate to the node type and context, "
        f"then persist it via graph_update_node."
        f"{ctx}",
        f"Node '{nid}' updated with non-empty content in the graph.",
    )


def _duplicate_node(nid: str, ctx: str, gap: Gap | None = None) -> tuple[str, str]:
    ctx_data = (gap.context or {}) if gap else {}
    duplicate_of = ctx_data.get("duplicate_of")
    if duplicate_of:
        return (
            f"The content-analysis system has confirmed that '{nid}' has IDENTICAL content "
            f"to '{duplicate_of}' (exact byte-for-byte match after normalisation).\n\n"
            f"'{duplicate_of}' is the canonical copy — '{nid}' is the duplicate to remove.\n\n"
            f"ACTION: Delete '{nid}' immediately:\n"
            f"  graph_delete_node(node_id={nid})\n\n"
            f"If graph_read returns 'node not found', the node was already "
            f"deleted — no further action needed. Do NOT re-evaluate whether they are duplicates; "
            f"the analysis has already confirmed this.",
            f"Exact duplicate '{nid}' deleted (canonical: '{duplicate_of}').",
        )
    return (
        f"Requirement '{nid}' is a potential semantic duplicate of a sibling.\n\n"
        f"The requirement content and sibling requirements are provided in the context "
        f"below — do NOT call graph_read.\n\n"
        f"Decision (choose exactly one — you MUST call a tool):\n"
        f"  • DUPLICATE: '{nid}' expresses the same behavioural intent as any sibling "
        f"(even with different wording) →\n"
        f"      graph_delete_node(node_id={nid})\n"
        f"  • UNIQUE: '{nid}' is genuinely distinct from every sibling →\n"
        f"      graph_update_node(node_id={nid}, "
        f'properties={{"semantic_check": "OK"}})\n\n'
        f"Evaluate the BEHAVIOURAL INTENT, not the exact wording. Two requirements that "
        f"describe the same system behaviour are duplicates even if phrased differently."
        f"{ctx}",
        f"Requirement '{nid}' — duplicate deleted or confirmed unique.",
    )


def _inconsistent_content(nid: str, ctx: str, gap: Gap | None = None) -> tuple[str, str]:
    ref_note = (
        (
            "The context below is the parent node — the PRIMARY REFERENCE. "
            "Evaluate whether this node content adequately addresses the parent.\n\n"
        )
        if ctx
        else ""
    )
    return (
        f"Node '{nid}' requires a parent consistency check.\n\n"
        f"{ref_note}"
        f"STEP 1: graph_read(operation=node, node_id={nid}) — read its content.\n"
        f"STEP 2: check_consistency(node_id='{nid}', child_content=<content>, "
        f"parent_content=<parent content from context>). You MUST pass both strings.\n"
        f"STEP 3: act on the result:\n"
        f"  • consistent=true — no action needed.\n"
        f"  • consistent=false — update the node content via graph_update_node.\n"
        f"  • true duplicate of another sibling — graph_delete_node.\n"
        f"{ctx}",
        f"Node '{nid}' reviewed — updated, deleted, or confirmed consistent.",
    )


def _vague_requirement(nid: str, ctx: str) -> tuple[str, str]:
    return (
        f"Requirement '{nid}' uses ambiguous language with no measurable criteria.\n\n"
        f"STEP 1: graph_read(operation=node, node_id={nid}) — read current content.\n"
        f"STEP 2: Identify vague terms (e.g. 'appropriate', 'reasonable', 'as needed', "
        f"'etc.', 'user-friendly') and replace with measurable criteria.\n"
        f"STEP 3: graph_update_node(node_id={nid}, content=<precise content>).\n"
        f"The rewritten requirement must be specific enough that two independent "
        f"developers would implement it the same way."
        f"{ctx}",
        f"Requirement '{nid}' rewritten with measurable criteria.",
    )


def _untestable_requirement(nid: str, ctx: str) -> tuple[str, str]:
    return (
        f"Requirement '{nid}' cannot be verified by testing — no observable outcome.\n\n"
        f"STEP 1: graph_read(operation=node, node_id={nid}) — read current content.\n"
        f"STEP 2: Rewrite the requirement so it describes a specific behaviour that "
        f"can be checked by running the system and observing the result.\n"
        f"STEP 3: graph_update_node(node_id={nid}, content=<testable content>).\n"
        f"Every requirement must describe an observable outcome."
        f"{ctx}",
        f"Requirement '{nid}' rewritten with an observable, testable outcome.",
    )


def _contradictory_requirements(nid: str, ctx: str, gap: Gap | None = None) -> tuple[str, str]:
    return (
        f"Requirement '{nid}' contradicts one or more sibling requirements.\n\n"
        f"STEP 1: graph_read(operation=node, node_id={nid}) — read current content.\n"
        f"STEP 2: Review sibling requirements in context. Identify the conflict.\n"
        f"STEP 3: Resolve the contradiction by either:\n"
        f"  a) Rewriting '{nid}' via graph_update_node to remove the conflict, OR\n"
        f"  b) Deleting '{nid}' via graph_delete_node if it is a duplicate or wrong.\n"
        f"Preserve the intent of the more specific requirement."
        f"{ctx}",
        f"Contradiction involving '{nid}' resolved.",
    )


def _incomplete_decomposition(nid: str, ctx: str) -> tuple[str, str]:
    return (
        f"HLR '{nid}' is incompletely decomposed — its LLR children do not fully "
        f"cover the requirement given the MODULE/CONTRACT context.\n\n"
        f"STEP 1: graph_read(operation=node, node_id={nid}) — read the HLR.\n"
        f"STEP 2: Review existing LLR children and the MODULE/CONTRACT context below.\n"
        f"STEP 3: Create additional LLR nodes via graph_add_node to cover the missing "
        f"aspects. Each LLR must be atomic ('The system shall …')."
        f"{ctx}",
        f"HLR '{nid}' fully decomposed into LLRs covering all CONTRACT interfaces.",
    )


def _inadequate_content(nid: str, ctx: str) -> tuple[str, str]:
    return (
        f"Node '{nid}' has content too short or vague to be actionable downstream.\n\n"
        f"STEP 1: graph_read(operation=node, node_id={nid}) — read current content.\n"
        f"STEP 2: Expand the content to be substantive and actionable. For DESIGNs, "
        f"include class name, method signatures, and responsibilities. For CONTRACTs, "
        f"include function signatures, pre/post conditions, and invariants.\n"
        f"STEP 3: graph_update_node(node_id={nid}, content=<expanded content>)."
        f"{ctx}",
        f"Node '{nid}' updated with substantive content.",
    )


def _contract_violation(nid: str, ctx: str) -> tuple[str, str]:
    return (
        f"DESIGN '{nid}' does not conform to its MODULE's CONTRACT interface.\n\n"
        f"STEP 1: graph_read(operation=node, node_id={nid}) — read the DESIGN.\n"
        f"STEP 2: Compare against the CONTRACT in the context below.\n"
        f"STEP 3: Rewrite the DESIGN via graph_update_node to conform to the "
        f"CONTRACT's public interface. The DESIGN must implement against the "
        f"CONTRACT's specified signatures and invariants."
        f"{ctx}",
        f"DESIGN '{nid}' updated to conform to its MODULE's CONTRACT.",
    )


def _stale_architecture(nid: str, ctx: str, gap: Gap | None = None) -> tuple[str, str]:
    newer = ""
    if gap and gap.context.get("newer_hlr_ids"):
        ids = gap.context["newer_hlr_ids"]
        newer = f"\nNewer HLRs not covered by this architecture: {ids}"
    return (
        f"ARCHITECTURE '{nid}' is stale — HLRs have been added since it was "
        f"written and the architecture no longer reflects the current "
        f"requirements landscape.{newer}\n\n"
        f"STEP 1: graph_read(operation=node, node_id={nid}) — read the current "
        f"architecture content.\n"
        f"STEP 2: Review the HLRs in the context; decide whether the existing\n"
        f"  module decomposition still applies or whether new modules are needed.\n"
        f"STEP 3: graph_update_node(node_id={nid}, content=<revised architecture>) "
        f"to refresh the document.\n"
        f"STEP 4: If a new MODULE is required, graph_add_node it under "
        f"'{nid}' with trace_to covering the new HLRs.\n"
        f"{ctx}",
        f"ARCHITECTURE '{nid}' refreshed to cover newer HLRs.",
    )


def _stale_suite(nid: str, ctx: str, gap: Gap | None = None) -> tuple[str, str]:
    newer = ""
    if gap and gap.context.get("newer_req_ids"):
        ids = gap.context["newer_req_ids"]
        newer = f"\nNewer requirements not covered by this suite's scope: {ids}"
    return (
        f"SUITE '{nid}' is stale — requirements have been added since this test "
        f"strategy was written.{newer}\n\n"
        f"STEP 1: graph_read(operation=node, node_id={nid}) — read the current "
        f"SUITE content.\n"
        f"STEP 2: Review the requirements in context; update the Scope, "
        f"Approach, Tools, and Entry/Exit criteria to include the new "
        f"requirements.\n"
        f"STEP 3: graph_update_node(node_id={nid}, content=<revised strategy>).\n"
        f"{ctx}",
        f"SUITE '{nid}' strategy updated to include newer requirements.",
    )


def _cross_module_coupling(nid: str, ctx: str) -> tuple[str, str]:
    return (
        f"DESIGN '{nid}' references internals of another MODULE.\n\n"
        f"STEP 1: graph_read(operation=node, node_id={nid}) — read the DESIGN.\n"
        f"STEP 2: Identify cross-module references that bypass CONTRACTs.\n"
        f"STEP 3: Rewrite the DESIGN via graph_update_node to only depend on "
        f"other modules through their CONTRACT interfaces."
        f"{ctx}",
        f"DESIGN '{nid}' updated to remove cross-module coupling.",
    )


def _stale_trace(nid: str, gap: Gap, ctx: str) -> tuple[str, str]:
    stale_refs = gap.context.get("stale_refs", [])
    wrong_type = gap.context.get("wrong_type_refs", [])
    missing_trace = gap.context.get("missing_trace", False)
    expected_type = gap.context.get("expected_type", "")

    if missing_trace:
        return (
            f"Node '{nid}' has no trace_to — "
            f"it must reference at least one {expected_type}.\n\n"
            f"STEP 1: graph_read(operation=node, node_id={nid}) — read its content "
            f"to understand what requirement it verifies.\n"
            f"STEP 2: graph_read(operation=nodes, node_type={expected_type}) — list all "
            f"{expected_type} nodes.\n"
            f"STEP 3: match the CASE to the {expected_type} it tests based on content.\n"
            f"STEP 4: graph_update_trace(node_id='{nid}', trace_to=[<{expected_type} node_id>]).",
            f"CASE '{nid}' trace_to updated to reference the correct {expected_type}.",
        )

    if wrong_type:
        return (
            f"CASE node '{nid}' has trace_to reference(s) pointing to nodes of the "
            f"WRONG type: {wrong_type}.\n\n"
            f"Expected: trace_to must contain only {expected_type} node IDs.\n\n"
            f"STEP 1: graph_read(operation=node, node_id={nid}) — note its sub_type and current trace_to.\n"
            f"STEP 2: remove the wrong-type reference(s) via graph_update_trace — set trace_to to contain "
            f"ONLY valid {expected_type} node IDs.\n"
            f"STEP 3: if no valid {expected_type} reference remains after removal, "
            f"find the correct {expected_type} node that this CASE should verify "
            f"(read the CASE content for clues) and add it via graph_update_trace.",
            f"Wrong-type trace_to reference(s) removed or corrected on '{nid}'.",
        )

    return (
        f"Node '{nid}' has trace_to references pointing to nodes that no "
        f"longer exist.\n\n"
        f"Use graph_read(operation=node, node_id={nid}) to confirm the current trace_to list.\n"
        f"Then remove the dead references via graph_remove_traces("
        f"node_id={nid}, trace_to={json.dumps(stale_refs)}).\n"
        f"remove_traces removes only the specified IDs, leaving valid references intact.\n"
        f"The stale refs are: {stale_refs}",
        f"Dead trace_to reference(s) removed from '{nid}'.",
    )
