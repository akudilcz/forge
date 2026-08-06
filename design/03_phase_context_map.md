# Phase Context Map

Cross-checked against source (`backend/pipeline/` + `backend/prompting/`, `backend/agents/`, `backend/tools/`) on 2026-04-22. For each phase: what actually runs, what context is assembled, concrete issues, and potential solutions.

## Pipeline orchestration (ground truth)

`backend/pipeline/runner.py::PHASE_STEPS` wires phases to step lists:

| Phase | Steps |
|---|---|
| 2 | default: `structural, quality_gaps, combined_quality, semantic` |
| 3 | `batch_phase3, quality_gaps, combined_quality, semantic` |
| 4 | default |
| 5 | `batch_phase5, quality_gaps, combined_quality, semantic` |
| 6 | default |
| 7 | `batch_phase7, quality_gaps, combined_quality, semantic` |
| 8 | `batch_phase8, quality_gaps, combined_quality, semantic, design_consolidation` |
| 9 | default |
| 10 | `batch_phase10, quality_gaps, combined_quality, semantic, case_trace_coverage` |
| 11 | Deterministic (`_run_dashboard_phase` in `special_phases.py`) |
| 12 | LLM code gen (mission agent — `codegen/slice_gen.py::run_code_gen`, dispatched by `_run_code_gen_phase` in `special_phases.py`) — enforces coverage gate (stmt + branch + requirement + test-pass must all be 100%) |
| 13 | `workspace_sync` (deterministic CODE/TEST node creation) then `record_results_step` (heal misparented RESULTs, run tests, record RESULT nodes) |
| 14 | Deterministic deliverables packaging |

Both entry points — the full-run loop (`kickoff_async` → `_run_phase`) and the per-phase route (`run_phase`) — execute these step lists via `run_phase_pipeline`. Step failures propagate (the phase is marked `awaiting_approval` and the exception re-raises); `DispatchQuotaError` always propagates so quota exhaustion halts the run loudly.

**Key correction vs earlier notes**: Phase 13 is *workspace_sync*, not a standalone quality audit. Quality audit runs *inside every phase's pipeline* via `quality_gaps` + `combined_quality` + `semantic` steps — where `combined_quality` is a single batched LLM call judging atomicity + EARS + title↔content match + title specificity on every authored node. `combined_quality` PASS verdicts are sticky per `(node_id, content-hash)` on the flow (unchanged nodes are not re-judged across the runner's up-to-12 cycles; FAIL verdicts are never cached), and the `semantic` step runs a deterministic token-overlap prescreen so clearly-dissimilar candidates never reach the LLM judge (deletion still requires the two-call double confirmation — design/01 §7.4).

The `structural` step's loop certifies each gap's resolution by re-running the gap analyser after the dispatch and requiring that gap's `(type, node_id)` key to be gone — a write anywhere else in the graph never resolves a gap (design/01 §8.3).

## Context assembly machinery

1. Per-gap context: `builder.py::build_context_for_gap()` — `structural` step path. Section builders live in `graph_context.py` (re-exported by `builder.py`).
2. Batch prompts: `batch_prompts.py` — for Phases 3/5/7/8/10.
3. Per-gap task description: `task_prompts.py::build_descriptions()` (helper templates in `task_prompts_authoring.py` / `task_prompts_repair.py`, re-exported by `task_prompts.py`).
4. Role prompts: `templates/roles/*.j2` via `agents/factory.py`.
5. Tool allowlist per gap: `phase_constraints.py`.

Budget: `context_budget.pack()` (tiktoken-counted, default 120k tokens) drops whole lowest-priority sections — the former 40,000-char tail-chop in `builder.py` is gone. DOCUMENT content skipped in ancestor walks (`graph_context.py::_SKIP_ANCESTOR_CONTENT`), included only as title breadcrumb. A `_CHARS_PER_TOKEN=4` estimator exists in `batch_steps.py` for prompt-size logging only.

## Truncation / cap inventory (historical — pre-refactor audit)

All `[:N]` content caps below were subsequently **removed** (see "Content-truncation policy" under Cross-cutting mechanisms — full content everywhere, priority budget absorbs scale). The table records the state that motivated the refactor.

