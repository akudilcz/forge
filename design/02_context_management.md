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

## Peer-artefact helpers (`prompting/builder.py`)

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

## Batch prompts

The four batch phases (3, 5, 7, 8) follow a `[static prefix] + [dynamic suffix]` structure where the static prefix is the large cacheable graph snapshot and the dynamic suffix is the attempt-specific set of unresolved gaps. Retries inside a batch step benefit from Anthropic's prompt cache on the static prefix. Full content everywhere — no `[:N]` slices anywhere in `batch_prompts.py`.

**Wire-up note**: the structural split is in place in `batch_prompts.py`. Actual `cache_control` block emission depends on the provider — the project runs through an OpenAI-compatible proxy (LiteLLM). When the proxy is configured to pass `cache_control` through to an Anthropic backend, the static prefix naturally lines up as the cache breakpoint. No per-phase code change is required beyond the prompt structure already in place.

## Remaining follow-ups

The following items are tracked but depend on schema or orchestration changes outside the context-assembly surface:

* **Per-heading document chunking for very large whitepapers** — infrastructure is available via `markdown_sections.extract_sections`; wiring it into Phase 2 as a sub-gap emitter is a follow-up when document sizes demand it.
* **Multiple SUITEs per test type** (`SUITE_UNIT`, `SUITE_INT`, `SUITE_ACCEPTANCE`) — requires a `suite_type` property on SUITE nodes and gap-detector updates. `STALE_SUITE` already exists for the singleton SUITE.

## Fail-loud on unresolved references

`build_trace_to_context` raises `RuntimeError` when `trace_to` references cannot be resolved, instead of silently substituting an ancestor walk. `case_trace_check` no longer assumes coverage on LLM failures — the exception propagates. Both changes align with the project "no fallbacks" rule: mis-wired state surfaces as a real error, not a quiet degradation.

## Staleness detectors

`gap_analyser.py` emits:

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
