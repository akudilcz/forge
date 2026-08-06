# Context Management

Living reference for how agent context is assembled across all pipeline phases. Describes the zero-truncation policy, priority-aware budget enforcement, peer-artefact helpers, and supporting infrastructure.

## Principle: zero truncation

We never truncate node content mid-string. Every node that appears in an agent's context appears in full. When a listing would otherwise exceed the token budget, we **drop whole lower-priority sections** or **select fewer candidates by relevance**, but we do not produce partial sentences or `...`-suffixed previews. The motivation is one-line: truncating the last clause of a requirement is exactly the clause that would have disambiguated it.

## Section-based context assembly

Per-gap context is built by `backend/prompting/builder.py::build_context_for_gap()`. Each builder emits zero or more `Section(priority, name, text)` entries, and the final string is produced by `context_budget.pack()`.

### Priority scale (`context_budget.py`)

| Const | Value | Intended use |
|---|---|---|
| `P_TARGET` | 100 | The node the gap is about |
| `P_TARGET_PARENT` | 95 | Direct parent of the target |
| `P_TRACE_TO` | 90 | Nodes the target traces to |
| `P_PEER_ARTEFACT` | 80 | CONTRACT for DESIGN, DESIGN for CASE, SUITE for CASE |
| `P_SIBLING_FOR_DEDUP` | 70 | Same-type siblings for duplicate checks |
| `P_ANCESTOR_CHAIN` | 60 | Ancestor walk beyond direct parent |
| `P_EXISTING_PEERS` | 50 | Existing LLRs / CASEs / DESIGNs listing |
| `P_LANDSCAPE` | 40 | Global views (all HLRs, all MODULEs) |
| `P_WHITEPAPER_DIGEST` | 30 | NFR / rationale / constraint PARAs |
| `P_BACKGROUND` | 10 | Anything else |

### Budget enforcement

`pack(sections, budget_tokens=...)`:

1. Counts tokens via `tiktoken` (`cl100k_base` encoding — a reasonable proxy for Claude).
2. Sums section token counts plus separator tokens.
3. If over budget, finds the section with the lowest `priority` (insertion order as tie-break) and drops it whole.
4. Repeats until the total fits.
5. Logs the dropped sections for telemetry.

Sections always appear in their **original insertion order** in the packed output; priority only affects the drop decision.

Default budget is 120 000 tokens (Claude 200 k window minus ~80 k headroom for system prompt, scratchpad, tool schema, and response).

## Relevance-based selection (no truncation)

Some listings can legitimately exceed the budget at scale — e.g. thousands of HLRs at Phase 4. A BM25 pre-selection helper was prototyped for this and removed as unused; if the priority budget stops being sufficient, the shape it would take is:

* Tokenises content with a simple alphanumeric regex.
* Score with a pure-Python BM25 (no model download).
* Returns the top-K candidates **in full content**.

Callers then wrap the selected candidates back into a `Section`. The selector never alters content — it only chooses which candidates to include.

## Markdown section extraction

`markdown_sections.extract_sections(text, headings)` wraps `langchain_text_splitters.MarkdownHeaderTextSplitter` to pull named sections out of long markdown documents (e.g. ARCHITECTURE) without positional slicing. Used by Phase 6 to surface Tech Stack + Cross-Cutting Concerns at the top of the CONTRACT-authoring context.

## Peer-artefact helpers (`prompting/graph_context.py`, re-exported by `prompting/builder.py`)

Each returns full content, with no caps:

| Helper | Phases | Returns |
|---|---|---|
| `build_all_hlrs_context` | 4, 9 | Every HLR with content |
| `build_all_llrs_context` | 9 | Every LLR with content |
| `build_all_modules_context` | 9 | Every MODULE with content |
| `build_existing_llrs_context` | 7 (per-gap) | Every LLR with parent + content |
| `build_existing_cases_context` | 10 | Every CASE (of the given type) with full content |
| `build_peer_contracts_context` | 6, 9 | Every CONTRACT, optionally excluding one module |
| `build_traced_hlrs_for_module` | 6 | HLRs the MODULE traces to |
| `build_module_design_context` | 8 (per-gap) | Owning MODULE + CONTRACT + existing DESIGNs |
| `build_design_for_llr` | 10 | DESIGN(s) under the LLR's MODULE |
| `build_cases_for_requirement` | (reusable) | CASEs that trace to the given req |
| `build_document_digest` | 4 | Rationale + constraint + NFR PARAs |
| `build_sibling_paras_context` | 3 | Same-parent PARAs of the target |
| `build_sibling_req_context` | 13 DUPLICATE | Same-type, same-parent siblings |
| `build_ancestor_context` | default | Full parent chain (DOCUMENT body skipped; breadcrumb only) |

