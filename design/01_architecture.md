# FORGE -- Architectural Design

FORGE transforms a white paper into fully traced, tested code through a pipeline of LLM agents operating on a Project Graph. Three concepts govern the design: **gaps**, **phases**, and **context**.

Six principles apply throughout:

1. **No cycle cap.** The system assumes convergence. If an agent cycles, the fix is better context, not an iteration limit.
2. **One agent per phase**, not one per role. The agent is rebuilt at each phase boundary.
3. **Quality is inline.** No separate quality pass or role. Quality gaps surface in the phase that created the affected node type.
4. **Trimming via pre_model_hook**, not thread rotation. Deterministic oldest-first trim, no summarisation.
5. **Context is curated, not discovered.** Agents never search the graph. Every piece of information is pre-assembled by gap type.
6. **Architecture before LLRs.** The full architectural skeleton (ARCHITECTURE, MODULEs, CONTRACTs) is established before detailed requirements are elaborated.

---

## 1. Core Concepts

### 1.1 Gaps

A _gap_ is a typed, actionable deficiency in the Project Graph. Gaps are the only unit of work in FORGE -- there are no task queues, workflow definitions, or hand-written orchestration scripts. The system detects gaps, dispatches them to an agent, the agent resolves them by mutating the graph, and the system re-scans for new gaps. This observe-act loop is the entire engine.

**Detection has two layers.** The Gap Analyser is a pure, deterministic function -- the same graph always produces the same gap list. It detects structural completeness ("this HLR has no LLR children") and integrity violations ("this node's parent doesn't exist"). But agents also find gaps that static analysis cannot: is this requirement atomic? Are these two HLRs semantically the same? Does this function actually implement the LLR it traces to? Both layers feed into the same dispatch loop. A gap is a gap regardless of how it was found.

**There are three families of gaps:** structural (missing nodes), quality (integrity violations and content problems), and code generation (Phase 12 workspace issues). Full taxonomy tables appear in section 3.

### 1.2 Phases

A _phase_ is the unit of orchestration. It defines what work happens, in what order, with what agent configuration. Phases enforce the architecture-first discipline: the full architectural skeleton is built in Phases 4-6 _before_ detailed requirements are elaborated in Phase 7.

| Phase | What It Does | How |
|-------|-------------|-----|
| 0 | Create PROJECT node | Human-initiated |
| 1 | Ingest `forge.md` into DOCUMENT | Deterministic file read |
| 2 | Parse DOCUMENT into PARA tree | Agent (structural dispatch) |
| 3 | Derive HLRs from paragraphs | Agent (batch dispatch) |
| 4 | Create ARCHITECTURE decomposition | Agent (structural dispatch) |
| 5 | Assign HLRs to MODULEs | Agent (batch dispatch) |
| 6 | Write CONTRACT for each MODULE | Agent (structural dispatch) |
| 7 | Derive LLRs from HLRs | Agent (batch dispatch) |
| 8 | Create DESIGNs for LLRs | Agent (batch dispatch, per-MODULE) |
| 9 | Write test strategy (SUITE) | Agent (structural dispatch) |
| 10 | Write test cases | Agent (structural dispatch) |
| 11 | Render graph as Markdown docs | Deterministic template render |
| 12 | Generate source code and tests | Mission agent (continuous ReAct) |
| 13 | Link workspace files to graph nodes | Deterministic file scan |
| 14 | Build deliverables ZIP | Deterministic packaging |

There is one agent per phase, not one per role. The agent is a LangGraph ReAct agent rebuilt at each phase boundary with a phase-specific system prompt, a fresh conversation, and tools whitelisted by gap type. Quality gaps are handled by the same phase agent -- it already has the domain context to decide whether a stale node should be refreshed or a duplicate deleted.

### 1.3 Context

An agent's effectiveness depends entirely on the context it receives. FORGE manages context at two scales: **per-gap** (what the agent sees for a single dispatch) and **per-phase** (what the agent accumulates across dispatches within a phase).

Per-gap context is curated, not discovered. Agents never search the graph. Every piece of information an agent sees is explicitly assembled before dispatch based on the gap type. The principle: give the agent exactly what it needs to make a good decision, and nothing else.

Within a phase, agents accumulate conversation history across gap dispatches. At phase boundaries, history is discarded entirely. Within a phase, when accumulated conversation exceeds 70% of the context window, the oldest message pairs are trimmed -- deterministic, fast, no summarisation. Full context tables appear in section 5.

---

## 2. Project Graph

### 2.1 Core Model

The FORGE project graph is a **single-rooted tree**. Every artefact is a node with exactly one structural parent (`parent_id`). Cross-branch semantic references use `trace_to`.

| Field | Cardinality | Purpose |
|-------|-------------|---------|
| `parent_id` | Exactly one | Structural ownership -- defines identity, position, and containment |
| `trace_to` | Zero or more | Cross-branch semantic references (see section 2.4) |

### 2.2 Node Types

16 active types across 9 abstraction layers (layers 0-8).

| Layer | Type | Role |
|-------|------|------|
| 0 | **PROJECT** | Root. One per workspace. |
| 1 | **DOCUMENT** | Source specification document. |
| 1 | **PARA** | Addressable paragraph within a document. |
| 2 | **HLR** | High-level system requirement derived from a paragraph. |
| 2 | **LLR** | Low-level software requirement derived from an HLR, informed by the architecture. |
| 3 | **ARCHITECTURE** | System design document. Captures the overall decomposition rationale. |
| 4 | **MODULE** | Major component or subsystem. Owns a set of HLRs and one CONTRACT. |
| 4 | **CONTRACT** | Interface specification of a module. |
| 5 | **DESIGN** | Design specification for an implementation unit. Traces to the LLRs it implements. |
| 5 | **CODE** | Workspace file reference linked to a DESIGN node. |
| 6 | **SUITE** | Test strategy document. |
| 6 | **CASE_HLR** | Test case verifying a high-level requirement (traces to HLR). |
| 6 | **CASE_LLR** | Test case verifying a low-level requirement (traces to LLR). |
| 6 | **TEST** | Workspace test file reference linked to a CASE node. |
| 7 | **RESULT** | Execution outcome. Immutable. |
| 8 | **RECORD** | Assurance record (review, baseline, problem, change). |

### 2.3 Parent Rules

| Node Type | Parent | Notes |
|-----------|--------|-------|
| PROJECT | -- | Only root node |
| DOCUMENT | PROJECT | One or more source documents |
| PARA | DOCUMENT or PARA | Nested sections allowed |
| HLR | PARA | Derived from source paragraph |
| LLR | HLR | Elaborated after architecture is defined |
| ARCHITECTURE | PROJECT | One per project initially |
| MODULE | ARCHITECTURE | One module per major component |
| CONTRACT | MODULE | Interface specification owned by the module |
| DESIGN | MODULE | Design specification for an implementation unit |
| CODE | DESIGN | Workspace source file reference |
| SUITE | PROJECT | Top-level test collection |
| CASE_HLR | SUITE | HLR-level test case |
| CASE_LLR | SUITE | LLR-level test case |
| TEST | CASE_HLR or CASE_LLR | Runnable test code |
| RESULT | TEST | Execution outcome (immutable leaf) |
| RECORD | any node | Assurance artefact |

### 2.4 Trace Pairs

`trace_to` is used for exactly **five cross-branch relationships**. No other `trace_to` entries should be created.

| Source | Target | Meaning |
|--------|--------|---------|
| ARCHITECTURE | HLR (one or more) | Architecture was designed to address these HLRs |
| MODULE | HLR (one or more) | This module addresses these HLRs |
| DESIGN | LLR (one or more) | This design spec implements these LLRs |
| CASE_HLR | HLR | This test case verifies a high-level requirement |
| CASE_LLR | LLR | This test case verifies a low-level requirement |