| Builder | Cap | Used by |
|---|---|---|
| `build_ancestor_context` | none (full content) | all structural paths |
| `build_trace_to_context` | none; **silent fallback to ancestor walk** if refs empty/missing (`graph_context.py::build_trace_to_context`) | CASE INCONSISTENT_CONTENT |
| `build_sibling_req_context` | **none** (full content) | DUPLICATE_NODE for HLR/LLR |
| `build_all_peers_context` | **none** (full content) | not called from build_context_for_gap |
| `build_existing_cases_context` | title `[:60]`, **content omitted entirely** | UNTESTED_HLR/LLR |
| `build_existing_llrs_context` | content `[:200]` | UNREFINED_HLR per-gap |
| `build_all_hlrs_context` | content `[:200]` | UNARCHITECTED, UNSUITED |
| `build_all_modules_context` | content `[:200]` | UNSUITED |
| `build_architecture_context` | none | UNCONTRACTED, UNSUITED |
| `build_traced_hlrs_for_module` | none | UNCONTRACTED |
| `build_module_design_context` | none (full MODULE + full CONTRACT + full DESIGNs) | UNDESIGNED per-gap |
| `batch_prompts._format_node_list` | content `[:120]` when `content` is a field | batch 3/5/7/8 node lists |
| `batch_prompts._format_para_list` | content `[:500]` | batch 3 PARA list |
| `batch_phase5` ARCHITECTURE | content `[:2000]` (`batch_prompts.py:56`) | batch 5 |
| `batch_phase8` MODULE | content `[:2000]` (`batch_prompts.py:134`) | batch 8 |
| `batch_phase8` CONTRACT | content `[:2000]` (`batch_prompts.py:128`) | batch 8 |

---

## Phase 2 — Document → PARA (chunking)

- **Pipeline**: default (`structural`) · **Agent**: Document Specialist · **Gap**: `UNCHUNKED_DOCUMENT`
- **Context**: **empty** — `UNCHUNKED_DOCUMENT` is in `_NO_PREFETCH_CONTEXT` (`builder.py:16`), so `build_context_for_gap` returns `""`. The agent must call `graph_read` to see the document.
- **Prompt** (`_doc_chunk`, `task_prompts_authoring.py`): STEP 1 graph_read for document, STEP 2 stop if PARAs exist, STEP 3 build recursive PARA tree. para_type enum: `functional|rationale|constraint|non_functional|heading`.
- **Tools**: `graph_read`, `graph_add_node`.

### Issues found
1. **[FIXED] No worked example**: the enum is listed but para_type boundaries are fuzzy — e.g. a paragraph mixing rationale and a functional sentence has no guidance.
2. **[FIXED] Agent must self-fetch**: every invocation spends a `graph_read` round-trip before doing anything — the document content could be inlined.
3. **Monolithic single-call**: a 20k-char whitepaper is chunked in one pass, no streaming. For long docs the agent may silently drop sections past its effective attention window. _(Infrastructure available via `markdown_sections.extract_sections`; wiring as sub-gap emitter is a follow-up.)_
4. **[FIXED] No idempotency check past "PARAs exist"**: if the previous run created a partial tree, the agent stops even though the tree is incomplete.
5. **[FIXED] No size/depth guidance**: no signal on target granularity (how deep, how big per PARA). Output quality varies model-to-model.

### Potential solutions
1. Inline document content in the context (reuse `build_ancestor_context` on the DOCUMENT itself, or build a new `build_document_content()` helper). Saves a tool call, reduces latency, avoids any chance the graph_read fails or is misrouted.
2. Add an exemplar (one paragraph → two PARA creations, one `functional`, one `rationale`) to the prompt so para_type is calibrated by example, not just enum.
3. For docs > 20k chars: split by top-level headings and dispatch one UNCHUNKED_DOCUMENT gap per heading (or implement a `chunk_document_section` sub-gap type). Each call then fits in cache and attention.
4. Replace "stop if PARAs exist" with a completeness check: compare existing PARA coverage to document headings and only stop when every heading is covered.
5. Specify target size bounds: e.g. "each PARA is 50–400 words of contiguous text; split at the next paragraph or heading boundary."

---

## Phase 3 — PARA → HLR (batch)

- **Pipeline**: `batch_phase3, quality_gaps, combined_quality, semantic` · **Agent**: Requirements Engineer · **Gap**: `UNCOVERED_PARA`
- **Batch context** (`batch_prompts.py:10`): uncovered PARAs (content capped at 500 chars — `_format_para_list`); **all existing HLRs** via `_format_node_list` with `content` capped at 120 chars.
- **Per-gap fallback context** (`_para_hlr`, `task_prompts_authoring.py`): ancestor chain (PARA full + breadcrumb to DOCUMENT) — the PARA text is regex-extracted from ancestor context and re-inserted into a `PARAGRAPH CONTENT` block to anchor the agent against hallucination.
- **Rule**: reparent existing HLR > create new via `derive_requirement`. HLR must be atomic "The system shall …".
- **Tools**: `graph_add_node`, `graph_reparent_node`, `derive_requirement`. Batch blocks `graph_read` (all context inline). Per-gap fallback permits `graph_read(operation=nodes, node_type=HLR)`.