## Agent conversation threads (per-gap scoping)

Agent-dispatch conversations are checkpointed by `backend/pipeline/phase_context.py::PhaseContext` (a `MemorySaver` shared across agents) and addressed by `thread_id`. A measured audit found 90–98% of per-gap dispatch tokens were re-sent dead conversation history: threads were scoped per `(phase, gap_type)`, so every gap of a type appended to one unbounded transcript and each new dispatch re-sent all prior gaps' transcripts (single-node repairs carried up to 106K tokens).

Threads are therefore scoped **per gap**:

* `get_thread_id(phase, gap_type, scope)` — `scope` is the gap's `node_id` for per-gap dispatch (`pipeline/dispatch.py`), so each gap starts a clean transcript containing only its own task. The ID is attempt-agnostic: **retries of the same gap reuse the same thread**, keeping the genuinely useful history of that gap's earlier attempts.
* Batch steps (`pipeline/batch_steps.py`) pass the fixed scope `"batch"` — one thread per `(phase, gap_type)` batch step, unchanged behaviour.
* `reset_phase` / `reset_all` still invalidate all threads via the nonce.

**Follow-up (not built yet)**: cross-gap learning within a phase is no longer carried implicitly by the shared transcript. The audit showed most of that "learning" was pattern-shortcutting rather than useful transfer, so no summary mechanism is built in this round. If cross-gap transfer proves valuable, the shape would be a cheap per-(phase, gap_type) rolling summary injected into each fresh thread's first message — not a shared transcript.

## Dispatch trim budget