### 2.5 Node ID Scheme

All node IDs use a simple sequential format: `{NODE_TYPE}-{seq:04d}`. Examples: `PROJECT-0001`, `HLR-0007`, `CASE_HLR-0042`. A global counter per node type is maintained in the `pg_node_sequences` table. The counter is monotonically increasing and never reused. Structural lineage and ownership are encoded in `parent_id` and `trace_to`, not in the ID.

### 2.6 Staleness and Change Propagation

When a node's content changes its `content_hash` is recomputed. Impact propagates **downward** through structural children, marking each descendant stale. CONTRACT changes additionally stale all sibling DESIGNs in the same MODULE. Propagation stops at RESULT nodes.

Staleness is **provenance-hash based**: every child node carries `properties.derived_from_hash` — the SHA-256 of the parent `content` the child was authored against. The graph engine stamps it automatically (agents never supply it):

* **create** (`add_node`): stamped from the live parent's current content.
* **content update** (`update_node` with changed content): re-stamped — the child was just re-authored against the current parent.
* **metadata-only update** (properties, `trace_to`): the existing stamp is carried over, even when the caller supplies a replacement properties bag that omits it.
* **reparent**: re-stamped against the new parent, so a deliberate move never triggers a stale storm by itself.

`STALE_NODE` fires **iff** the stored `derived_from_hash` differs from the SHA-256 of the parent's *current* content. Because the hash covers content only, metadata/trace/title touches of a parent (e.g. DOCUMENT chunk bookkeeping in phase 2) can never cascade staleness onto children. Repair is closed deterministically: rewriting the child's content re-stamps as a side effect, and a "reviewed, no change needed" verdict is a free `graph_refresh_provenance` call — never a paid LLM no-op.

**Backfill rule (LOUD)**: legacy nodes without `derived_from_hash` are stamped during schema migration (`_migrate_derived_from_hash`), treating the parent's *current* content as the provenance — the only defensible baseline, since the historical parent content is unknowable. Each backfill run logs the stamped-node count via `forge_logger` at WARNING so it is visible in the build log; the analyser additionally logs (and emits no gap) if it ever meets an unstamped node mid-run.

The `content_updated_at` column and its engine bookkeeping are retained, but staleness logic no longer reads it. *(Follow-up: `content_updated_at` may now be redundant and could be dropped once no other consumer appears.)*

Workspace-sync node types (CODE, TEST, RESULT) are exempt from staleness detection. Their parents are routinely updated with metadata that does not invalidate child content. Validity of CODE and TEST is governed by `UNSYNCED_DESIGN` and `UNSYNCED_TEST` gap checks.

### 2.7 Implementation

| Concern | Detail |
|---------|--------|
| Persistence | SQLite `pg_nodes` -- `trace_to` is a first-class column (JSON array), not inside the `properties` blob |
| In-memory graph | NetworkX DiGraph -- parent/child via `parent_id`; supplementary edges in `pg_edges` table |
| Trace validation | Validated against allowed (source_type, target_type) pairs on write |
| Reverse lookup | `nodes_tracing_to(target_id, source_type)` -- O(n) scan of in-memory graph |
| Concurrency | Single asyncio writer lock -- all mutations serialised |
| Sync discipline | Write SQLite first, then update NetworkX |

---

## 3. Gap Analyser

### 3.1 Two-Layer Detection

**Layer 1 -- Deterministic.** A pure function with no LLM calls and no side effects. Given a `ProjectGraph`, it returns a sorted list of `Gap` objects covering structural completeness and integrity violations. The same graph always produces the same gap list.

**Layer 2 -- LLM-driven.** Agents find quality issues that static analysis cannot: atomicity, semantic duplication, consistency with traced requirements. These checks run as pipeline steps within each phase and produce the same `Gap` objects.

### 3.2 Structural Gaps

Structural gaps represent missing nodes in the graph. They are strictly priority-ordered -- lower phases block higher ones.

| Priority | Gap Type | Phase | Condition |
|----------|----------|-------|-----------|
| 1 | `UNCHUNKED_DOCUMENT` | 2 | DOCUMENT has no PARA children |
| 2 | `UNCOVERED_PARA` | 3 | Non-heading PARA with body content has no HLR child |
| 3 | `UNARCHITECTED` | 4 | PROJECT has no ARCHITECTURE child |
| 4 | `UNMODULARISED` | 5 | HLR not referenced by any MODULE.trace_to |
| 5 | `UNCONTRACTED` | 6 | MODULE has no CONTRACT child |
| 6 | `UNREFINED_HLR` | 7 | HLR has no LLR children |
| 7 | `UNDESIGNED` | 8 | LLR not referenced by any DESIGN.trace_to |
| 8 | `UNSUITED` | 9 | PROJECT has no SUITE child |
| 9 | `UNTESTED_HLR` | 10 | HLR not referenced by any CASE_HLR.trace_to |
| 10 | `UNTESTED_LLR` | 10 | LLR not referenced by any CASE_LLR.trace_to |
| 12 | `UNSYNCED_DESIGN` | 13 | DESIGN has no CODE child |
| 13 | `UNSYNCED_TEST` | 13 | CASE has no TEST child |

The priority chain encodes architecture-first discipline: Phases 4-6 establish ARCHITECTURE, MODULEs, and CONTRACTs before Phase 7 writes LLRs with full visibility of module boundaries and interface contracts.

### 3.3 Quality Gaps

Quality gaps represent integrity violations and content problems. They surface **inline within the phase where the affected node type was created** -- not in a separate quality phase. All share priority 13 (`MAINTENANCE`).

**Graph integrity** -- structural soundness of the graph. Cheap deterministic checks.

| Gap Type | Meaning | Detection |
|----------|---------|-----------|
| `STALE_NODE` | Child's `derived_from_hash` differs from hash of parent's current content | Deterministic |
| `ORPHAN_NODE` | Parent missing or wrong type | Deterministic |
| `EMPTY_CONTENT` | Non-container node with no content | Deterministic |
| `STALE_TRACE_TO` | Trace references non-existent node | Deterministic |
| `EMPTY_TRACE` | MODULE or DESIGN traces to nothing | Deterministic |
| `CIRCULAR_TRACE` | trace_to chain forms a cycle | Deterministic |
| `DUPLICATE_NODE` | Exact-hash or semantic duplicate. PARA nodes are exempt from the exact-hash check: they mirror document structure, whose identity is position + title, not body — a whitepaper may legitimately repeat a sentence in two sections, and heading PARAs are empty by design | Deterministic + LLM |
| `UNTITLED_NODE` | Missing or too-long title | Deterministic |

**Requirement quality** -- the foundation of everything downstream. If an HLR is vague, every LLR, DESIGN, and test case derived from it will be wrong.

| Gap Type | Meaning | Detection |
|----------|---------|-----------|
| `MALFORMED_REQUIREMENT` | Doesn't start with "The system shall" | Deterministic |
| `NON_ATOMIC_REQUIREMENT` | Covers multiple obligations -- must be split | LLM |
| `NON_EARS_REQUIREMENT` | Doesn't follow EARS template | LLM |
| `VAGUE_REQUIREMENT` | Ambiguous language with no measurable criteria | LLM |
| `UNTESTABLE_REQUIREMENT` | No observable outcome for testing | LLM |
| `CONTRADICTORY_REQUIREMENTS` | Two sibling requirements conflict | LLM |
| `INCOMPLETE_DECOMPOSITION` | HLR's LLRs don't fully cover the HLR given CONTRACT/DESIGN context | LLM |

The `INCOMPLETE_DECOMPOSITION` check is context-sensitive: a simple HLR with one LLR is fine. But if the MODULE's CONTRACT specifies multiple interfaces and the HLR covers a broad concern, the LLRs should decompose it into testable parts that cover each interface. The LLM judges adequacy relative to the architectural context.

