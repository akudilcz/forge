"""Prompt templates that author new graph artefacts.

Per-gap-type helpers for structural gaps — chunking documents, deriving
requirements, writing architecture/contracts/designs, and creating test
cases. Extracted from ``task_prompts.py``, which re-exports every helper
so existing import sites and patch targets keep working.
Each helper returns a ``(description, expected_output)`` tuple.
"""

from __future__ import annotations

#: Contract categories that live builds repeatedly dropped between whitepaper
#: and graph (topological_sort e2e: CyclicGraphError's ValueError base class,
#: `find_cycle -> ... | None`, and the tie_breaker key-function arity all
#: survived only inside an API-signature code block and never became
#: requirements). Shared by the phase 2/3 derivation prompts (must-capture)
#: and mirrored by ``CASE_CONTRACT_ENCODING`` for phase 9/10.
NORMATIVE_MUST_CAPTURE = (
    "MUST-CAPTURE categories — each normative fact in these categories is a\n"
    "separate obligation and MUST become its own requirement (these are the\n"
    "recurring casualties of summarisation):\n"
    "  • Exception contracts: the exact exception class AND its base class\n"
    "    (e.g. 'CyclicGraphError shall subclass ValueError'), plus any\n"
    "    required attributes or message format on the exception.\n"
    "  • Return-value contracts: the exact value and type returned in each\n"
    "    documented situation — including None-vs-empty-collection\n"
    "    distinctions and '| None' Optional returns.\n"
    "  • Ordering / tie-break / determinism rules: WHAT order, WHEN the rule\n"
    "    applies (e.g. at every selection step, not once up front),\n"
    "    comparability fallbacks, and cross-run determinism guarantees.\n"
    "  • Caller-supplied callable contracts: the exact signature and arity\n"
    "    the callable is invoked with (e.g. a per-item key function, NOT a\n"
    "    list transform).\n"
    "API-signature code blocks are NORMATIVE requirements sources: every\n"
    "fact in a signature (base classes, attribute types, '| None' returns,\n"
    "Callable[...] shapes, keyword-only markers) is an obligation. NEVER\n"
    "summarise a signature block as 'shall provide function X'.\n"
)

#: Phase 9/10 counterpart of ``NORMATIVE_MUST_CAPTURE``: how CASEs must
#: encode those contracts so a wrong implementation cannot pass.
CASE_CONTRACT_ENCODING = (
    "CONTRACT-ENCODING RULES — acceptance criteria must make a wrong\n"
    "implementation FAIL, not merely exercise the topic:\n"
    "  • Exception cases: assert the exception's base class too (e.g. 'is\n"
    "    caught by except ValueError') and any required attributes or\n"
    "    message wording.\n"
    "  • Return-value cases: assert the EXACT value — 'is None' where the\n"
    "    contract says None; never accept an empty collection as equivalent.\n"
    "  • Ordering/tie-break cases: use DISCRIMINATING inputs — data with\n"
    "    real dependencies/edges such that an implementation applying the\n"
    "    rule only to the initial candidates (instead of at every selection\n"
    "    step) fails — and assert the FULL exact output sequence, never\n"
    "    just membership or edge-free inputs.\n"
    "  • Callable-parameter cases: invoke the callable with the exact arity\n"
    "    the contract states (e.g. one item -> key), so a wrong arity breaks.\n"
)

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
        f"- API-signature code blocks are NORMATIVE, not decoration: a code block\n"
        f"  declaring base classes, attributes, '| None' returns, or Callable\n"
        f"  parameter shapes carries one obligation per fact — keep the code block\n"
        f"  verbatim in its PARA body so derivation can capture every fact.\n"
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
        f"{NORMATIVE_MUST_CAPTURE}"
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
        f"behavioural invariants, external dependencies.\n\n"
        f"STEP 4 — REQUIRED: pass properties with a structured public_api list.\n"
        f"Every public symbol the module must expose becomes one entry:\n"
        f'  properties = {{"public_api": [\n'
        f'    {{"module": "<top-level module name>", "symbol": "<name or '
        f'Class.method>",\n'
        f'     "kind": "function|class|method", "signature": "<exact signature>"}},\n'
        f"    ...]}}\n"
        f"Where the source material contains signature blocks, transcribe each\n"
        f"signature exactly — base classes, '| None' returns, keyword-only\n"
        f"markers, Callable shapes — never summarise. The write is REJECTED\n"
        f"without a valid non-empty public_api, and phase 12 verifies the\n"
        f"workspace exposes every entry.\n\n"
        f"STEP 5 — implementation prohibitions: if the source material forbids\n"
        f"implementation techniques ('must not use X', 'forbidden', 'without\n"
        f"using'), transcribe each ban into the same properties object:\n"
        f'  "prohibited_constructs": [\n'
        f'    {{"construct": "<dotted name, e.g. eval / compile / ast /'
        f' ast.literal_eval>",\n'
        f'     "rationale": "<the source material\'s stated reason,'
        f' with section ref>"}}]\n'
        f"omit the key entirely when the source material states none. Phase 12\n"
        f"statically bans every listed construct in src/ (tests exempt).\n"
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
        f"  Align the Scope to the SUITE Scope where shown.\n"
        f"{CASE_CONTRACT_ENCODING}\n"
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
        f"  Align the Scope to the SUITE Scope where shown.\n"
        f"{CASE_CONTRACT_ENCODING}\n"
        f"STRICT RULES: No pytest code. No file_write. No run_tests. "
        f"No SUITE id in trace_to. ONE tool call maximum."
        f"{ctx}",
        f"'{nid}' covered by a CASE with functional test coverage.",
    )