### Issues found
1. **[FIXED] 120-char HLR previews kill dedup recall**: atomic HLRs start "The system shall …" — the distinguishing clause is the verb phrase, and it is frequently past char 120. Two HLRs with near-identical openings look identical in the prompt.
2. **[FIXED] PARA 500-char cap truncates the obligation**: a derivation source that was 800 chars loses the last 300. Those final chars often carry "must" / "shall" / "cannot" clauses that define the requirement.
3. **[FIXED] No sibling PARA context**: rationale/constraint PARAs immediately adjacent (same heading, different child index) are not shown — NFRs stated next-door go unused.
4. **[FIXED] Only HLRs shown, not LLRs**: if an LLR already captures the PARA's obligation, the batch prompt offers no reparent path — the agent will create a redundant HLR. (Reparent "LLR→PARA" is disallowed anyway per `_para_hlr`, but that rule isn't visible in the batch prompt.)
5. **No similarity hinting**: the batch shows *all* HLRs every attempt. At 100+ HLRs the prompt bloats and the agent's reparent judgement degrades. _(A BM25 shortlist was prototyped and removed as unused — the priority budget keeps listings under the cap at current graph sizes.)_
6. **[FIXED] Batch retry-on-fail re-sends the full HLR list three times** (`_MAX_BATCH_ATTEMPTS=3`, `batch_steps.py:39`) — expensive and doesn't help if the problem was context shape, not a transient failure.

### Potential solutions
1. Raise `_format_node_list` content cap from 120 → 400 (or make it a function of remaining context budget). Single cheapest win for dedup quality.
2. Send PARA content in full (or raise cap to 1500) — derivation is the one task where truncating the source is directly harmful.
3. Add a "nearby PARAs" block: the direct parent + preceding + following sibling at each ancestor level (constraint/rationale/non_functional only, skip functional to control size).
4. Use embeddings to shortlist the 8–12 HLRs most similar to each PARA and send those **in full content**. Keep the global list as 120-char titles for reparent visibility.
5. Cache the `all HLRs` block across attempts (content hash) so retries benefit from prompt caching.
6. Add an explicit "if an LLR covers this, ignore — Phase 7 will reparent" instruction so agents don't create HLR stand-ins for LLR-scope obligations.

---

## Phase 4 — HLR → ARCHITECTURE + MODULE

- **Pipeline**: default (per-gap structural) · **Agent**: Design Architect · **Gap**: `UNARCHITECTED` (parent = PROJECT)
- **Context** (`build_context_for_gap` UNARCHITECTED branch, `builder.py:311`): ancestor walk (DOCUMENT breadcrumb title only, no content) + **all HLRs** listing (content capped at 200 chars via `build_all_hlrs_context`).
- **Prompt** (`_architect`, `task_prompts_authoring.py`): mandatory markdown sections (Executive Summary, Tech Stack, Patterns, Module Design, Data Flow, Cross-Cutting, Key Decisions); output single MODULE with `trace_to=[all HLR ids]`.
- **Tools**: `graph_read`, `graph_add_node`.

### Issues found
1. **[FIXED] DOCUMENT content stripped**: the whitepaper — including `rationale`, `constraint`, and `non_functional` PARAs — is excluded from context. Architect sees requirements but not *why*. Technology-stack decisions become guesses.
2. **[FIXED] 200-char HLR preview** is enough for an atomic one-liner, so this is the rare case where the cap is adequate. But combined with the missing DOCUMENT, there is no signal for latency/scale/constraint.
3. **[FIXED] Prompt mandates single MODULE** (`task_prompts_authoring.py`) — "Multiple classes within one module is fine — multiple modules is not, unless the whitepaper explicitly describes separate deployable components". This is a forced architecture, not an assessment.
4. **[FIXED] No re-architecting rule**: if ARCHITECTURE already exists and is stale, UNARCHITECTED doesn't fire; there's no trigger to revise.
5. **trace_to cardinality unbounded**: Architect must trace *every* HLR from the single MODULE. At 200 HLRs the trace_to list becomes unreadable and stale trace detection becomes brittle.

### Potential solutions
1. Build a `build_document_digest()` that includes only `rationale + constraint + non_functional` PARAs (skip functional — those are summarised by HLRs). Expect 2–5k chars; well within budget.
2. Drop the single-MODULE constraint; allow the architect to propose N modules. Phase 5 already handles reassigning HLRs across modules.
3. Add an explicit `STALE_ARCHITECTURE` gap type triggered when a significant fraction of HLRs were added after ARCHITECTURE's `created_at`.
4. Cache ARCHITECTURE generation by `hash(sorted(HLR_ids + HLR_contents), model, provider)` — re-runs on unchanged HLRs should skip the LLM call entirely.
5. If keeping single-MODULE: omit the full `trace_to=[all HLR ids]` from the prompt — instead, auto-compute it after the MODULE node is created.

---

## Phase 5 — HLR → MODULE assignment (batch)

- **Pipeline**: `batch_phase5, quality_gaps, combined_quality, semantic` · **Agent**: Design Architect · **Gap**: `UNMODULARISED`
- **Batch context** (`batch_prompts.py:44`): unassigned HLRs (content capped 120 via `_format_node_list`); all MODULEs (fields = `node_id, title, trace_to` — **content not sent**); ARCHITECTURE content first 2000 chars.
- **Prompt**: rule is `graph_add_traces(module_id, [hlr_id])`; create new MODULE only if list empty.
- **Tools**: `graph_add_traces`, `graph_add_node`; `graph_delete` blocked.