**Content adequacy** -- catches stubs and skeletal content before they propagate through the pipeline.

| Gap Type | Meaning | Detection |
|----------|---------|-----------|
| `INADEQUATE_CONTENT` | Content too short or too vague to be actionable | Deterministic (length) + LLM |
| `INCONSISTENT_CONTENT` | Content inconsistent with traced requirement | LLM |

**Architectural conformance** -- enforces module boundaries that downstream work must respect.

| Gap Type | Meaning | Detection |
|----------|---------|-----------|
| `CONTRACT_VIOLATION` | DESIGN contradicts the CONTRACT's declared public surface. When the CONTRACT carries structured `properties.public_api` (design/16), a DESIGN violates it **only** when it declares an annotated signature reusing a public function's name and none of its declarations for that name agrees with the `public_api` signature (parameter-name sequence must match; return types compared only when both sides state one). Internal helpers — any name not in `public_api` — are never violations: DESIGNs legitimately specify private classes and methods the CONTRACT never lists. Whether a DESIGN *claims* an unlisted symbol as public is not deterministically detectable (DESIGNs mark publicness in free prose only); that direction is owned by the phase-12 API-surface gate. Legacy contracts without `public_api` keep the older token-subset check: PEP-8-adjacent `name(` tokens (skipping private/dunder names and prose parentheticals) found in the DESIGN but absent from the CONTRACT text | Deterministic |
| `CROSS_MODULE_COUPLING` | DESIGN references internals of another MODULE | LLM |

Container types (PROJECT, DOCUMENT, ARCHITECTURE, SUITE) are exempt from `EMPTY_CONTENT`. Workspace-sync types (CODE, TEST, RESULT) are exempt from `STALE_NODE`.

| Node Type | Quality gaps surface in Phase |
|-----------|------------------------------|
| PARA | 2 |
| HLR | 3 |
| ARCHITECTURE | 4 |
| MODULE | 5 |
| CONTRACT | 6 |
| LLR | 7 |
| DESIGN | 8 |
| SUITE | 9 |
| CASE_HLR / CASE_LLR | 10 |
| CODE / TEST | 13 |

### 3.4 Code Generation Gaps (Phase 12)

Phase 12 has its own gap taxonomy detected by workspace scanning.

| Priority | GapKind | Meaning |
|----------|---------|---------|
| 0 | `TEST_ENV_BROKEN` | Test environment cannot run at all |
| 1 | `SYNTAX_ERROR` | File has a Python syntax error |
| 2 | `MISSING_SOURCE` | DESIGN node has no generated source file |
| 3 | `MISSING_TEST` | CASE node has no generated test file |
| 4 | `FAILING_TESTS` | One or more tests fail |
| 5 | `INVALID_TRACES` | Annotations reference non-existent LLR IDs |
| 6 | `UNTRACED_FUNCTIONS` | Functions missing `@traces` decorator |
| 7 | `LOW_STRUCTURAL_COVERAGE` | Statement coverage below 100% |
| 8 | `LOW_BRANCH_COVERAGE` | MC/DC branch coverage below 100% |
| 9 | `UNCOVERED_REQUIREMENT` | LLR has no passing test evidence |
| 10 | `WEAK_TRACE` | Function traces to LLR but doesn't implement it |
| 11 | `SCOPE_CREEP` | Function not backed by any requirement |

### 3.5 Detection Algorithm

The `GapAnalyser.analyse()` method iterates every node once, running three check families per node (structural completeness, staleness, integrity), then four cross-node scans (duplicate siblings, empty traces, circular traces, inadequate content). Within a priority level, gaps are ordered by `node_id` for deterministic runs.

Semantic duplicate detection groups nodes by parent and type, excludes the canonical (lowest `node_id`) from each group, and dispatches a `DUPLICATE_NODE` gap for each non-canonical sibling. CASE nodes with unique `trace_to` sets are never treated as duplicates. PARA nodes are exempt from the exact-hash sibling scan entirely: they are document mirrors whose identity is their position and title in the section tree, not their body — heading PARAs are empty by design (all byte-identical), and a source document may repeat the same sentence in different sections. Deleting such a PARA would reparent its children and flatten the document structure (live evidence: topological_sort r3, PARA-0010/0011/0013 vs PARA-0008 — four distinct empty section headings, each with its own child sections).

### 3.6 Write-Time Invariant Enforcement (Correct-by-Construction)

Every deterministic invariant that the analyser can detect *after* the fact is also enforced *at write time* by the graph-write tools. A write that would violate one is **rejected** with a tool `ERROR: ...` message telling the agent exactly how to fix it — the correction happens in the same (already-paid) agent turn, instead of the analyser flagging a gap that costs a later LLM repair dispatch.

The checks live in **one shared module, `backend/analysis/node_invariants.py`**, used by both the write tools (`graph_write`, `graph_ops`, `multi_graph_write`) and the Gap Analyser, so the two layers can never diverge. The analyser keeps running as a backstop for graphs authored before enforcement existed (resumed builds).

Enforced invariants (each is a pure function returning `None` or an actionable message):

| Invariant | Applies to | Rejected when | Analyser backstop gap |
|-----------|-----------|---------------|----------------------|
| Title presence / length | all authored types (not PROJECT, DOCUMENT, RESULT, RECORD) | title missing or > 7 words | `UNTITLED_NODE` |
| Sibling title uniqueness | same | title duplicates a sibling's (case/whitespace-insensitive) under the same parent | `SIBLING_TITLE_DUPLICATE` |
| Title distinct from parent | same | title identical (case/whitespace-insensitive) to the parent node's title — scope not narrowed | `TITLE_COLLIDES_WITH_PARENT` |
| Requirement wording | HLR, LLR | content doesn't start with "The system shall ", or contains a raw `PARA-nnnn` placeholder | `MALFORMED_REQUIREMENT` |
| Minimum content length | ARCHITECTURE, MODULE, CONTRACT, DESIGN, SUITE, CASE_* | non-empty content < 50 chars | `INADEQUATE_CONTENT` |
| Sibling content uniqueness | all types except PARA (document mirrors — see §3.5) | content identical (trim/lowercase) to a same-type sibling's | `DUPLICATE_NODE` |
| CASE trace_to membership | CASE_HLR, CASE_LLR | trace_to empty, or contains refs resolving to the wrong node type (CASE_HLR→HLR, CASE_LLR→LLR) | `STALE_TRACE_TO` |

Enforcement points: `add_node` checks all applicable invariants against the prospective node; `update_node` checks only the fields being changed (title and/or content); `update_trace` / `add_traces` check CASE trace membership. `multi_graph_write` validates the **whole batch first** and rejects it atomically (multi_file_write precedent) — a bad operation never leaves the batch half-applied — reporting per-op errors in its existing `[i] ERROR: ...` summary format.

---

## 4. Agent System

### 4.1 One Agent Per Phase

The agent is not a persistent actor tied to a role. At each phase boundary, `AgentFactory.create_agent_for_gap()` builds a fresh ReAct agent. Roles (`AgentRole`) exist as an internal configuration axis -- they select the model, fallback prompt template, and tool permissions -- but the phase IS the operational identity.

Each agent is configured by:
1. **System prompt** -- loaded from gap-type Jinja templates
2. **Tool whitelist** -- controlled by gap type (see section 6)
3. **Checkpointer** -- a shared `MemorySaver` for conversation accumulation within a phase
4. **Pre-model hook** -- trims oldest messages when conversation exceeds the context window budget

The agent is a two-node `StateGraph` (llm_node + tool_node) backed by JSON function calling. The model emits structured `tool_call` objects -- no text parsing.

### 4.2 Prompt Resolution

