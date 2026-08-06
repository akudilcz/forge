# Phase 3 -- Derive HLRs

**Related docs**: [03 Graph Design](01_architecture.md#2-project-graph) -- [06 Gap Analyser](01_architecture.md#3-gap-analyser) -- [07 Agent System](01_architecture.md#4-agent-system) -- [08 Tool API](01_architecture.md#6-tool-api) -- [09 Frontend Design](01_architecture.md#11-frontend-architecture)

---

## What This Phase Does

Phase 3 derives high-level requirements (HLRs) from paragraphs. The agent
reads all uncovered PARAs and all existing HLRs in a single batch prompt,
then creates formal "The system shall..." requirements in EARS format for
each paragraph that contains a functional requirement.

Batch dispatch is essential because multiple PARAs may describe the same
requirement -- the agent must see all paragraphs and all existing HLRs at
once to avoid creating duplicates and to prefer re-parenting existing HLRs.

---

## Node Type Created

| Field | Value |
|-------|-------|
| Node type | **HLR** |
| Layer | 2 |
| Parent | PARA |
| `trace_to` | Empty (HLRs never trace to anything) |

Each HLR contains a formal shall-statement derived from its parent PARA's
content. The `derive_requirement` tool produces the statement in EARS format
with a verification method (`test`, `analysis`, `review`, or `demo`).

---

## Gap Type

| Gap Type | Priority | Condition |
|----------|----------|-----------|
| `UNCOVERED_PARA` | 2 | Non-heading PARA with body content has no HLR child |

The check skips: `para_type == "heading"`, empty content, and content that
is only a markdown heading with less than 20 characters of body text.

---

## Dispatch Strategy

**Batch dispatch.** All `UNCOVERED_PARA` gaps are presented in a single
prompt alongside all existing HLRs. The agent sees the full picture and
decides for each PARA whether to create a new HLR or re-parent an existing
one.

The batch step (`batch_phase3`) retries up to 3 times if gaps remain after
the first pass. If still unresolved, it falls back to structural dispatch
(one gap at a time).

---

## Context Provided

| Context element | Why included |
|-----------------|-------------|
| All uncovered PARAs | The gaps to resolve |
| All existing HLRs | Full picture to avoid duplicate HLRs |

**Prefer reuse**: if an existing HLR already captures a paragraph's
requirement, the agent re-parents it under the PARA via
`graph_reparent_node` instead of creating a duplicate.

---

## Agent Procedure

The agent is a Requirements Engineer. Tools: `graph_read`,
`derive_requirement`, `graph_add_node`, `graph_reparent_node`.

```
1. [Batch prompt provides all uncovered PARAs + all existing HLRs]
2. For each PARA: decide create-new or reuse-existing
3. Create new:
     derive_requirement(para_id, level="hlr")
     graph_add_node(HLR, parent_id=<para_id>,
                    content="The system shall ...",
                    title="3-5 words")
   Or reuse existing:
     graph_reparent_node(hlr_id, parent_id=<para_id>)
```

**Must-capture categories** (`NORMATIVE_MUST_CAPTURE` in
`backend/prompting/task_prompts_authoring.py`, shared with the batch
phase-3 prompt): derivation must produce one requirement per normative
fact in these repeatedly-dropped categories — exception contracts
(exact class AND base class, required attributes/message), return-value
contracts (exact values incl. None-vs-empty distinctions), ordering /
tie-break / determinism rules (including *when* the rule applies), and
caller-supplied callable contracts (signature and arity). API-signature
code blocks are normative: never summarised as "shall provide
function X".

The `derive_requirement` tool makes a targeted LLM call to produce:
- `req_text`: formal shall-statement in EARS format
- `verification_method`: test | analysis | review | demo
- `derived` + `derived_rationale`: when the requirement is inferred rather
  than directly stated in the source text

---

## Pipeline Steps

| Step | Function | What It Does |
|------|----------|-------------|
| 1 | `batch_phase3` | Batch dispatch: all uncovered PARAs + all HLRs |
| 2 | `quality_gaps` | Detect and dispatch deterministic graph-integrity gaps |
| 3 | `combined_quality` | Single batched LLM call judges atomicity + EARS + title↔content match + title specificity across all HLRs |
| 4 | `semantic` | Detect and remove semantic duplicate HLRs |

Pipeline: `[batch_phase3, quality_gaps, combined_quality, semantic]`.

After all steps, if any step deleted nodes the pipeline cycles. Stable when
no deletions occur.

---

## Quality Checks

**Requirement + title quality** (detected by `combined_quality` step, one batched LLM call per phase):

| Gap Type | Meaning |
|----------|---------|
| `MALFORMED_REQUIREMENT` | Does not start with "The system shall" or contains placeholder PARA IDs |
| `NON_ATOMIC_REQUIREMENT` | Covers multiple obligations -- must be split |
| `NON_EARS_REQUIREMENT` | Does not follow an EARS template pattern |
| `VAGUE_REQUIREMENT` | Uses ambiguous language with no measurable criteria |
| `UNTESTABLE_REQUIREMENT` | Cannot be verified by testing -- no observable outcome |
| `CONTRADICTORY_REQUIREMENTS` | Two sibling HLRs under the same PARA conflict |

**Graph integrity** (detected by `quality_gaps` step, deterministic):

| Gap Type | Meaning |
|----------|---------|
| `STALE_NODE` | HLR's `derived_from_hash` no longer matches its parent PARA's content |
| `ORPHAN_NODE` | HLR parent missing or wrong type |
| `EMPTY_CONTENT` | HLR with no content |
| `UNTITLED_NODE` | Missing or too-long title |
| `TITLE_COLLIDES_WITH_PARENT` | Child title duplicates its parent's title (case/whitespace-insensitive) — suggests scope isn't being narrowed |
| `SIBLING_TITLE_DUPLICATE` | Two or more sibling HLRs under the same parent share an identical title |
| `STALE_TITLE` | LLM-judged: title no longer accurately summarises the content scope (e.g. "Handle Edge Cases" but content covers only empty lists) |
| `VAGUE_TITLE` | LLM-judged: title is a generic label rather than a concrete 3-5 word noun phrase |
| `DUPLICATE_NODE` | Exact-hash or semantic duplicate among sibling HLRs |

All quality gaps are handled inline by the same phase agent (Requirements
Engineer). Non-atomic requirements are split into separate HLR nodes.
Malformed requirements are rewritten to conform to EARS templates.

---

## Cumulative Audit

After stabilisation, the `PhaseAuditor` checks that both `UNCHUNKED_DOCUMENT`
(Phase 2) and `UNCOVERED_PARA` (Phase 3) are absent. This catches
regressions -- if Phase 3 accidentally deletes a PARA that re-exposes Phase
2 gaps, the audit flags it.

---

## Frontend Dashboard

**Route**: `/phase/3`

Standard **PhaseDashboard** layout:

- **PhaseLifecyclePanel** (left): Shows the 4 pipeline steps
  (batch_phase3, quality_gaps, combined_quality, semantic) with status indicators.
  Cycle indicator shows re-run count.

- **NodeTablePanel** (right): Shows HLR and PARA nodes. Type filter chips:
  `[ALL] [HLR] [PARA]`. Selecting an HLR shows its shall-statement content,
  verification method, parent PARA chain, and any quality gaps in the detail
  panel. The NodeContextPanel below shows the parent PARA's content for
  traceability review.

```
+-----------------------------+----------------------------------+
| PhaseLifecyclePanel         | NodeTablePanel                   |
|                             |                                  |
| Step 1: Batch HLRs    [OK]  | [ALL] [HLR] [PARA]               |
| Step 2: Quality Gaps  [OK]  |                                  |
| Step 3: Combined Qual [OK]  | HLR  System Sorting Capability   |
| Step 4: Semantic      [OK]  | HLR  Error Logging Required      |
|                             | HLR  Input Validation Rules      |
| Cycles: 1                   | PARA System Overview             |
|                             | PARA Input Requirements          |
+-----------------------------+----------------------------------+
```