`make_trim_hook(budget_tokens)` (`pipeline/phase_context.py`, wired as the react agent's `pre_model_hook` in `agents/factory.py`) trims the oldest conversation messages before every LLM call:

* **Exact token counting**: the counter is the same tiktoken `cl100k_base` `count_tokens` used by `context_budget.pack()`. The previous hook used LangChain's `"approximate"` counter, which undercounted — real prompts hit 106K tokens against an 89.6K intended cap.
* **Explicit configured budget**: the cap is `llm.dispatch_token_budget` (`LLMConfig`, default **24 000** tokens), task-scaled rather than %-of-context-window. A per-gap repair task never legitimately needs anywhere near a model window of history.
* **Loud but non-fatal**: over-budget history is trimmed (system message preserved, trimmed list starts on a human message) and logged; the hook never crashes mid-loop.

## Batch prompts

The batch phases (3, 5, 7, 8, 10) follow a `[static prefix] + [dynamic suffix]` structure where the static prefix is the large cacheable graph snapshot and the dynamic suffix is the chunk-specific set of unresolved items. Retries inside a batch step benefit from Anthropic's prompt cache on the static prefix. Full content everywhere — no `[:N]` slices anywhere in `batch_prompts.py`.

**Chunked batch authoring**: a single batch call whose *output* scales with item count truncates at the provider's output-token limit on large documents. Live evidence (trie wave-3 resume, `trace.1614841.jsonl`): 46+ PARAs → 123 HLRs; phase 3's single batch response hit the output cap, the last PARAs never received HLRs, all 3 batch attempts exhausted, and the phase halted `awaiting_approval` with `UNCOVERED_PARA` gaps for PARA-0183/PARA-0185 — zero per-gap dispatches in a 42-call trace. Same truncation economics already fixed for the combined judge (`quality_judge_batch_size`, commit 07aa75a).

The fix mirrors the judge: the phases whose per-item output scales with item count — 3 (one+ HLR per PARA), 5 (one trace call per HLR), 7 (one+ LLR per HLR), 10 (one CASE per requirement) — split the unresolved item list into chunks of `LLMConfig.batch_author_chunk_size` (default **20**) and make one LLM call per chunk. Phase 8 is naturally chunked per-MODULE and keeps that grouping. Rules:

* The **static prefix is snapshotted once per step invocation** and byte-identical across every chunk call and retry — only the dynamic item list varies, so provider caching keeps amortising the graph snapshot.
* **Attempts are counted per chunk** (`_MAX_BATCH_ATTEMPTS = 3` per chunk), so one stubborn chunk cannot starve the others: a failed or truncated chunk retries with only its own unresolved items.
* **Straggler fallback**: items still unresolved after a chunk's attempts exhaust are handed to the per-gap structural dispatch loop (`_fallback_structural` → `steps.structural`). A batch phase never ends with undispatched structural gaps.

**Dedup judge alignment**: the semantic duplicate judge's pair prompt is likewise ordered `[system + SIBLINGS]` static prefix then `[TARGET]` dynamic suffix, so the prefix is shared across targets under one parent and across the byte-identical double-confirmation call (independence unaffected — see design/01 §7.4).

**Wire-up note**: the structural split is in place in `batch_prompts.py`. Actual `cache_control` block emission depends on the provider — the project runs through an OpenAI-compatible proxy (LiteLLM). When the proxy is configured to pass `cache_control` through to an Anthropic backend, the static prefix naturally lines up as the cache breakpoint. No per-phase code change is required beyond the prompt structure already in place.

## Remaining follow-ups

The following items are tracked but depend on schema or orchestration changes outside the context-assembly surface:

* **Per-heading document chunking for very large whitepapers** — infrastructure is available via `markdown_sections.extract_sections`; wiring it into Phase 2 as a sub-gap emitter is a follow-up when document sizes demand it.
* **Multiple SUITEs per test type** (`SUITE_UNIT`, `SUITE_INT`, `SUITE_ACCEPTANCE`) — requires a `suite_type` property on SUITE nodes and gap-detector updates. `STALE_SUITE` already exists for the singleton SUITE.

## Fail-loud on unresolved references

`build_trace_to_context` raises `RuntimeError` when `trace_to` references cannot be resolved, instead of silently substituting an ancestor walk. `case_trace_check` never assumes coverage on LLM failures: transport exceptions propagate, and a response with missing verdicts (empty/truncated body) is retried exactly once — any verdict still missing after the retry leaves its trace **kept and marked unverified** with an ERROR log (absent evidence never justifies destructive trace removal). Both changes align with the project "no fallbacks" rule: mis-wired state surfaces loudly, not as a quiet degradation.

## STALE_NODE repair context

The `STALE_NODE` task description (`task_prompts_repair.py::_stale_node`) is self-sufficient: it inlines the staleness reason (`Gap.description`, carrying the provenance-hash mismatch explanation and the parent id from `Gap.context`), and — because the ancestor-chain context starts at the node itself — the node's own content and the parent's current content are already present in the packed context (token-budgeted by `context_budget.pack`, never truncated mid-string). The prompt tells the agent both blocks are in context and forbids redundant `graph_read` round-trips, and names `graph_refresh_provenance` explicitly for the "content still valid" outcome (which records the review without touching content). When no context could be assembled, the prompt falls back to explicit read instructions — stated, not silent.

## Staleness detectors

`gap_analyser.py` (staleness checks in `gap_analyser_staleness.py`; the per-node `STALE_NODE` check lives in `gap_analyser_integrity.py` and compares the child's `properties.derived_from_hash` provenance stamp against the SHA-256 of the parent's current content, so metadata/trace-only parent touches never cascade — see design/01 §2.6) emits:

* `STALE_ARCHITECTURE` when >20% of current HLRs were added after the ARCHITECTURE's `created_at`.
* `STALE_SUITE` when >20% of current HLRs+LLRs were added after a SUITE's `created_at`.
* `STALE_CODE` when a DESIGN/CASE carries `properties.codegen_error`.
* `MISSING_CODE` emitted by `workspace_sync` when a declared `file_path` is missing from disk.

## Dependencies

Pure-Python deps for zero-truncation selection:

* `tiktoken` (token counting — already required for the agent stack)
* `langchain-text-splitters` (markdown heading extraction)

No embeddings, no GPU, no external service calls in the context-assembly path.

---

## Related documents

* [03_phase_context_map.md](03_phase_context_map.md) — per-phase inventory and identified issues (ground truth for the refactor).