Prompts resolve with a three-level priority:
1. User gap-type override (set via API)
2. Built-in gap-type default (Jinja template per gap type)
3. User role override / built-in role default (fallback)

Gap-type prompts are the primary mechanism. Each gap type has a template that embeds the procedure, constraints, and examples the agent needs.

### 4.3 Dispatch Strategies

**Structural dispatch** resolves gaps one at a time. Pick the highest-priority gap, dispatch it, check if the graph changed, repeat. Works when gaps are independent.

**Batch dispatch** presents all gaps and all existing nodes of the target type in a single prompt. The agent sees the full picture and makes assignments across all gaps at once. Necessary when gaps are interdependent.

The rule: **if resolving one gap changes how you should resolve another, batch them.**

| Phase | Gap Type | Dispatch | Why |
|-------|----------|----------|-----|
| 2 | `UNCHUNKED_DOCUMENT` | Structural | One doc, all PARAs in one conversation |
| 3 | `UNCOVERED_PARA` | **Batch** | PARAs compete for same HLRs |
| 4 | `UNARCHITECTED` | Structural | Single gap (one PROJECT) |
| 5 | `UNMODULARISED` | **Batch** | HLRs compete for same MODULEs |
| 6 | `UNCONTRACTED` | Structural | One CONTRACT per MODULE, independent |
| 7 | `UNREFINED_HLR` | **Batch** | LLRs from different HLRs can overlap |
| 8 | `UNDESIGNED` | **Batch** (per MODULE) | LLRs compete for DESIGNs within a MODULE |
| 9 | `UNSUITED` | Structural | Single gap (one PROJECT) |
| 10 | `UNTESTED_HLR/LLR` | Structural | Each requirement gets its own CASE |

Batch dispatch retries up to 3 times with only unresolved gaps, then falls back to structural dispatch. Phase 8 has a fast-path: when a MODULE already has a DESIGN, LLRs are linked directly via `trace_to` without invoking the LLM.

### 4.4 Gap-to-Role Mapping

| Gap Type(s) | Agent Role |
|-------------|-----------|
| `UNCHUNKED_DOCUMENT` | Document Specialist |
| `UNCOVERED_PARA`, `UNREFINED_HLR`, requirement quality gaps | Requirements Engineer |
| `UNARCHITECTED`, `UNMODULARISED`, `UNCONTRACTED` | Design Architect |
| `UNDESIGNED`, `CONTRACT_VIOLATION`, `CROSS_MODULE_COUPLING` | Software Engineer |
| `UNSUITED`, `UNTESTED_HLR`, `UNTESTED_LLR` | Test Engineer |
| `STALE_NODE`, `ORPHAN_NODE`, `EMPTY_CONTENT`, `STALE_TRACE_TO`, `INCONSISTENT_CONTENT`, `INADEQUATE_CONTENT`, `DUPLICATE_NODE`, `UNTITLED_NODE` | Quality Auditor |
| `EMPTY_TRACE`, `CIRCULAR_TRACE` | *(deterministic fix -- no agent)* |
| `UNSYNCED_DESIGN`, `UNSYNCED_TEST` | *(workspace_sync -- no agent)* |

---

## 5. Context Engine

### 5.1 Principles

Three principles govern context assembly across all phases:

1. **Batch phases get the global picture.** When gaps are interdependent, the agent sees all gaps and all existing nodes of the target type.
2. **Every agent sees existing nodes of the type it might create.** This prevents proliferation -- extend or reuse before creating new.
3. **CONTRACTs flow downstream.** From Phase 7 onward, the agent sees the relevant CONTRACT because it constrains the interface boundary.

### 5.2 Per-Gap Context -- Batch Phases

| Phase | Gap Type | Context Provided | Rationale |
|-------|----------|-----------------|-----------|
| 3 | `UNCOVERED_PARA` | All uncovered PARAs (full content) + all existing HLRs (id, parent, title, content) | Full picture to avoid duplicate HLRs |
| 5 | `UNMODULARISED` | All unassigned HLRs + all MODULEs + ARCHITECTURE (content, truncated to 2000 chars) | Full assignment picture |
| 7 | `UNREFINED_HLR` | All unrefined HLRs + all LLRs + all MODULEs and CONTRACTs | Derive LLRs within architectural boundaries |
| 8 | `UNDESIGNED` | Per-MODULE: MODULE + CONTRACT + undesigned LLRs + existing DESIGNs | Consolidate rather than proliferate |

### 5.3 Per-Gap Context -- Structural Phases

| Phase | Gap Type | Context Provided | Rationale |
|-------|----------|-----------------|-----------|
| 2 | `UNCHUNKED_DOCUMENT` | None (agent reads document via tools) | Document may be large; agent chunks interactively |
| 4 | `UNARCHITECTED` | Ancestor chain + all HLRs | Full requirements landscape |
| 6 | `UNCONTRACTED` | Ancestor chain + ARCHITECTURE + HLRs traced to the MODULE | Contract reflects module's requirements |
| 9 | `UNSUITED` | Ancestor chain + ARCHITECTURE + all MODULEs + all HLRs | Test strategy covers the full system |
| 10 | `UNTESTED_HLR` | Ancestor chain + all existing CASE_HLR nodes | Reuse existing cases |
| 10 | `UNTESTED_LLR` | Ancestor chain + all existing CASE_LLR nodes | Reuse existing cases |

### 5.4 Per-Gap Context -- Quality Gaps

**Graph integrity gaps:**

| Gap Type | Context Provided | Rationale |
|----------|-----------------|-----------|
| `STALE_NODE` | Ancestor chain (starts at the node itself, so it carries the node's own content AND the parent's current content, packed under the token budget) + the staleness reason inline in the task description | Most repairs need zero `graph_read` round-trips: the prompt states the reason, points at the two content blocks already in context, and names `graph_refresh_provenance` for the "still valid" outcome |
| `ORPHAN_NODE` | Ancestor chain | Find or create correct parent |
| `DUPLICATE_NODE` | Ancestor chain + siblings (same parent, same type) | Judge semantic overlap |
| Other integrity gaps | Ancestor chain | Standard structural context |

**Requirement quality gaps:**

| Gap Type | Context Provided | Rationale |
|----------|-----------------|-----------|
| `MALFORMED_REQUIREMENT` | Ancestor chain + node content | Rewrite relative to parent |
| `VAGUE_REQUIREMENT` | Ancestor chain + node content | Judge measurable criteria |
| `UNTESTABLE_REQUIREMENT` | Ancestor chain + node content | Determine observable outcome |
| `NON_ATOMIC_REQUIREMENT` | Ancestor chain + node content | Split relative to parent |
| `CONTRADICTORY_REQUIREMENTS` | Node content + all sibling requirements | Full peer set to resolve conflict |
| `INCOMPLETE_DECOMPOSITION` | HLR + its LLRs + MODULE/CONTRACT context | Judge coverage against architecture |

**Content adequacy gaps:**

| Gap Type | Context Provided | Rationale |
|----------|-----------------|-----------|
| `INADEQUATE_CONTENT` | Ancestor chain + node content | Judge sufficiency for downstream |
| `INCONSISTENT_CONTENT` | Ancestor chain; for CASE nodes, trace_to targets | Consistency against specification |

**Architectural conformance gaps:**

| Gap Type | Context Provided | Rationale |
|----------|-----------------|-----------|
| `CONTRACT_VIOLATION` | DESIGN content + MODULE's CONTRACT content | Compare against interface |
| `CROSS_MODULE_COUPLING` | DESIGN content + all MODULE/CONTRACT nodes | Spot cross-references into other modules |

### 5.5 Per-Phase Accumulation

Within a phase, agents accumulate conversation history across gap dispatches. At phase boundaries, history is discarded entirely via `PhaseContext.reset_phase()` which increments a nonce, changing the thread ID passed to LangGraph's `MemorySaver`.

