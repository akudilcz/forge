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

**Structural dispatch.** Each untested requirement produces an independent gap
and receives its own agent call. HLR and LLR cases are authored separately so
each case stays focused on a single requirement.

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

## Pipeline Steps

| Step | Purpose |
|------|---------|
| `structural` | Detects UNTESTED_HLR and UNTESTED_LLR gaps from missing traces. |
| `quality_gaps` | Raises quality gaps on CASE nodes (see below). |
| `semantic` | Validates each CASE is consistent with its traced requirement. |
| `case_trace_coverage` | LLM-checks each CASE covers its traced requirement. On cycle 2+ only checks *newly created* CASEs to avoid re-checking the entire population. |

## Quality Gaps

| Gap | Trigger |
|-----|---------|
| STALE_NODE | The traced requirement was updated after the CASE was written. |
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
