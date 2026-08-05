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
| `STALE_NODE` | Child older than parent | Deterministic |
| `ORPHAN_NODE` | Parent missing or wrong type | Deterministic |
| `EMPTY_CONTENT` | Non-container node with no content | Deterministic |
| `STALE_TRACE_TO` | Trace references non-existent node | Deterministic |
| `EMPTY_TRACE` | MODULE or DESIGN traces to nothing | Deterministic |
| `CIRCULAR_TRACE` | trace_to chain forms a cycle | Deterministic |
| `DUPLICATE_NODE` | Exact-hash or semantic duplicate | Deterministic + LLM |
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
| `CONTRACT_VIOLATION` | DESIGN doesn't conform to its MODULE's CONTRACT interface | LLM |
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

Semantic duplicate detection groups nodes by parent and type, excludes the canonical (lowest `node_id`) from each group, and dispatches a `DUPLICATE_NODE` gap for each non-canonical sibling. CASE nodes with unique `trace_to` sets are never treated as duplicates.

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
| `STALE_NODE` | Ancestor chain | Re-derive from current parent |
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
| `STALE_NODE` | `graph_read`, `graph_update_node`, `graph_delete_node` |
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
| `structural` | Dispatch agent to close structural gaps one at a time |
| `batch_phaseN` | Dispatch agent with all gaps in a single prompt; agent prefers `multi_graph_write` to emit every new node in one tool call |
| `combined_quality` | Single batched LLM call judges every authored node on four axes (ATOMIC, EARS, title↔content match, title specificity) and emits the relevant gap types. A missing verdict is never a pass: nodes/axes the model failed to judge are re-asked in exactly one follow-up call, and anything still unjudged raises `UnjudgedQualityError` — the step fails loudly rather than scoring silence as clean |
| `quality_gaps` | Detect and dispatch deterministic quality gaps (orphan, empty-content, title-collision, sibling-title-duplicate, stale-trace-to, untitled, duplicate) |
| `semantic` | Detect and remove semantic duplicate nodes (sibling-scoped; skips containers and sole-coverage children). Deletion requires the same DUPLICATE verdict from **two independent LLM calls** — a single nondeterministic verdict must not destroy requirement text. A UNIQUE verdict (including a UNIQUE on the confirmation call) is sticky: it is cached per `(node_id, content-hash)` on the flow, so unchanged nodes are never re-litigated by later pipeline cycles |
| `design_consolidation` | Merge DESIGN sprawl within each MODULE (Phase 8) |
| `case_trace_coverage` | Verify CASE nodes cover traced requirements (Phase 10) |
| `workspace_sync` | Deterministic file scan to create CODE/TEST nodes (Phase 13) |

### 8.3 Convergence

After all steps complete, if any step reported deletions, the pipeline cycles -- re-runs all steps -- because deletions can uncover new gaps. When no deletions occur the phase is stable. A cycle cap (12) bounds runaway delete/recreate loops.

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