**Trimming**: FORGE reserves 30% of the context window for the system prompt, tools, and working space. The remaining 70% is the budget. A `pre_model_hook` (`make_trim_hook`) runs before every LLM call and applies `trim_messages` with `strategy="last"`, `include_system=True`, and `start_on="human"`. No summarisation. Trimming is deterministic and fast.

### 5.6 Thread ID Scheme

Each thread ID encodes `phase-{N}-{gap_type}-{nonce}`. Same phase + same gap type + same nonce = same thread. Phase boundary = new nonce = fresh thread. Trimming does not change the thread.

### 5.7 CONTRACT as Coordination Mechanism

CONTRACTs appear in context starting at Phase 7:

| Phase | How CONTRACT appears |
|-------|---------------------|
| 7 | All MODULEs and CONTRACTs provided as MODULE/CONTRACT context block |
| 8 | The specific MODULE's CONTRACT in full (up to 2000 chars) |
| 10 | CONTRACT accessible via ancestor chain when CASE traces to an LLR under the MODULE |

Cross-module dependencies must be expressed through CONTRACTs. If a dependency is not in a CONTRACT, FORGE treats it as a gap and routes back to Phase 6.

---

## 6. Tool API

### 6.1 Access Control

Tools are whitelisted by **gap type**, not by role. At dispatch time the agent receives only the tools listed for the gap it is resolving.

### 6.2 Structural Gap Tools

| Gap Type | Tools |
|----------|-------|
| `UNCHUNKED_DOCUMENT` | `graph_read`, `graph_add_node` |
| `UNCOVERED_PARA` | `graph_read`, `derive_requirement`, `graph_add_node`, `graph_reparent_node` |
| `UNARCHITECTED` | `graph_read`, `graph_add_node`, `graph_update_node` |
| `UNMODULARISED` | `graph_read`, `graph_add_node`, `graph_add_traces` |
| `UNCONTRACTED` | `graph_read`, `graph_add_node` |
| `UNREFINED_HLR` | `graph_read`, `derive_requirement`, `graph_add_node`, `graph_reparent_node` |
| `UNDESIGNED` | `graph_read`, `graph_add_node`, `graph_add_traces` |
| `UNSUITED` | `graph_read`, `graph_add_node` |
| `UNTESTED_HLR` | `graph_read`, `graph_add_node`, `graph_add_traces` |
| `UNTESTED_LLR` | `graph_read`, `graph_add_node`, `graph_add_traces` |

`UNSYNCED_DESIGN` and `UNSYNCED_TEST` are handled by a deterministic workspace-sync step -- no agent dispatch.

### 6.3 Quality Gap Tools

**Graph integrity:**

| Gap Type | Tools |
|----------|-------|
| `STALE_NODE` | `graph_read`, `graph_update_node`, `graph_delete_node`, `graph_refresh_provenance` |
| `ORPHAN_NODE` | `graph_read`, `graph_reparent_node`, `graph_delete_node` |
| `EMPTY_CONTENT` | `graph_read`, `graph_update_node` |
| `STALE_TRACE_TO` | `graph_read`, `graph_add_traces`, `graph_remove_traces`, `graph_update_trace` |
| `DUPLICATE_NODE` | `graph_read`, `graph_delete_node`, `graph_update_node` |
| `UNTITLED_NODE` | `graph_read`, `graph_update_node` |

`EMPTY_TRACE` and `CIRCULAR_TRACE` are resolved deterministically -- no agent dispatch.

**Requirement quality:**

| Gap Type | Tools |
|----------|-------|
| `MALFORMED_REQUIREMENT` | `graph_read`, `graph_update_node` |
| `NON_ATOMIC_REQUIREMENT` | `graph_read`, `check_atomicity`, `graph_update_node`, `graph_add_node` |
| `NON_EARS_REQUIREMENT` | `graph_read`, `graph_update_node` |
| `VAGUE_REQUIREMENT` | `graph_read`, `graph_update_node` |
| `UNTESTABLE_REQUIREMENT` | `graph_read`, `graph_update_node` |
| `CONTRADICTORY_REQUIREMENTS` | `graph_read`, `graph_update_node`, `graph_delete_node` |
| `INCOMPLETE_DECOMPOSITION` | `graph_read`, `graph_add_node` |

**Content adequacy:**

| Gap Type | Tools |
|----------|-------|
| `INADEQUATE_CONTENT` | `graph_read`, `graph_update_node` |
| `INCONSISTENT_CONTENT` | `graph_read`, `check_consistency`, `graph_update_node`, `graph_delete_node` |

**Architectural conformance:**

| Gap Type | Tools |
|----------|-------|
| `CONTRACT_VIOLATION` | `graph_read`, `graph_update_node` |
| `CROSS_MODULE_COUPLING` | `graph_read`, `graph_update_node` |

### 6.4 Key Tool Descriptions

**`graph_read`** -- all graph observation operations via an `operation` parameter: `get_node`, `get_children`, `get_ancestors`, `get_siblings`, `get_subtree`, `get_stale_nodes`, `get_gaps`, `nodes(node_type)`, `nodes_tracing_to(node_id, source_type)`.

**`graph_add_node`** -- creates a new node. Validates parent type. Requires `title` for authored nodes. Accepts optional `trace_to` as JSON array.

**`graph_update_node`** -- updates title, content, or properties. Recomputes `content_hash`, triggers staleness propagation to direct children.

**`graph_delete_node`** -- soft-deletes a node and all descendants. Nothing is hard-deleted.

**`graph_reparent_node`** -- moves a node to a different parent. Used for `ORPHAN_NODE` resolution.

**`graph_add_traces` / `graph_remove_traces`** -- incremental trace_to list management (idempotent).

**`derive_requirement`** -- single LLM call to derive a formal shall-statement in EARS format from a source paragraph or parent requirement. Returns `RequirementResult` with `req_text`, `verification_method`, `derived`, and `derived_rationale`.

**`check_consistency`** -- checks whether node content is consistent with its parent. Returns `ConsistencyResult` with issues and suggested content.

**`check_atomicity`** -- checks whether a requirement is atomic and EARS-compliant. Returns `AtomicityResult` with suggested splits and rewrites.

---

## 7. Prompt Design

### 7.1 Two-Layer Model

1. **Phase system prompt** -- gives the agent its identity and domain focus. Rendered from Jinja2 templates in `templates/gaps/`.
2. **Gap-type task description** -- injected as the human message. Contains the specific gap, target node, pre-assembled context, numbered steps, and constraints.

There is no role abstraction layer between these two. The phase prompt is the system prompt. The task description is the user message. That is the entire prompt architecture.

### 7.2 Template Structure

```
templates/
  shared/          # Reusable fragments ({% include %})
  roles/           # Persona fragments (3-4 lines each)
  gaps/            # Phase system prompts, one per gap type
  codegen/         # Code generation agent prompts
  quality/         # Judge and planner prompts
```

**Shared fragments** -- any rule in 2+ templates lives in a single file:

| Fragment | Purpose |
|----------|---------|
| `tool_reminder.j2` | "Always call tools before writing conclusions" |
| `node_id_format.j2` | "Node IDs use TYPE-NNNN format" |
| `req_format.j2` | Requirement quality rules |
| `title_rule.j2` | "3 to 5 plain-English words" |
| `tracing_rules.j2` | @traces decorator conventions |
| `case_spec.j2` | Test case content format |
| `atomic_rule.j2` | Atomicity constraint for requirements |

**Role fragments** are 3-4 line persona definitions included by multiple gap templates. This is text reuse, not a role abstraction. The gap template is the unit of prompt identity.

### 7.3 Batch Prompts

Batch prompts follow a consistent pattern:

