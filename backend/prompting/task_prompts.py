"""Per-gap-type prompt templates for agent task descriptions.

Extracted from task_builder.py to keep that file focused on context
building. Each helper returns a ``(description, expected_output)`` tuple.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from backend.analysis.gaps import GapType

if TYPE_CHECKING:
    from backend.analysis.gaps import Gap


def build_descriptions(
    nid: str,
    ctx: str,
    gap: Gap,
    *,
    suite_id: str = "",
) -> dict[GapType, tuple[str, str]]:
    """Build the full dispatch-table mapping GapType -> (description, output)."""
    return {
        GapType.UNCHUNKED_DOCUMENT: _doc_chunk(nid, ctx),
        GapType.UNCOVERED_PARA: _para_hlr(nid, ctx),
        GapType.UNARCHITECTED: _architect(nid, ctx),
        GapType.UNMODULARISED: _modularise(nid, ctx),
        GapType.UNCONTRACTED: _contract(nid, ctx),
        GapType.UNREFINED_HLR: _llr(nid, ctx),
        GapType.UNDESIGNED: _design(nid, ctx),
        GapType.UNSUITED: _suite(nid, ctx),
        GapType.UNTESTED_HLR: _test_hlr(nid, ctx, suite_id=suite_id),
        GapType.UNTESTED_LLR: _test_llr(nid, ctx, suite_id=suite_id),
        # UNSYNCED_DESIGN / UNSYNCED_TEST: handled by workspace_sync step (no agent)
        GapType.STALE_NODE: _stale_node(nid, ctx),
        GapType.ORPHAN_NODE: _orphan_node(nid, ctx),
        GapType.EMPTY_CONTENT: _empty_content(nid, ctx),
        GapType.STALE_TRACE_TO: _stale_trace(nid, gap, ctx),
        GapType.INCONSISTENT_CONTENT: _inconsistent_content(nid, ctx, gap),
        GapType.NON_ATOMIC_REQUIREMENT: _non_atomic_requirement(nid, ctx, gap),
        GapType.NON_EARS_REQUIREMENT: _non_ears_requirement(nid, ctx, gap),
        GapType.MALFORMED_REQUIREMENT: _malformed_requirement(nid, ctx),
        GapType.UNTITLED_NODE: _untitled_node(nid, ctx),
        GapType.TITLE_COLLIDES_WITH_PARENT: _title_collides_with_parent(nid, ctx, gap),
        GapType.SIBLING_TITLE_DUPLICATE: _sibling_title_duplicate(nid, ctx, gap),
        GapType.STALE_TITLE: _stale_title(nid, ctx, gap),
        GapType.VAGUE_TITLE: _vague_title(nid, ctx, gap),
        GapType.DUPLICATE_NODE: _duplicate_node(nid, ctx, gap),
        GapType.VAGUE_REQUIREMENT: _vague_requirement(nid, ctx),
        GapType.UNTESTABLE_REQUIREMENT: _untestable_requirement(nid, ctx),
        GapType.CONTRADICTORY_REQUIREMENTS: _contradictory_requirements(nid, ctx, gap),
        GapType.INCOMPLETE_DECOMPOSITION: _incomplete_decomposition(nid, ctx),
        GapType.INADEQUATE_CONTENT: _inadequate_content(nid, ctx),
        GapType.CONTRACT_VIOLATION: _contract_violation(nid, ctx),
        GapType.CROSS_MODULE_COUPLING: _cross_module_coupling(nid, ctx),
        GapType.STALE_ARCHITECTURE: _stale_architecture(nid, ctx, gap),
        GapType.STALE_SUITE: _stale_suite(nid, ctx, gap),
    }


# ── Per-gap-type helpers (each <= 20 lines) ──────────────────────────────────


def _doc_chunk(nid: str, ctx: str) -> tuple[str, str]:
    return (
        f"Document '{nid}' needs to be organised into a hierarchical tree of PARA nodes.\n\n"
        f"The full document content is inlined in the context below — you do NOT\n"
        f"need to call graph_read for the document content itself.\n\n"
        f"STEP 1 — graph_read(operation=children, node_id='{nid}') — check for\n"
        f"  existing PARAs. If coverage is already complete for every document\n"
        f"  heading, STOP. If partial, resume from the first uncovered heading.\n\n"
        f"STEP 2 — Build the PARA tree by calling graph_add_node for each section:\n\n"
        f"  For each top-level section:\n"
        f"    graph_add_node(node_type=PARA, parent_id='{nid}',\n"
        f"                   content=<section body>, title=<3-5 words>,\n"
        f"                   para_type=<functional|rationale|constraint|non_functional|heading>)\n\n"
        f"  For each subsection, use the RETURNED node_id as parent_id:\n"
        f"    graph_add_node(node_type=PARA, parent_id=<parent PARA node_id>,\n"
        f"                   content=<subsection body>, title=<3-5 words>,\n"
        f"                   para_type=...)\n\n"
        f"para_type guide — choose the dominant intent of the paragraph:\n"
        f"  • functional: describes behaviour the system MUST perform.\n"
        f"       e.g. 'The parser extracts HLRs from each paragraph.'\n"
        f"  • rationale: explains WHY a design or requirement exists.\n"
        f"       e.g. 'Chunking before derivation keeps the LLM context small.'\n"
        f"  • constraint: hard limit, dependency, or compliance rule.\n"
        f"       e.g. 'Must run on Python 3.12+; no external services at build time.'\n"
        f"  • non_functional: quality attribute with a measurable target.\n"
        f"       e.g. 'P95 response latency below 200 ms under 100 qps.'\n"
        f"  • heading: section marker with no meaningful body (body is in children).\n\n"
        f"Granularity — split at the REQUIREMENT boundary, not just at headings:\n"
        f"- Each distinct 'shall' statement, numbered requirement, or bullet-point\n"
        f"  behaviour becomes its own PARA. Do NOT pack multiple independent\n"
        f"  requirements into one PARA just because they share a section heading.\n"
        f"- A body like 'The system shall do X. It shall also do Y. It must\n"
        f"  handle Z.' becomes three PARAs under the same parent, not one PARA.\n"
        f"- Edge-case bullet lists (e.g. 'empty returns empty, single returns\n"
        f"  single, already-sorted stays sorted') become one PARA per bullet.\n"
        f"- Error-handling sentences (e.g. 'shall raise TypeError on bad input')\n"
        f"  get their own PARA — separate concern from the happy path.\n"
        f"- Prefer MORE granular PARAs over fewer large ones. One concern per PARA.\n"
        f"  There is no minimum word count; a one-sentence PARA is fine.\n\n"
        f"Rules:\n"
        f"- Each PARA carries ONLY its own text, not its children's.\n"
        f"- No trace_to on PARA nodes.\n"
        f"- Cover every section of the document; do not silently skip any heading.\n"
        f"{ctx}",
        f"Hierarchical PARA tree built under '{nid}' covering every document section.",
    )


def _para_hlr(nid: str, ctx: str) -> tuple[str, str]:
    # Extract the PARA content from the ancestor context
    para_content = ""
    if ctx:
        import re  # noqa: PLC0415

        match = re.search(rf"\[PARA {re.escape(nid)}\]\n(.+?)(?:\n\n---|\Z)", ctx, re.DOTALL)
        if match:
            para_content = match.group(1).strip()

    content_block = (
        (
            f"\nPARAGRAPH CONTENT (use this — do NOT hallucinate or guess):\n"
            f"---\n{para_content}\n---\n"
        )
        if para_content
        else ""
    )

    return (
        f"Paragraph '{nid}' has no HLR requirement derived from it.\n"
        f"{content_block}\n"
        f"PREFERRED ACTION — link an existing HLR:\n"
        f"  graph_read(operation=nodes, node_type=HLR) — list all existing HLRs.\n"
        f"  If ANY existing HLR already captures the same requirement as this paragraph,\n"
        f"  re-parent it under '{nid}' via:\n"
        f"    graph_reparent_node(node_id=<hlr_id>, parent_id='{nid}')\n"
        f"  Then STOP.\n"
        f"  ONLY re-parent LLR nodes — never PARA, DOCUMENT, or other types.\n\n"
        f"FALLBACK — only if no existing HLR covers this paragraph:\n"
        f"  Step A — ENUMERATE distinct obligations in the PARAGRAPH CONTENT.\n"
        f"    An obligation is any separately-testable behaviour: a main\n"
        f"    behaviour, an edge-case rule, an error-handling rule. Count them.\n"
        f"    A paragraph that says 'shall do X; empty returns empty; raises\n"
        f"    TypeError on bad input' contains THREE obligations.\n"
        f"  Step B — CREATE ONE HLR PER OBLIGATION. If the paragraph contains N\n"
        f"    obligations, emit N graph_add_node calls, each a separate HLR\n"
        f"    under '{nid}'. Do not merge obligations into a compound HLR.\n"
        f"  Step C — each HLR content MUST be a single ATOMIC 'The system shall '\n"
        f"    sentence — ONE testable obligation, no 'and'/'while'/semicolons.\n"
        f"    Atomicity test: if the sentence contains 'and' linking two verbs\n"
        f"    or outcomes, split into separate HLRs.\n"
        f"  Leave node_id empty — auto-assigns a HLR-NNNN ID.\n"
        f"  NEVER create placeholder HLRs like 'Handle PARA-XXXX Content'.\n"
        f"  When calling derive_requirement (once per obligation), pass the\n"
        f"  specific obligation's text as parent_content, not the full paragraph."
        f"{ctx}",
        f"Existing HLR re-parented under '{nid}', or new HLR node written to the graph.",
    )


def _architect(nid: str, ctx: str) -> tuple[str, str]:
    return (
        f"Project '{nid}' has no ARCHITECTURE.\n\n"
        f"STEP 1 — read ALL HLRs:\n"
        f"Use graph_read (operation=nodes, node_type=HLR). Read each HLR's content carefully.\n\n"
        f"STEP 2 — create the ARCHITECTURE node as a DETAILED MARKDOWN DOCUMENT:\n"
        f"Parent: '{nid}'. Leave node_id empty — auto-assigns (e.g. ARCHITECTURE-0001).\n"
        f"trace_to: ALL HLR node_ids. NEVER include PARA, DOCUMENT, or PROJECT.\n\n"
        f"The content MUST be a full markdown document with ALL of the following sections:\n\n"
        f"  ## Executive Summary\n"
        f"  2-4 sentences: what the system does, the chosen architectural style, and why.\n\n"
        f"  ## Technology Stack\n"
        f"  List the concrete technology choices: language(s), runtime, key libraries/frameworks,\n"
        f"  databases or storage, external services, and build tooling. State WHY each was chosen.\n\n"
        f"  ## Architectural Patterns\n"
        f"  Name and describe the primary pattern(s) in use (e.g. layered, event-driven,\n"
        f"  hexagonal, pipeline, CQRS). Explain how they apply to this system.\n\n"
        f"  ## Module Design\n"
        f"  Describe the single module that implements the system. Include:\n"
        f"    - Public interface preview: key function/class signatures, inputs, outputs\n"
        f"    - Class plan: class name(s), role, responsibilities\n"
        f"    Do NOT list HLR IDs here — HLR tracing is done via trace_to on MODULE nodes.\n\n"
        f"  ## Data Flow & State Management\n"
        f"  Describe how data moves through the system end-to-end. Identify any shared state,\n"
        f"  persistence boundaries, and concurrency considerations.\n\n"
        f"  ## Cross-Cutting Concerns\n"
        f"  Address: error handling strategy, logging/observability, security boundaries,\n"
        f"  configuration management, and extensibility points.\n\n"
        f"  ## Key Design Decisions\n"
        f"  Bullet list of the most important decisions with brief rationale for each.\n\n"
        f"STEP 3 — create one or more MODULE nodes:\n"
        f"Create the set of modules that best decomposes the system — one module\n"
        f"per cohesive area of responsibility implied by the HLRs, the whitepaper\n"
        f"digest (rationale + NFRs + constraints, in the context below), and the\n"
        f"architectural pattern you chose. Prefer FEWER larger modules over many\n"
        f"small ones; only split when responsibilities are genuinely independent.\n\n"
        f"Parent: the ARCHITECTURE node_id returned in Step 2. Leave node_id empty.\n"
        f"trace_to: list the HLR node_ids this module covers. Every HLR must be\n"
        f"traced by exactly ONE module (no overlap, no omissions).\n"
        f"content MUST include ONLY:\n"
        f"  • Responsibilities and scope (what the module does)\n"
        f"  • Class plan — the implementation classes, their names, and the\n"
        f"    functional role each covers (LLRs do not exist yet — describe by role).\n"
        f"DO NOT list HLR IDs in the content — that information belongs in trace_to.\n\n"
        f"Use the WHITEPAPER DIGEST section in the context below to inform tech\n"
        f"stack, constraints, and NFR-sensitive design choices."
        f"{ctx}",
        "ARCHITECTURE node written as a detailed markdown design document; MODULE nodes created.",
    )


def _modularise(nid: str, ctx: str) -> tuple[str, str]:
    return (
        f"HLR '{nid}' is not covered by any MODULE.\n\n"
        f"STEP 1: graph_read(operation=nodes, node_type=MODULE) — list all existing MODULEs.\n\n"
        f"STEP 2: pick the most relevant MODULE — the one whose responsibilities\n"
        f"  and class plan semantically cover this HLR's concern — and call:\n"
        f'  graph_add_traces(node_id=<module_id>, trace_to=["{nid}"])\n'
        f"  add_traces APPENDS '{nid}' to the existing list without replacing\n"
        f"  other HLRs. That's all.\n\n"
        f"Only create a NEW MODULE when no existing MODULE is a semantically\n"
        f"good fit. If creating:\n"
        f"  graph_read(operation=nodes, node_type=ARCHITECTURE) → find the ARCHITECTURE id.\n"
        f"  graph_add_node(node_type=MODULE, parent_id=<arch_id>,\n"
        f'    trace_to=["{nid}"], content=<responsibilities + class plan>,\n'
        f"    title=<3-5 word name>). Leave node_id empty — auto-assigns.\n"
        f"  Do NOT list HLR IDs in the content — trace_to is the only place for traceability."
        f"{ctx}",
        f"HLR '{nid}' appended to a MODULE's trace_to.",
    )


def _contract(nid: str, ctx: str) -> tuple[str, str]:
    return (
        f"MODULE '{nid}' has no CONTRACT child.\n\n"
        f"STEP 1: read the MODULE and its parent ARCHITECTURE.\n\n"
        f"STEP 2: use graph_read (operation=children, node_id={nid}).\n\n"
        f"STEP 3: create a CONTRACT node as a child of '{nid}' via graph_add_node.\n"
        f"Content must specify: public function signatures, pre/post-conditions, "
        f"behavioural invariants, external dependencies.\n"
        f"Leave node_id empty — auto-assigns (e.g. CONTRACT-0001)."
        f"{ctx}",
        f"CONTRACT node written to the graph as a child of '{nid}'.",
    )


def _llr(nid: str, ctx: str) -> tuple[str, str]:
    return (
        f"HLR '{nid}' has no LLR children.\n\n"
        f"All context is provided below — do NOT call graph_read.\n"
        f"The HLR content and ALL existing LLR nodes are listed in the context.\n\n"
        f"PREFERRED ACTION — re-parent existing LLR(s):\n"
        f"  Review the EXISTING LLR NODES in the context. If ANY existing LLR\n"
        f"  semantically refines '{nid}' (same subject/behaviour, more specific),\n"
        f"  move it under '{nid}' via:\n"
        f"    graph_reparent_node(node_id=<llr_id>, parent_id='{nid}')\n"
        f"  You may re-parent multiple LLRs. If this fully covers the HLR, STOP.\n"
        f"  ONLY re-parent LLR nodes — never PARA, DOCUMENT, or other types.\n"
        f"  NEVER re-parent an LLR that is the ONLY child of its current parent —\n"
        f"  that would leave the other HLR uncovered and create an infinite loop.\n"
        f"  If the only matching LLR is already assigned, use FALLBACK instead.\n\n"
        f"FALLBACK — create NEW LLR(s) for the unique aspects of '{nid}':\n"
        f"  Each HLR has one or more unique obligations. Create ONE LLR per\n"
        f"  distinct obligation — an HLR that contains two ANDed behaviours\n"
        f"  requires two LLRs. Do NOT duplicate existing LLRs.\n"
        f"  Each LLR as a child of '{nid}' via graph_add_node.\n"
        f"  Leave node_id empty — auto-assigns (e.g. LLR-0001).\n"
        f"  Each LLR content MUST be a single ATOMIC sentence beginning with 'The system shall '.\n"
        f"  ATOMIC means ONE testable obligation — never bundle with 'and'/'while'/semicolons.\n\n"
        f"STRICT RULES: Do NOT call graph_read. Do NOT call derive_requirement.\n"
        f"Do NOT create MODULEs or CONTRACTs."
        f"{ctx}",
        f"Existing LLR(s) re-parented under '{nid}', or new LLR node(s) created.",
    )


def _design(nid: str, ctx: str) -> tuple[str, str]:
    return (
        f"LLR '{nid}' is not addressed by any DESIGN spec.\n\n"
        f"The MODULE (with its class plan), CONTRACT, and all existing DESIGN nodes "
        f"are provided in the context below. Do NOT call graph_read to discover them.\n\n"
        f"STEP 1 — read the context: find the MODULE's class plan and any existing DESIGNs.\n\n"
        f"STEP 2 — match '{nid}' to a class in the MODULE's class plan.\n\n"
        f"STEP 3 — if a DESIGN for that class ALREADY EXISTS in the context:\n"
        f'  graph_add_traces(node_id=<existing_design_id>, trace_to=["{nid}"])\n'
        f"  add_traces APPENDS without replacing. This is the PREFERRED action — done.\n\n"
        f"STEP 4 — ONLY if the class plan names a class with NO existing DESIGN:\n"
        f"  Create a DESIGN node under the MODULE via graph_add_node. Content MUST include:\n"
        f"    • Class name (matching the class plan) and single responsibility\n"
        f"    • Method signatures: name, parameters (with types), return type\n"
        f"    • Responsibilities paragraph — what this class does (NOT how it is coded)\n"
        f"  trace_to=['{nid}'], parent_id=<module_id>.\n\n"
        f"CONSOLIDATION RULE: the number of DESIGN nodes must NOT exceed the number "
        f"of classes in the MODULE's class plan. Default is ONE class per MODULE — "
        f"ONE DESIGN covering all its LLRs. Creating a new DESIGN when a matching "
        f"one already exists is WRONG.\n\n"
        f"Do NOT create MODULEs, do NOT write files, do NOT write implementation code."
        f"{ctx}",
        f"DESIGN node updated with trace_to including '{nid}' (new DESIGN only if class plan requires it).",
    )


def _suite(nid: str, ctx: str) -> tuple[str, str]:
    return (
        f"Project '{nid}' has no SUITE (test strategy document).\n\n"
        f"Create a SUITE node as a child of '{nid}' via graph_add_node.\n"
        f"Content MUST be a markdown document with ALL of the following sections:\n\n"
        f"  ## Scope\n"
        f"  Two sub-lists — machine-readable lists of requirement IDs so\n"
        f"  downstream phases can verify coverage programmatically:\n"
        f"    ### In scope\n"
        f"    - <HLR_ID>: <one-line reason>\n"
        f"    - <LLR_ID>: <one-line reason>\n"
        f"    ### Out of scope\n"
        f"    - <HLR_ID or LLR_ID>: <one-line reason>\n\n"
        f"  ## Approach\n"
        f"  Categories of test cases planned (happy path, boundary, error,\n"
        f"  integration, performance) and which requirement types each covers.\n\n"
        f"  ## Tools\n"
        f"  pytest configuration, fixtures, mocking strategy, test data sources.\n\n"
        f"  ## Entry / Exit Criteria\n"
        f"  Entry: prerequisites before testing begins.\n"
        f"  Exit: conditions that declare the suite done (e.g. coverage %, zero\n"
        f"  FAILED cases).\n\n"
        f"This is a STRATEGY document — do NOT list individual test cases here.\n"
        f"Use the ARCHITECTURE / CONTRACTs / requirements in the context below\n"
        f"to ground the Approach and Tools sections in the actual system.\n"
        f"Leave node_id empty — auto-assigns (e.g. SUITE-0001)."
        f"{ctx}",
        f"SUITE node written to the graph as a child of '{nid}'.",
    )


def _test_hlr(nid: str, ctx: str, *, suite_id: str = "") -> tuple[str, str]:
    suite_line = f"  SUITE ID = '{suite_id}'\n" if suite_id else ""
    return (
        f"HLR '{nid}' has no HLR-level test case.\n"
        f"All context (HLR content, existing CASEs) is provided below — "
        f"do NOT call graph_read.\n\n"
        f"OPTION A — reuse an existing CASE (only if it ALREADY provides "
        f"functional coverage):\n"
        f"  Review the EXISTING CASE NODES below. If an existing CASE's test "
        f"steps and acceptance criteria ALREADY VERIFY the EXACT behaviour "
        f"described in '{nid}' (not just a vaguely related topic), run:\n"
        f"    graph_add_traces(node_id=<case_id>, trace_to=['{nid}'])\n"
        f"  Then STOP. Do NOT use this option for a CASE that merely touches "
        f"the same module — the test steps must functionally cover '{nid}'.\n\n"
        f"OPTION B — create a new CASE_HLR (the default):\n"
        f"  If no existing CASE provides genuine functional coverage, create one:\n"
        f"    graph_add_node(node_type=CASE_HLR,\n"
        f"{suite_line}"
        f"      parent_id = '{suite_id}',\n"
        f"      title = short (3-6 word) title,\n"
        f"      trace_to = ['{nid}'],\n"
        f"      content = English verification plan with:\n"
        f"        Objective, Preconditions, Test steps, Acceptance criteria)\n"
        f"  The test steps MUST exercise the specific behaviour in '{nid}'.\n"
        f"  When a DESIGN excerpt is included in the context below, reference its\n"
        f"  actual method names in the test steps rather than invented APIs.\n"
        f"  Align the Scope to the SUITE Scope where shown.\n\n"
        f"STRICT RULES: No pytest code. No file_write. No run_tests. "
        f"No SUITE id in trace_to. ONE tool call maximum."
        f"{ctx}",
        f"'{nid}' covered by a CASE with functional test coverage.",
    )


def _test_llr(nid: str, ctx: str, *, suite_id: str = "") -> tuple[str, str]:
    suite_line = f"  SUITE ID = '{suite_id}'\n" if suite_id else ""
    return (
        f"LLR '{nid}' has no LLR-level test case.\n"
        f"All context (LLR content, existing CASEs) is provided below — "
        f"do NOT call graph_read.\n\n"
        f"OPTION A — reuse an existing CASE (only if it ALREADY provides "
        f"functional coverage):\n"
        f"  Review the EXISTING CASE NODES below. If an existing CASE's test "
        f"steps and acceptance criteria ALREADY VERIFY the EXACT behaviour "
        f"described in '{nid}' (not just a vaguely related topic), run:\n"
        f"    graph_add_traces(node_id=<case_id>, trace_to=['{nid}'])\n"
        f"  Then STOP. Do NOT use this option for a CASE that merely touches "
        f"the same module — the test steps must functionally cover '{nid}'.\n\n"
        f"OPTION B — create a new CASE_LLR (the default):\n"
        f"  If no existing CASE provides genuine functional coverage, create one:\n"
        f"    graph_add_node(node_type=CASE_LLR,\n"
        f"{suite_line}"
        f"      parent_id = '{suite_id}',\n"
        f"      title = short (3-6 word) title,\n"
        f"      trace_to = ['{nid}'],\n"
        f"      content = English verification plan with:\n"
        f"        Objective, Preconditions, Test steps, Acceptance criteria)\n"
        f"  The test steps MUST exercise the specific behaviour in '{nid}'.\n"
        f"  When a DESIGN excerpt is included in the context below, reference its\n"
        f"  actual method names in the test steps rather than invented APIs.\n"
        f"  Align the Scope to the SUITE Scope where shown.\n\n"
        f"STRICT RULES: No pytest code. No file_write. No run_tests. "
        f"No SUITE id in trace_to. ONE tool call maximum."
        f"{ctx}",
        f"'{nid}' covered by a CASE with functional test coverage.",
    )


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


def _stale_node(nid: str, ctx: str) -> tuple[str, str]:
    return (
        f"Node '{nid}' is marked stale — its content may be out of date.\n\n"
        f"Read the node, review its content against the current context, "
        f"then update it via graph_update_node."
        f"{ctx}",
        f"Node '{nid}' updated in the graph.",
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
