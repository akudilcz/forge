# Phase 10 — Write Test Cases

## Purpose

Phase 10 produces individual test case nodes that verify the system's
requirements. Each High-Level Requirement gets a CASE_HLR and each Low-Level
Requirement gets a CASE_LLR. The phase ensures every requirement has traceable,
reviewable test coverage with no gaps and no duplication.

## Node Types

| Field | CASE_HLR | CASE_LLR |
|-------|----------|----------|
| Layer | 6 | 6 |
| Parent | SUITE | SUITE |
| trace_to | HLR | LLR |

Each CASE node contains:

- **Test description** — what is being verified and why.
- **Preconditions** — required state before execution.
- **Steps** — ordered actions to perform.
- **Expected results** — observable outcomes for each step.
- **Acceptance criteria** — pass/fail determination rules.

## Gap Types

| Gap | Trigger |
|-----|---------|
| **`UNTESTED_HLR`** | An HLR has no CASE_HLR tracing to it. |
| **`UNTESTED_LLR`** | An LLR has no CASE_LLR tracing to it. |

## Dispatch Strategy

**Chunked batch dispatch.** `batch_phase10` presents untested HLRs and LLRs
together with the SUITE strategy and existing CASEs, and the agent emits new
cases via `multi_graph_write`. The untested-requirement list is processed in
chunks of `LLMConfig.batch_author_chunk_size` (one agent call per chunk; the
SUITE + existing-CASE snapshot is taken once and shared across chunks) so the
authored case output never hits the provider output-token limit. Per-chunk
retry; stragglers fall back to per-gap structural dispatch — one agent call
per remaining requirement (see design/02 §Batch prompts).

## Context Provided to the Agent

Per-requirement context (deliberately shallow to avoid O(N^2) growth):

- **Requirement + parent** — the target HLR or LLR and its immediate parent
  (not the full ancestor chain up to DOCUMENT — that would repeat ~20k chars
  per dispatch and grow linearly with each existing CASE).
- **Compact CASE list** — all existing CASE_HLR or CASE_LLR nodes as
  `[ID] trace_to=[...] | title` (ID + trace + title only, no content).
  This lets the agent decide create-vs-reuse without inflating context.
- **SUITE ID** — injected into the task prompt so the agent knows the parent.

## Tools

| Tool | Usage |
|------|-------|
| `graph_read` | Read the requirement, its ancestors, SUITE, and existing CASE nodes. |
| `graph_add_node` | Create CASE_HLR or CASE_LLR as a child of SUITE. |
| `graph_add_traces` | Link the CASE node → requirement via `trace_to`. |

## Agent Procedure

1. Read the target requirement and its ancestor chain for full context.
2. Read the SUITE node to understand the test strategy and coverage approach.
3. Read all existing CASE nodes of the same type to check for reusable
   preconditions or overlapping coverage.
4. Author the CASE node content: description, preconditions, steps, expected
   results, and acceptance criteria.
5. Create the CASE node as a child of SUITE.
6. Add a `trace_to` link from the CASE to the requirement it verifies.

**Contract-encoding rules** (`CASE_CONTRACT_ENCODING` in
`backend/prompting/task_prompts_authoring.py`, shared with the batch
phase-10 prompt): acceptance criteria must make a wrong implementation
fail — exception cases assert the base class too; return-value cases
assert the exact value (`is None`, never an empty collection as
equivalent); ordering/tie-break cases use discriminating inputs (data
with real dependencies, full exact output sequence asserted — an
edge-free input cannot distinguish "sort once" from "sort at every
selection step"); callable-parameter cases invoke the callable with the
exact contracted arity.

## Pipeline Steps

| Step | Purpose |
|------|---------|
| `batch_phase10` | Chunked batch dispatch of UNTESTED_HLR/UNTESTED_LLR gaps; stragglers fall back to per-gap structural dispatch. |
| `quality_gaps` | Raises quality gaps on CASE nodes (see below). |
| `combined_quality` | Batched LLM judging of authored nodes (title axes). |
| `semantic` | Detects and removes semantic duplicate CASEs (double-confirmed deletion). |
| `case_trace_coverage` | One LLM call per CASE judges whether it covers each traced requirement. On cycle 2+ only checks *newly created* CASEs to avoid re-checking the entire population. Missing verdicts (empty/truncated provider body) are retried once; anything still unjudged after the retry keeps its trace **unverified** with an ERROR log — absent evidence never removes a trace. |

## Quality Gaps

| Gap | Trigger |
|-----|---------|
| STALE_NODE | CASE's provenance hash mismatches parent SUITE content. |
| EMPTY_CONTENT | CASE body is blank or trivially short. |
| INADEQUATE_CONTENT | Missing required sections (steps, expected results, or acceptance criteria). |
| INCONSISTENT_CONTENT | CASE steps contradict the requirement or conflict with another CASE for the same requirement. |

## Frontend Dashboard

**Phase Dashboard** — NodeTablePanel listing all CASE nodes with type filter
chips for CASE_HLR and CASE_LLR. Columns show traced requirement, parent SUITE,
and quality status.

**Verification Dashboard** — requirement coverage matrix with HLRs and LLRs on
rows and their CASE nodes on columns. Cells indicate coverage status (covered,
partial, missing) so reviewers can spot gaps at a glance.