```
You are <doing what> within <scope>.

<GAP TYPE> (<count> -- each needs <resolution>):
  [NODE-0001] field=value | field=value
  ...

EXISTING <TARGET TYPE> (<count>):
  [NODE-0010] field=value | field=value
  ...

FOR EACH <gap node> above, do ONE of:
  A) <preferred action using existing nodes>
  B) <fallback: create new node>

RULES:
- ...
```

### 7.4 Quality Prompts

Quality checks (judge and planner templates) are invoked directly via plain LLM calls, not through the phase agent. **Judges** evaluate and do not act (no tools). **Planners** produce structured plans (no tools, no mutations).

All plain LLM calls are constructed through the single `build_llm` factory, which retries transient transport failures at the client level (`max_retries=2`). `build_llm` fails **at construction** with a loud error when the environment variable named by `llm.api_key_env` is unset or empty — unless the endpoint is explicitly declared keyless via `llm.keyless = true` (a local endpoint such as Ollama that requires no API key). There is no implicit fallback key: a misconfigured key surfaces immediately, not as swallowed mid-run 401s.

**Response cache.** `build_llm` requires an explicit `cacheable: bool` argument at every construction site — cache participation is never implicit. When `cacheable=True` and `llm.cache_enabled` is true (the default), the model is constructed with a local SQLite-backed LangChain response cache (`backend/agents/llm_cache.py::SQLiteLLMCache`, passed per-model via `cache=`, never via the global `set_llm_cache`) stored at `<llm.cache_dir>/llm_cache.db` (default `.cache/llm_cache.db`; the directory is created on first use). A relative `llm.cache_dir` resolves against the **repo root** (derived from the `backend/` package location), never the process cwd — the per-phase integration tests chdir into throwaway per-test workspaces and must still share the warm repo-level cache. An absolute `llm.cache_dir` is used as-is. When `cacheable=False` or `llm.cache_enabled` is false, the model is constructed with `cache=False`, which also opts the model out of any global LangChain cache.

**Cache key composition.** Entries are keyed by `(prompt, llm_string)`. The `llm_string` comes from langchain-core's `_get_llm_string`, which serializes the model configuration — model name, temperature, `base_url`, and every other generation-affecting constructor parameter — so two calls with the same prompt but different model settings never share an entry (the API key is excluded as a secret and never lands in the DB). This is pinned empirically against `ThrottledChatOpenAI` in `backend/tests/test_llm_cache.py::TestCacheKeyIncludesModelParams`: same prompt at two temperatures produces two distinct cache rows and never cross-serves.

What actually caches (verified against langchain-core 1.5.x): only non-streaming `.invoke`/`.ainvoke` calls go through `_generate_with_cache`/`_agenerate_with_cache` and hit the cache — i.e. the direct plain-LLM callers (quality checkers, trace auditor, consolidators) and repeated runs over unchanged inputs. Agent streaming paths (`astream_events`) bypass the LangChain cache entirely; this is accepted — the cache primarily serves the direct `.ainvoke` checkers.

**Independence exemption.** The semantic duplicate checker (`backend/quality/semantic_duplicate_check.py`) deletes a node only when two *independent* LLM calls both return a DUPLICATE verdict. Both calls send byte-identical prompts, so a response cache would turn the second call into a replay of the first and make the double confirmation vacuous. That construction site therefore passes `cacheable=False`; every other `build_llm` site passes `cacheable=True`.

**Dedup prompt ordering for provider prompt caching.** The dedup judge's pair prompt is ordered `[system prompt + SIBLINGS context]` (static prefix) followed by `[TARGET node]` (dynamic suffix). Targets sharing a parent share the same siblings block, and the confirmation call repeats the whole prompt byte-for-byte, so a provider-side KV/prompt cache can reuse the prefix across targets and across the double-confirmation call. This does **not** weaken verdict independence: provider prompt caching reuses attention-prefix computation only — sampling of the two responses remains independent — and the *response* cache stays disabled for this site (`cacheable=False`) exactly as above. The byte-identical prompt for both confirmation calls is by design.

**Batched micro-repair (deterministic-dispatch batching for small edits).** Title-family gaps (`VAGUE_TITLE`, `STALE_TITLE`, `SIBLING_TITLE_DUPLICATE`) and wording-family gaps (`MALFORMED_REQUIREMENT`, `NON_EARS_REQUIREMENT`) each need one small per-node edit, yet the per-gap dispatch path re-sends the full system prompt and context for every node (~150 calls in a measured build). Before per-gap dispatch, when a cycle's gap list contains **N ≥ 3** gaps of the same family, `backend/quality/micro_repair.py::apply_micro_repair_batches` issues **one** structured LLM call per family (prompt builder: `backend/prompting/repair_batch.py`, same single-call-judges-all pattern as the combined checker) containing each node's full content plus the violated invariant message. The response is parsed as per-node fixes and applied through the graph engine with the same write-time invariant validation the tools use (`backend.analysis.node_invariants`: title shape, sibling-title uniqueness, requirement wording, sibling-content uniqueness). Failure handling is loud, never silent: a node line the model dropped or garbled, or a fix an invariant rejects, is logged at WARN and its gap **stays open for the normal per-gap dispatch path**; a transport failure of the batch call is logged at ERROR and leaves the whole family for per-gap dispatch. Resolution is still certified per-gap: after application the gap analyser re-runs, and a gap is dropped from the cycle only when its fix applied **and** its exact `(type, node_id)` key is absent from the fresh analysis (the §8.3 certificate; judge-found types the analyser cannot re-detect are certified by the invariant-validated applied write). The pre-pass is wired into the structural loop's collect, the quality-gap stability loop's scan, and the `combined_quality` dispatch loop.

**Sticky PASS verdicts for the combined quality check.** The combined checker (`backend/quality/combined_check.py`) is driven by `run_combined_quality_check` up to once per pipeline cycle (the runner cycles up to 12 times), and re-judging an unchanged node is pure spend. `checks.py` therefore caches PASS verdicts per `(node_id, sha256(title + NUL + content))` in the flow-scoped `ForgeFlow._quality_verdict_cache` (same pattern as the semantic dedup cache): a node whose title+content hash is unchanged since it last passed **every** applicable axis is filtered out of the batch and never re-sent to the judge. FAIL verdicts are **never** cached — a node that failed any axis must be re-judged after its repair dispatch (repairs change the content, which also rotates the hash). A batch that raises (`UnjudgedQualityError` or transport failure) caches nothing. The cache is flow-scoped and rebuilt on restart: the worst case after a process restart is one full re-judging sweep, so resumability is unharmed.

**Deterministic prescreen for semantic dedup.** Before the semantic duplicate judge is called, a stdlib-only lexical similarity check (`semantic_duplicate_check.py::prescreen_similar_peers`) compares the target's normalised token set against each peer block parsed from the peers context. Similarity is the token-set overlap coefficient `|A∩B| / min(|A|,|B|)`; only when **every** peer scores below the conservative threshold (`_PRESCREEN_MIN_OVERLAP = 0.2`) is the LLM call skipped — clearly-dissimilar candidates share almost no vocabulary, whereas true semantic duplicates share far more than 20% of the smaller node's tokens. Anything ambiguous (and any peers text the prescreen cannot parse) still goes to the judge. **Deletion safety is unchanged**: the prescreen only reduces the candidate pairs that reach the LLM — it never authorises a deletion. The two-call double confirmation below still gates every actual deletion exactly as before, and byte-identical pairs are still resolved deterministically upstream by `duplicate_resolver`.

