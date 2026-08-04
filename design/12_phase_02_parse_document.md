# Phase 2 -- Parse Document

**Related docs**: [03 Graph Design](01_architecture.md#2-project-graph) -- [06 Gap Analyser](01_architecture.md#3-gap-analyser) -- [07 Agent System](01_architecture.md#4-agent-system) -- [08 Tool API](01_architecture.md#6-tool-api) -- [09 Frontend Design](01_architecture.md#11-frontend-architecture)

---

## What This Phase Does

Phase 2 parses a DOCUMENT node into a hierarchical tree of PARA (paragraph)
nodes. Each PARA represents a logical section or paragraph of the source
specification. PARAs are the finest unit of upstream traceability -- every
requirement traces back to a specific PARA.

This is the first agent-driven phase. The agent reads the document content
via tools (because documents may be large), identifies logical sections and
subsections, and creates PARA nodes mirroring the document's outline.

---

## Node Type Created

| Field | Value |
|-------|-------|
| Node type | **PARA** |
| Layer | 1 |
| Parent | DOCUMENT or PARA (nested sections allowed) |
| `trace_to` | Empty (PARAs never trace to anything) |

Each PARA carries its own content (not its children's content). The
`para_type` property classifies the paragraph: `"functional"`, `"rationale"`,
`"constraint"`, `"non_functional"`, or `"heading"` (for heading-only nodes
with no body text).

---

## Gap Type

| Gap Type | Priority | Condition |
|----------|----------|-----------|
| `UNCHUNKED_DOCUMENT` | 1 | DOCUMENT has no PARA children |

One gap is emitted per DOCUMENT node that has no children. Since the
standard flow has one DOCUMENT, there is typically one gap.

---

## Dispatch Strategy

**Structural dispatch.** One document, one conversation. The agent processes
the entire document in a single invocation, creating all PARAs in one pass.
There is no batching because there is typically only one UNCHUNKED_DOCUMENT
gap.

---

## Context Provided

**None in the prompt.** The agent reads the document content interactively
via `graph_read` tool calls because the document may be large. This avoids
blowing the context window with the full document text upfront.

---

## Agent Procedure

The agent is a Document Specialist (role used for model selection and prompt
resolution). Tools: `graph_read`, `graph_add_node`.

```
1. graph_read get_node(DOCUMENT_ID)     -- read full document content
2. graph_read get_children(DOCUMENT_ID) -- check for existing PARAs
3. For each top-level section:
     graph_add_node(PARA, parent_id=DOCUMENT_ID, content=<section text>,
                    title="3-5 words", para_type=<classification>)
     For each subsection:
       graph_add_node(PARA, parent_id=<parent PARA ID>, content=...,
                      title="3-5 words", para_type=<classification>)
```

**Hierarchy rules:**
- Top-level sections become PARA children of DOCUMENT
- Subsections become PARA children of their parent section PARA
- Each PARA carries its own content, not its children's content
- Heading-only nodes (no body text) use `para_type: "heading"`

---

## Pipeline Steps

| Step | Function | What It Does |
|------|----------|-------------|
| 1 | `structural` | Dispatch agent to close `UNCHUNKED_DOCUMENT` gaps |
| 2 | `quality_gaps` | Detect and dispatch quality gaps on PARA nodes |
| 3 | `semantic` | Detect and remove semantic duplicate PARAs |

Default pipeline: `[structural, quality_gaps, semantic]`.

After all steps complete, if any step deleted nodes the pipeline cycles --
re-runs all steps -- because deletions can uncover new gaps. Stable when no
deletions occur.

---

## Quality Checks

Quality gaps that surface on PARA nodes during this phase:

| Gap Type | Detection | Meaning |
|----------|-----------|---------|
| `STALE_NODE` | Deterministic | PARA older than its parent DOCUMENT |
| `ORPHAN_NODE` | Deterministic | PARA parent missing or wrong type |
| `EMPTY_CONTENT` | Deterministic | PARA with no content (non-heading) |
| `UNTITLED_NODE` | Deterministic | Missing or too-long title |
| `DUPLICATE_NODE` | Deterministic + LLM | Exact-hash or semantic duplicate among sibling PARAs |

These are handled inline by the same phase agent -- the Document Specialist
already has the domain context to decide whether to fix, merge, or delete.

---

## Cumulative Audit

After stabilisation, the `PhaseAuditor` checks that `UNCHUNKED_DOCUMENT` is
absent across the entire graph. This is cumulative: all gap types from
phases 0-2 must be absent.

---

## Frontend Dashboard

**Route**: `/phase/2`

Standard **PhaseDashboard** layout:

- **PhaseLifecyclePanel** (left): Shows the 3 pipeline steps (structural,
  quality_gaps, semantic) with status indicators (pending / running / done).
  Cycle indicator shows re-run count if deletions triggered cycling.

- **NodeTablePanel** (right): Shows all PARA nodes produced by this phase.
  Type filter chips: `[ALL] [PARA]`. Selecting a PARA shows its content,
  `para_type` property, and parent chain in the detail panel.

```
+-----------------------------+----------------------------------+
| PhaseLifecyclePanel         | NodeTablePanel                   |
|                             |                                  |
| Step 1: Structural    [OK]  | [ALL] [PARA]                     |
| Step 2: Quality Gaps  [OK]  |                                  |
| Step 3: Semantic      [OK]  | PARA  System Overview            |
|                             | PARA  Input Requirements         |
| Cycles: 1                   | PARA  Performance Constraints    |
|                             | PARA  Error Handling             |
+-----------------------------+----------------------------------+
```