### Issues found
1. **[FIXED] MODULE content not sent**: agents assign HLRs based on MODULE *title* + existing `trace_to`. Title is 3–5 words; `trace_to` says "already linked to HLR-0017" but not *why*. This is too little signal for a correct assignment decision.
2. **[FIXED] ARCHITECTURE 2000-char cap truncates the Module Design / Data Flow sections** — those are the sections that actually specify which module does what. Fixed-position truncation hits the most informative text.
3. **[FIXED] Prompt says "Only create a NEW MODULE if the list is completely empty"** (`_modularise`, `task_prompts_authoring.py`) — harsher than the batch prompt which says "if no existing MODULE covers the HLR's concern". The per-gap path effectively forbids architectural splits that may be correct.
4. **[FIXED] HLR content 120-char cap** — acceptable for atomic requirements but will truncate qualifying clauses ("… when the request is authenticated …").
5. **[FIXED] No CONTRACT visibility** — if CONTRACTs exist (Phase 6 may have run for some modules), they are the sharpest signal for responsibility but they are not in the prompt.

### Potential solutions
1. Send MODULE content (even at 600-char preview) instead of just `trace_to`. Or: send CONTRACT titles + first-line responsibility statement for modules that have them.
2. Section-extract ARCHITECTURE by heading ("## Module Design", "## Data Flow") before sending; concat those two sections in full, drop the rest. Much higher signal per token.
3. Reconcile per-gap prompt (`_modularise`) with batch prompt — harmonise on "create new MODULE when no existing is a semantically good fit" with an explicit similarity threshold rule.
4. Embedding-precompute an HLR→MODULE affinity score; batch the top-1 deterministic mappings (above threshold) without LLM, dispatch only the ambiguous remainder. Cuts cost materially on large graphs.
5. When `trace_to` is already populated on a MODULE, show its *first three traced HLRs inline* as exemplars — gives the model a concrete sense of the module's existing scope.

---

## Phase 6 — MODULE → CONTRACT

- **Pipeline**: default · **Agent**: Design Architect · **Gap**: `UNCONTRACTED`
- **Context** (`builder.py:316–323`): ancestor walk (MODULE full content, ARCHITECTURE full, DOCUMENT breadcrumb) + full ARCHITECTURE appended again + all traced HLRs (full content) via `build_traced_hlrs_for_module`.
- **Prompt** (`_contract`, line 193): three steps; content must specify public function signatures, pre/post-conditions, invariants, external dependencies.
- **Tools**: `graph_add_node`.