**Deterministic byte-identical duplicate deletion.** The double-confirmation safety above exists because *semantic* duplicate judgment is an LLM opinion. Byte-identical duplicates need no judgment at all: when the gap analyser confirms two siblings share the same parent, the same node type, and identical content after its normalisation (`strip().lower()`), it emits a `DUPLICATE_NODE` gap carrying `context.duplicate_of` (the canonical, lowest node ID). `backend/pipeline/duplicate_resolver.py::try_resolve_exact_duplicate` — invoked as a pre-dispatch fast path in `pipeline/dispatch.py`, alongside `try_fast_trace` — resolves these **without any LLM dispatch**: it re-verifies the byte-identity precondition against the live graph, merges the duplicate's `trace_to` references into the canonical node, then deletes the younger node via the engine's `delete_node` path (which auto-reparents children). PARA nodes are never resolved this way: the resolver refuses them (and the analyser no longer emits exact-hash `DUPLICATE_NODE` gaps for PARA — §3.5), because deleting a document-mirror node reparents its child sections and destroys the document structure. This is not a silent fallback — it acts on a re-verified fact, logs loudly through `forge_logger`, and raises `RuntimeError` if the deletion precondition fails. Gaps whose content is *not* byte-identical at resolution time (or that lack `duplicate_of`) fall through to the LLM path unchanged; the semantic double-confirmation rules above apply only to that LLM path.

### 7.5 Style Guide

- **Directives** (imperative) for safety constraints, tool restrictions, output format rules.
- **Hints** (suggestion) for strategies with latitude, error patterns to watch for.
- Keep directives under 10 per template.
- Templates state persona, available tools, output expectations, and constraints explicitly.
- Minimal effective prompt: five clear sentences outperform fifty vague ones.

---

## 8. Phase Pipeline

### 8.1 ForgeFlow

`ForgeFlow` is the main build loop. `kickoff_async()` iterates from `start_phase` through `end_phase`, running each phase sequentially. At each phase boundary it resets the agent conversation context.

Special phases have dedicated handlers:

| Phase | Handler | What it does |
|-------|---------|-------------|
| 0 | Direct | Mark project initialised |
| 1 | `_run_ingest_phase` | Read forge.md, create DOCUMENT node |
| 11 | `_run_dashboard_phase` | Render graph as Markdown docs |
| 12 | `_run_code_gen_phase` | Gap-first code generation (mission agent) |
| 14 | `_run_deliverables_phase` | Build deliverables ZIP |

Agent-driven phases (2-10, 13) delegate to the phase pipeline. Both entry
points run the same pipeline: the full-run loop (`kickoff_async` →
`_run_phase`, launched by the UI "start build" route) and the per-phase route
(`run_phase`). There is no structural-only shortcut — a full run gets exactly
the same batch authoring, quality, dedup, and coverage steps as a single-phase
run. The `structural` step invokes the structural gap-resolution loop via
`_run_structural_loop`.

### 8.2 Pipeline Steps

The phase pipeline runs an ordered list of step functions per phase:

| Phase | Steps |
|-------|-------|
| 3  | batch_phase3, quality_gaps, combined_quality, semantic |
| 5  | batch_phase5, quality_gaps, combined_quality, semantic |
| 7  | batch_phase7, quality_gaps, combined_quality, semantic |
| 8  | batch_phase8, quality_gaps, combined_quality, semantic, design_consolidation |
| 10 | batch_phase10, quality_gaps, combined_quality, semantic, case_trace_coverage |
| 13 | workspace_sync |
| Others | structural, quality_gaps, combined_quality, semantic |

Step functions:

| Step | What It Does |
|------|-------------|
| `structural` | Dispatch agent to close structural gaps one at a time; each gap's resolution is certified by re-running the gap analyser (see §8.3) |
| `batch_phaseN` | Dispatch agent with all gaps in a single prompt; agent prefers `multi_graph_write` to emit every new node in one tool call |
| `combined_quality` | Single batched LLM call judges every authored node on four axes (ATOMIC, EARS, title↔content match, title specificity) and emits the relevant gap types. A missing verdict is never a pass: nodes/axes the model failed to judge are re-asked in exactly one follow-up call, and anything still unjudged raises `UnjudgedQualityError` — the step fails loudly rather than scoring silence as clean. PASS verdicts are sticky per `(node_id, content-hash)` on the flow, so unchanged nodes are not re-judged in later cycles; FAIL verdicts are never cached (see §7.4). Before dispatching fixes, batchable title/wording gaps go through the batched micro-repair pre-pass (§7.4); only gaps it could not certify-resolve are dispatched per-gap |
| `quality_gaps` | Detect and dispatch deterministic quality gaps (orphan, empty-content, title-collision, sibling-title-duplicate, stale-trace-to, untitled, duplicate). Batchable title/wording gaps first go through the batched micro-repair pre-pass (§7.4); only survivors are dispatched per-gap |
| `semantic` | Detect and remove semantic duplicate nodes (sibling-scoped; skips containers and sole-coverage children). Deletion requires the same DUPLICATE verdict from **two independent LLM calls** — a single nondeterministic verdict must not destroy requirement text. A UNIQUE verdict (including a UNIQUE on the confirmation call) is sticky: it is cached per `(node_id, content-hash)` on the flow, so unchanged nodes are never re-litigated by later pipeline cycles. A deterministic lexical prescreen (token-set overlap, stdlib only) skips clearly-dissimilar candidates before the LLM judge; it only reduces candidate pairs and never authorises deletion (see §7.4) |
| `design_consolidation` | Merge DESIGN sprawl within each MODULE (Phase 8) |
| `case_trace_coverage` | Verify CASE nodes cover traced requirements (Phase 10) |
| `workspace_sync` | Deterministic file scan to create CODE/TEST nodes (Phase 13) |

### 8.3 Convergence

After all steps complete, if any step reported deletions, the pipeline cycles -- re-runs all steps -- because deletions can uncover new gaps. When no deletions occur the phase is stable. A cycle cap (12) bounds runaway delete/recreate loops.

Within the structural loop, resolution is **proven, not inferred**: after
each dispatch the loop re-runs the gap analyser (a cheap in-memory scan) and
declares the gap resolved only when its exact key `(gap type, node id)` is
absent from the fresh analysis — the **per-gap resolution certificate**. The
former global version-sum "progress" signal (`sum(node.version)` deltas) is
retired for resolution: under it ANY write anywhere in the graph counted,
including no-op re-stamps, wrong-typed nodes, and vandalism of unrelated
nodes (the hostile-agent fake-progress incident). A write that does not
close the dispatched gap now provably never resolves it.

Every dispatch of a still-open gap counts against a per-gap cap
(`_MAX_GAP_ATTEMPTS = 3`); at the cap the gap is abandoned for the pass and
stays open, so the cumulative audit fails the phase loudly. A gap whose
certificate clears never reappears in the next collect, so the counter is
only ever consulted for gaps that failed to close. Counters are per
structural pass; legitimate rework in a later pipeline cycle starts fresh and
is bounded by the cycle cap.

### 8.4 Failure Semantics

Quality verification must never fail open: a failed check is not a passed
check.

- **Step exceptions propagate.** If any pipeline step raises, the pipeline
  marks the phase `awaiting_approval` and re-raises. The run halts loudly
  instead of continuing on an unverified graph and reporting the phase
  complete.
- **Quota exhaustion halts the run.** `DispatchQuotaError` propagates out of
  the structural loop, the qual-check graph, and the `combined_quality`
  dispatch loop -- it is never converted into an empty gap list or a
  "check complete" log line.
- **Combined quality check retries once.** A transient LLM failure is retried
  a single time; a second failure propagates. It is never converted into an
  empty gap list (which would be indistinguishable from a clean sweep).

### 8.5 Cumulative Audit

When a phase stabilises, `PhaseAuditor` checks that all gap types for this phase **and all prior phases** are absent:

| Phase | Must be absent (cumulative) |
|-------|---------------------------|
| 2 | `UNCHUNKED_DOCUMENT` |
| 3 | + `UNCOVERED_PARA` |
| 4 | + `UNARCHITECTED` |
| 5 | + `UNMODULARISED` |
| 6 | + `UNCONTRACTED` |
| 7 | + `UNREFINED_HLR` |
| 8 | + `UNDESIGNED` |
| 9 | + `UNSUITED` |
| 10 | + `UNTESTED_HLR`, `UNTESTED_LLR` |
| 13 | + `UNSYNCED_DESIGN`, `UNSYNCED_TEST` |

Quality gap types are not part of `PHASE_COMPLETION_CRITERIA`. They are handled by quality-check steps within each phase's pipeline.

---

## 9. Backend Infrastructure

### 9.1 Technology Stack

| Technology | Role |
|-----------|------|
| Python 3.12 | Async-first runtime |
| FastAPI | HTTP API and WebSocket server |
| aiosqlite | Async SQLite for persistence |
| NetworkX 3.3 | In-memory graph algorithms |
| Pydantic 2.7 | Data validation and schema |
| LangGraph 0.3+ | Agent execution -- JSON function calling |
| LangChain Core 0.3+ | Tool base class, message schema, LLM interface |
| LiteLLM 1.30+ | LLM provider abstraction for internal tools |

### 9.2 Server Lifecycle

Startup sequence (`server/lifespan.py`):

1. `_init_workspace_paths` -- resolve workspace, create .forge dirs
2. `_init_config` -- load settings from forge.db, inject API keys
3. `_init_graph` -- create ProjectGraph, apply schema, hydrate NetworkX
4. `_init_session_and_phases` -- create session and PhaseStore
5. `_init_events` -- EventBus, WebSocketManager, EventBroadcaster
6. `_wire_graph_events` -- connect graph mutations to WebSocket broadcasts
7. `_init_tools` -- build ToolRegistry with all file + graph tools
8. `_init_agents` -- AgentFactory, AgentPool, OperatorService

Dependency order matters. Config before graph. Graph before tools. Tools before agents.

### 9.3 REST API Summary

All endpoints prefixed with `/api/v1`.

**Graph** (`/api/v1/graph`): node CRUD, ancestry, descendants, siblings, impact analysis, traceability, context bundles, trace management, compliance reports, baselines.

**Phases** (`/api/v1/phases`): list states, start/stop/reset build flow, purge derived, per-phase run/scan/audit/approve/qual-check/semantic-check.

**Session** (`/api/v1/session`): session metadata read/update.

**Console** (`/api/v1/console`): natural-language requests against the graph.

**Settings** (`/api/v1/settings`): ForgeConfig read/write (deep-merge patch).

**Auth** (`/auth`): login, check, logout. Enabled when `FORGE_AUTH_USER` and `FORGE_AUTH_PASS` are set.

### 9.4 WebSocket Events

| Event | Payload | When |
|-------|---------|------|
| `GAP_LIST_UPDATE` | `{ gaps, stats }` | After every gap analysis run |
| `AGENT_ACTION_START` | `{ agent, gap_type, node_id }` | Agent picks up a gap |
| `AGENT_ACTION_END` | `{ agent, gap_resolved, nodes_created, elapsed_ms }` | Agent finishes |
| `GRAPH_NODE_CHANGED` | `{ action, node }` | Node created or updated |
| `PHASE_TRANSITION` | `{ to_phase, status }` | Phase boundary crossed |
| `LOOP_STATUS` | `{ status }` | Loop state changes |
| `WORK_QUEUE` | `{ items, history }` | Queue state changes |

### 9.5 Deployment

Production uses `render.yaml` with Docker. Three environment variables: `FORGE_AUTH_USER`, `FORGE_AUTH_PASS`, `FORGE_WORKSPACE`.

---

## 10. Work Queue

### 10.1 Service Overview

The Work Queue is a system-wide singleton that provides a visible, prioritized list of work items. The gap analyser still controls dispatch -- the queue provides **visibility**: what the system is doing, what it plans to do next, and what was already tried.

| Source | How items are added |
|--------|-------------------|
| `structural_loop_graph` (phases 2-11) | Auto-populated from gap analyser output each batch |
| Phase agent (any phase) | LLM calls `queue_add` for discovered sub-items |
| Mission agent (phase 12) | LLM calls `queue_add` during its continuous loop |
| Quality steps | Quality gaps become queue items automatically |

### 10.2 Data Model

**WorkItem**: `id`, `phase`, `urgency` (critical/high/medium/low), `importance` (high/medium/low), `category`, `description`, `target` (file path or node ID), `affected_files`, `effort` (low/medium/high), `rationale`, `status` (pending/in_progress/done/failed). Sorted by Eisenhower dimensions: low-effort first, then urgency, then importance, then FIFO.

**ActionRecord**: `round`, `work_item_id`, `phase`, `category`, `files_modified`, `tool_calls`, `gap_count_before`, `gap_count_after`, `outcome` (improved/no_change/worse), `summary`.

### 10.3 Integration with Phases 2-11

The `collect_gaps` node in `structural_loop_graph` populates the queue from gap analyser output. The `dispatch_gap` node updates item status through the lifecycle: pending -> in_progress -> done/failed. The phase agent can also call `queue_add` to register sub-items it discovers during dispatch.

### 10.4 Integration with Phase 12

Phase 12 uses a single long-lived mission agent in one continuous conversation. The mission agent calls `queue_add` to register items it plans to work on (making its plan visible in the UI), but the agent itself drives execution -- the queue does not schedule work for it.

### 10.5 Anti-Thrash

Max 2 consecutive failures per category. `should_skip_category` returns `True` when the failure count reaches the threshold, allowing callers to skip hopeless categories. The count tracks trailing consecutive "no_change" or "worse" outcomes and resets on any success. This is separate from the phase pipeline's convergence model (which has no cycle cap).

---

## 11. Frontend Architecture

### 11.1 Technology Stack

| Technology | Role |
|-----------|------|
| TypeScript 5.4 | Strict typing mandatory |
| React + Vite | SPA architecture |
| Zustand 4.5 | Single combined store with sliced state |
| TanStack Query 5 | Data fetching, synced with WebSocket via invalidation |
| Tailwind CSS 3.4 | Utility-first styling |
| shadcn/ui | Component primitives |
| React Flow 11 | Graph visualisation |
| Monaco Editor | In-browser code and document editing |
| React Router 6 | Client-side routing |

### 11.2 State Management

Single combined Zustand store exported from `frontend/src/store/index.ts`. Logical sections: session (loopStatus, iterationCount), gaps, agents, phases, logs, UI state. WebSocket events update the store directly; TanStack Query handles REST fetches with WebSocket-triggered cache invalidation.

### 11.3 Routing

| Path | Component | Description |
|------|-----------|-------------|
| `/` | `CommandCentre` | Primary loop view |
| `/phase/:phaseNum` | `PhaseDashboard` | Phase N detail view (0-14) |
| `/graph-inspector` | `GraphInspector` | 3D force-directed graph |
| `/agent-inspector` | `AgentInspector` | Agent cards + pipeline canvas |
| `/settings` | `Settings` | API keys and per-phase model config |
| `/login` | `Login` | Session login (when auth enabled) |

### 11.4 Global Layout

```
StatusBar:   project name | loop status | Play/Pause | agent pip
Sidebar:     nav links + 15 phase buttons (0-14)
Outlet:      active route component
ConsoleBar:  persistent at bottom; natural-language commands via POST /console/run
WorkQueue:   persistent panel beside console; visible on every dashboard
```

All dashboards that expose graph nodes use the **NodeTablePanel** as the primary left panel (38% width) with dashboard-specific content on the right. No tabs -- every section (Header, Content, Properties, Edges, Context) is always visible by scrolling.