### Issues found
1. **[FIXED] ARCHITECTURE included twice** — once via ancestor walk, once via explicit append (`builder.py:317–320`). Costs tokens, no information gain.
2. **[FIXED] No sibling CONTRACT visibility**: if other modules already have CONTRACTs, theirs define API conventions (naming, error types, pagination shape). Writing each CONTRACT in isolation is why API drift gets flagged in Phase 13 CROSS_MODULE_COUPLING — preventable.
3. **[FIXED] No LLR hint**: if LLRs already exist (they shouldn't yet at Phase 6, but in a re-run they may), they would sharpen the function signatures the CONTRACT needs to declare. Not consulted.
4. **[FIXED] No Tech Stack pull-through**: ARCHITECTURE has a Tech Stack section, but it's embedded in the full ARCHITECTURE block, so error-handling/auth conventions may be ignored.
5. **[FIXED] No validation that CONTRACT signatures match later DESIGNs**: Phase 13 has `CONTRACT_VIOLATION` but detection depends on the auditor noticing drift. _(Deterministic detector in `backend/quality/signature_validator.py` now emits `CONTRACT_VIOLATION` automatically.)_

### Potential solutions
1. Deduplicate ARCHITECTURE (check if it's already in the ancestor walk before appending). Trivial.
2. Add a `build_sibling_contracts_context()` that sends `(module_title, first_line_of_contract_content)` for every existing CONTRACT — gives the architect a stylistic baseline.
3. Extract and bold-label Tech Stack + Cross-Cutting sections at the top of the context, not buried inside ARCHITECTURE full text.
4. Add an explicit "each public signature in CONTRACT must cite the HLR id it satisfies" output requirement; Phase 13 coverage checks then become deterministic.
5. Have Phase 6 also re-emit `INADEQUATE_CONTENT` for its own MODULE when the MODULE's class plan doesn't include signatures (fixes the bottleneck where MODULEs have bare class plans).

---

## Phase 7 — HLR → LLR refinement (batch)

- **Pipeline**: `batch_phase7, quality_gaps, combined_quality, semantic` · **Agent**: Requirements Engineer · **Gap**: `UNREFINED_HLR`
- **Batch context** (`batch_prompts.py:73`): unrefined HLRs (content 120); all LLRs (parent_id + content 120); MODULE+CONTRACT combined list using fields `node_id, node_type, title, trace_to` — **no content**.
- **Prompt**: reparent existing LLR > create new. LLR must be atomic "The system shall …". Explicitly blocks `graph_read` and `derive_requirement`.
- **Tools**: `graph_add_node`, `graph_reparent_node`; explicit rule in prompt blocks `graph_read`, `derive_requirement`.

### Issues found
1. **[FIXED] CRITICAL: CONTRACT content is withheld**: the CONTRACT lists the public function signatures that LLRs should decompose against; sending only titles + trace_to is insufficient for coherent refinement. This is the single largest context gap in the pipeline.
2. **[FIXED] MODULE content also not sent** — same problem: class plan is invisible, so LLRs may split along irrelevant axes.
3. **`graph_read` blocked by prompt**, so the agent cannot recover — it is forced to refine blind. _(Retained intentionally — all context is now inline; graph_read would be redundant.)_
4. **[FIXED] 120-char LLR content cap**: reparent decisions are made on 120-char excerpts of existing LLRs; since LLRs are "The system shall …" sentences the distinguishing noun phrase may be truncated.
5. **[FIXED] No parent HLR siblings shown**: LLRs under sibling HLRs are surfaced via the global list, but there's no structural grouping — the model must work out from parent_id which HLRs belong to the same module.
6. **[FIXED] Prompt says "usually just 1 per HLR"** (`_llr`, line 225): this is a false constraint. HLRs with multiple obligations legitimately need multiple LLRs; the hint biases toward under-decomposition, which then surfaces as `INCOMPLETE_DECOMPOSITION` in Phase 13.

### Potential solutions
1. **Send full MODULE and full CONTRACT content** (or at least first 2000 chars, matching Phase 8's cap) — blocks the worst quality gap in the pipeline. Change is one field in `batch_phase7::mc`.
2. Group the global LLR list by parent HLR id so the agent reads LLR sets contextually.
3. Raise LLR content cap 120 → 400 in batch; keep global title list compact for retrieval.
4. Remove the "usually just 1" hint from `_llr`; replace with "one LLR per distinct obligation — an HLR with two ANDed clauses needs two LLRs".
5. Use embedding shortlist: for each unrefined HLR, show the 5 most similar existing LLRs in full content; everything else compact. Massive cost + quality win.
6. Stop blocking `graph_read` at all — or replace the block with a softer "all context is inlined, calling graph_read is unnecessary" hint so the agent can recover if the inline snapshot is incomplete.

---

## Phase 8 — LLR → DESIGN (batch with fast-path)

- **Pipeline**: `batch_phase8, quality_gaps, combined_quality, semantic, design_consolidation` · **Agent**: Software Engineer · **Gap**: `UNDESIGNED`
- **Fast path** (`pipeline/dispatch.py::try_fast_trace`, invoked from the batch step): deterministic trace linking for LLRs that map unambiguously to an existing DESIGN. Runs *before* any LLM call.
- **Batch context** (`batch_prompts.py:111, grouped per MODULE`): MODULE content capped 2000; CONTRACT content capped 2000; undesigned LLRs (content 120); existing DESIGNs (fields = `node_id, title, trace_to`, no content).
- **Per-gap fallback** (`build_module_design_context`, `graph_context.py`): MODULE full content + CONTRACT full content + *all* existing DESIGNs with full content.
- **Prompt** (`_design`, `task_prompts_authoring.py`): match LLR to class plan; reuse existing DESIGN via `graph_add_traces`; consolidation rule `#DESIGNs ≤ #classes in class plan`.
- **Post-step**: `design_consolidation` — merges DESIGN sprawl within a MODULE.
- **Tools**: `graph_add_node`, `graph_add_traces`; `graph_delete` blocked.

### Issues found
1. **[FIXED] Batch path omits existing DESIGN content**: agent sees `node_id + title + trace_to` only. It then decides "does any existing DESIGN match this LLR?" — but without reading the DESIGN's actual methods, the decision is a guess. Result: create-new, then design_consolidation post-step merges. Wasteful.
2. **[FIXED] MODULE/CONTRACT 2000-char cap** is consistent but may cut CONTRACT mid-signature on large modules.
3. **[FIXED] No SUITE / CASE awareness**: DESIGNs are generated blind to test strategy and any existing CASE_HLRs on the parent HLR. Test-testable-ness of a DESIGN is never checked until Phase 10 writes a CASE against an implementable API.
4. **No existing workspace code context**: on re-runs after code has been generated, the DESIGN is regenerated as if greenfield — causing Phase 11/12 churn. _(Available via DESIGN.properties.file_path; not yet wired into batch_phase8.)_
5. **[FIXED] `#DESIGNs ≤ #classes` stated in prompt but not enforced in code** — relies on agent compliance. Easier to reject at `graph_add_node` time. _(Runtime validator in `backend/quality/module_validators.py`; rejects via `graph_add_node`.)_
6. **[FIXED] Fast-path is a win but only runs once, pre-loop** — if deletions in subsequent cycles create new LLRs, they don't get fast-path eligibility checks. _(Fast-path now runs at the start of every attempt inside the cycle loop.)_

### Potential solutions
1. **Send existing DESIGN content in batch** (cap at 600 chars each or use embedding top-3 by LLR similarity in full). Single biggest quality win for this phase — prevents duplicate DESIGN creation that consolidation has to clean up.
2. Section-extract CONTRACT by function signature rather than first-2000-char cap; an agent needs the signatures relevant to this LLR, not the opening.
3. Add a `build_related_cases_context()` call — for each LLR, show the CASEs already on its parent HLR (title + objective). Aligns DESIGN with test-reachable behaviour.
4. On re-runs, include the current workspace code for the target class (path already known from `DESIGN.properties.file_path`).
5. Enforce `#DESIGNs ≤ #classes` at the `graph_add_node` validator — treat it as a constraint violation, not a prompt hope.
6. Move fast-path into the cycle loop so every cycle benefits; it's deterministic and cheap.

---

## Phase 9 — PROJECT → SUITE (test strategy)

- **Pipeline**: default · **Agent**: Test Engineer · **Gap**: `UNSUITED`
- **Context** (`builder.py:324–335`): ancestor walk (PROJECT, DOCUMENT breadcrumb) + full ARCHITECTURE + all MODULEs (content capped 200) + all HLRs (content capped 200).
- **Prompt** (`_suite`, line 263): SUITE content describes Scope, Approach, Tools, Entry/Exit criteria; not individual test cases.
- **Tools**: `graph_add_node`.

### Issues found
1. **[FIXED] LLRs not included**: SUITE should cover both HLR-level and LLR-level test strategy but only HLRs are shown. The "Approach" section cannot differentiate abstraction levels because the agent only sees one level.
2. **[FIXED] No CONTRACT content**: testing approach depends on API shapes (sync vs async, streaming, auth modes) — all of which live in CONTRACTs and are invisible here.
3. **[FIXED] 200-char HLR previews obscure NFRs**: "The system shall respond within 200 ms when load is below 100 qps" — the performance criterion (the thing SUITE should specifically address) often sits past char 200.
4. **SUITE is singleton**: no support for multiple SUITEs (e.g., unit vs integration vs acceptance). Once created, no regeneration trigger until it goes stale. _(STALE_SUITE added; multi-SUITE schema deferred.)_
5. **[FIXED] Scope/Coverage is prose, not structural**: SUITE Scope is unstructured text, so Phase 10 cannot programmatically check "is HLR-0042 in scope?".

### Potential solutions
1. Include LLR summary (id + one-line content) and CONTRACT titles + first-line responsibility — full coverage visibility.
2. Raise HLR/MODULE preview cap from 200 → 600 — the existing NFR-truncation problem vanishes.
3. Require SUITE content to include an explicit `## Coverage` section with a machine-readable list of HLR/LLR ids in scope vs out-of-scope. Then `case_trace_coverage` can verify programmatically.
4. Split SUITE into one-per-test-type (SUITE_UNIT, SUITE_INT, SUITE_ACCEPTANCE) so the strategy can be richer per category — low-cost schema evolution.
5. Emit `STALE_SUITE` when significant HLR population change occurs after SUITE creation.

---

## Phase 10 — HLR/LLR → CASE

- **Pipeline**: `batch_phase10, quality_gaps, combined_quality, semantic, case_trace_coverage` · **Agent**: Test Engineer · **Gaps**: `UNTESTED_HLR`, `UNTESTED_LLR`
- **Context** (`builder.py:350–359`): **deliberately shallow** — only `_build_shallow_req_context` (target requirement + direct parent) + existing CASE list (`node_id + trace_to + title[:60]` — **content entirely omitted**). SUITE id passed separately.
- **Prompt** (`_test_hlr`, `_test_llr`): Option A reuse existing CASE only if it *already* functionally covers; Option B create CASE with English verification plan (Objective, Preconditions, Steps, Acceptance). No pytest code, no file_write, ONE tool call max.
- **Post-step** (`case_trace_coverage` → `case_trace_check.py`): per-CASE LLM coverage judge; removes non-covering traces; deletes CASE when all traces bad; guards against removing sole coverage.
- **Tools**: `graph_add_node`, `graph_add_traces`; `graph_read` blocked by prompt; per-gap also omits ancestor walk.

### Issues found
1. **[FIXED] CRITICAL: CASE agent cannot see existing CASE content**: to decide "does existing CASE X already cover my requirement?" the agent gets only `title[:60]`. This is the explicit reason Option A is barely chosen and new CASEs proliferate, which then triggers `case_trace_coverage` post-hoc cleanup.
2. **[FIXED] No DESIGN visibility**: the agent writes test steps without knowing what the code's methods *are*. Steps may reference operations the DESIGN doesn't expose. A Phase 10 CASE may fail at Phase 11/12 codegen alignment.
3. **[FIXED] No sibling CASE content even on same requirement**: two passes create two overlapping CASE_LLRs for the same LLR with no mutual awareness.
4. **[FIXED] `case_trace_check.py:113` silent "assume coverage on error"** — an LLM failure silently marks a CASE as covering, leaving a false-positive trace. Violates the project's "no fallbacks" rule.
5. **[FIXED] Per-CASE 1-LLM-call coverage judge** is expensive at scale (N CASEs × M traces each). _(Now one LLM call per CASE covering all its traces.)_
6. **Ancestor walk skipped** is defensible for speed, but means the agent cannot see *other LLRs under the same HLR*, which is precisely the context needed to avoid duplicate CASE creation. _(Retained intentionally — DESIGN peer now provides the relevant signal.)_
7. **[FIXED] `_build_shallow_req_context` excludes the SUITE Scope**: the agent doesn't know whether this requirement is in unit / integration / acceptance scope.

### Potential solutions
1. **Include existing CASE content in Option-A decision context**: 400-char objective excerpt for cases whose `trace_to` is to the same or adjacent requirement. Expect Option A use to jump from rare to common; Phase 10 cost drops.
2. **Add the owning DESIGN content** (via reverse lookup from LLR parent HLR → MODULE → DESIGN with matching `trace_to`). Test steps then reference real API.
3. **Add sibling-LLR CASE map**: for an HLR-level CASE, show CASEs already on its LLRs; for an LLR-level CASE, show the HLR's CASE_HLR if any — avoids redundant-level testing.
4. **Kill the silent fallback**: on LLM failure in `case_trace_check._check_case_traces`, raise — don't assume coverage. Consistent with project rule.
5. **Batch coverage judge**: one LLM call per CASE with all its traces (instead of per trace); or a SUITE-wide batch "which of these CASE/req pairs don't cover?" Single-call savings scale N×M → N.
6. **Include SUITE Scope** in the CASE-authoring context as a 500-char excerpt pinned to the top.
7. **Option A gating in code**: when the agent calls `graph_add_traces` on an existing CASE, run the coverage judge *before* linking — reject if it doesn't cover, forcing Option B. Moves the check from post-step to pre-commit.

---

## Phase 11 — Render Documentation (deterministic)

- **Pipeline**: dedicated handler `_run_dashboard_phase` (`pipeline/special_phases.py`) — renders the graph as Markdown docs into the workspace `docs/` directory. No agent, no gap type, no context assembly. See `design/21_phase_11_render_documentation.md`.

---

## Phase 12 — Code generation (LLM)

- **Pipeline**: dedicated handler `_run_code_gen_phase` (`pipeline/special_phases.py`) · **Driver**: `backend/codegen/slice_gen.py::run_code_gen` (mission agent — see `design/22_phase_12_generate_code.md`).
- **Context**: DESIGN node content + CONTRACT + any existing workspace file. Stores `file_path` on DESIGN / CASE `properties`.

### Issues found
1. **[FIXED] (Not in scope of context report, but visible from workspace_sync docstring)**: Phase 12 writes to disk; if it fails mid-run, there is no graph-side indicator that the DESIGN node is now out-of-sync with the file. _(Now stamps properties.codegen_error; STALE_CODE gap surfaces it.)_
2. **[FIXED — alternative] No regen-avoidance cache**: unchanged DESIGN content regenerates each run. _(`codegen_hash` stamped on every successful persist; gap analyser emits `STALE_CODE` when stored hash diverges from current inputs. The mission agent itself does not yet short-circuit on matching hashes, but the staleness signal is now available for audit and future wiring.)_

### Potential solutions
1. On codegen failure, stamp `DESIGN.properties.codegen_error` and emit a `STALE_CODE` gap so the audit catches it.
2. Hash-cache codegen output keyed on `(design_content, contract_content, model)` — skip LLM call when cached.

---

## Phase 13 — Workspace sync (deterministic, no LLM)

- **Pipeline**: `workspace_sync`, then `record_results_step` (heals misparented RESULTs and records fresh RESULT nodes after TEST sync — design/23).
- **Behaviour** (`workspace_sync.py`): for every DESIGN with `properties.file_path`, read the file, create a CODE child node linking to it. Same for CASEs → TEST nodes.
- **No context assembly** — pure filesystem→graph sync.

### Issues found
1. **[FIXED] No feedback loop**: if a workspace file is missing or unreadable, the loop silently skips (no gap emitted, no log warning visible in user UI). _(MISSING_CODE gap now emitted.)_
2. **[FIXED] No diff-detection**: if the workspace file exists but diverges from what the DESIGN specified, the CODE node is created from the file as-is — drift is laundered as compliance. _(file_hash stored; content refreshed when changed.)_
3. **[FIXED] `_has_child_of_type` idempotency** means an updated file after re-run doesn't refresh the CODE node content.
4. **[FIXED] Phase is named "Quality Audit" in README** but the code runs only `workspace_sync`. Docs and code disagree.

### Potential solutions
1. When `file_path` is set but file is missing, emit `UNSYNCED_DESIGN` (or `MISSING_CODE`) gap. Loud failure per project rule.
2. On re-run, recompute `CODE.properties.file_content` from disk; update if changed, emit `STALE_CODE` if drift from DESIGN detected.
3. Fix the README ↔ code mismatch: either rename phase 13 to "Workspace Sync" in README, or run an auditor step after sync as originally intended.

---

## Cross-cutting mechanisms

### [FIXED] `build_trace_to_context` silent fallback
`graph_context.py::build_trace_to_context` — returned an ancestor walk when trace_to refs are empty or missing. **Fix applied**: empty trace_to returns `""`; unresolved refs raise `RuntimeError`.

### [FIXED] `case_trace_check` silent fallback
`case_trace_check.py:113` — LLM failure marks CASE as covering. **Fix applied**: the except-swallow removed; failures propagate.

### [FIXED] 40k-char hard cap
`builder.py:368` — tail-chop replaced with `context_budget.pack()` which drops whole lowest-priority sections by tiktoken count.

### [FIXED] Content-truncation policy is inconsistent
All `[:N]` caps removed from `prompting/builder.py`/`prompting/graph_context.py` and `prompting/batch_prompts.py`. Full content everywhere; priority budget absorbs the consequences at scale.

### Prompt caching is not exploited across retries
_(Structural prep done: batch prompts split into static + dynamic sections. Provider-level `cache_control` wiring pending proxy support.)_

### [FIXED] Missing peer-artefact context is systemic
`build_peer_contracts_context`, `build_design_for_llr`, `build_cases_for_requirement`, `build_document_digest`, `build_sibling_paras_context`, `build_all_llrs_context` added and wired across phases 3, 6, 7, 8, 9, 10.

### [FIXED — alternative] No embedding infrastructure for shortlisting
A BM25 shortlist helper (`context_selection.select_relevant`, `rank_bm25.BM25Okapi`) was prototyped and later **removed as unused** — the priority budget keeps full landscape views under budget at current graph sizes. Its intended shape is recorded in design/02 §"Relevance-based selection" should it become necessary.

---

## Highest-impact improvements (ranked)

1. **[FIXED] Send existing DESIGN / CASE / CONTRACT content in batches that currently send only titles** (Phases 7, 8, 10). Each is a one-line fix with big quality wins and directly unlocks reparent/reuse paths.
2. **[FIXED] Add peer-artefact context**: DESIGN sees SUITE+CASEs; CASE sees DESIGN; CONTRACT sees sibling CONTRACTs. Single helper, applied uniformly.
3. **[FIXED] Kill silent fallbacks**: `build_trace_to_context` ancestor substitution (line 94) and `case_trace_check` "assume coverage on error" (line 113). Both are one-line fixes that align with the project's stated principles.
4. **[FIXED] Whitepaper rationale digest for Phase 4**: NFR/constraint/rationale-only PARAs included; functional PARAs excluded (already summarised by HLRs).
5. **[FIXED — alternative] Embedding shortlist + full content for top-K** replaces the "all X with 120-char preview" pattern across 5 phases. _(A BM25 helper was prototyped and removed as unused; the priority budget keeps full listings under the 120k token cap.)_
6. **[FIXED] Unify and raise content caps**: central preview helper; 120-char cap is the single biggest quality-killer for dedup decisions — raise to 400–600 where budget allows. _(All `[:N]` caps removed; zero-truncation policy enforced; priority-aware budget drops whole sections if needed.)_
7. **[FIXED] Structural Coverage in SUITE** (machine-readable in-scope HLR/LLR list) enables Phase 10 programmatic check vs LLM coverage judge.
8. **Prompt-cache-aware batch layout**: split static node-list section from dynamic gap section, set cache breakpoints. Cuts retry cost and latency. _(Structural split done in `batch_prompts.py`; actual cache_control wiring depends on the OpenAI-compat proxy supporting pass-through.)_
9. **[FIXED] Fix README ↔ Phase 13 mismatch** and document Phase 11.
10. **[FIXED — alternative] Content-hash caching** for ARCHITECTURE (Phase 4) and code gen (Phase 12) outputs — skip regeneration on unchanged inputs. _(codegen_hash stamped and validated by the gap analyser as STALE_CODE on input drift; ARCHITECTURE-level hashing is a separate follow-up when Phase 4 regeneration proves expensive.)_
