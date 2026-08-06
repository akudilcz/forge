# Phase 5: Assign Modules

## Purpose

Phase 5 assigns every HLR to a MODULE. Modules are the architectural building blocks
defined in the ARCHITECTURE document. This phase reifies them as graph nodes and
links each HLR to the module responsible for satisfying it.

## Node Type Created

| Field     | Value                            |
|-----------|----------------------------------|
| Type      | MODULE                           |
| Layer     | 4                                |
| Parent    | ARCHITECTURE                     |
| trace_to  | HLR[] (HLRs assigned to module)  |

Multiple MODULE nodes are created -- one per identified module.

## Gap Type

**UNMODULARISED** -- raised for each HLR that is not yet traced-to by any MODULE.
All unassigned HLRs are collected into a single batch gap.

## Dispatch Strategy

**Chunked batch dispatch.** HLR-to-MODULE assignment is a global optimization
problem: all HLRs compete for the same modules, and the agent must see the full
picture to avoid fragmentation or duplication. Unassigned HLRs are processed in
chunks of `LLMConfig.batch_author_chunk_size` (one agent call per chunk, static
MODULE/CONTRACT/ARCHITECTURE snapshot shared across chunks) so the response
never hits the provider output-token limit on large graphs. Per-chunk retry;
stragglers fall back to per-gap structural dispatch (see design/02 §Batch
prompts).

## Tools

- `graph_read` -- read HLRs, existing MODULEs, and ARCHITECTURE content.
- `graph_add_node` -- create new MODULE nodes.
- `graph_add_traces` -- link HLRs to existing MODULEs via trace_to.

## Context Provided to the Agent

| Slot              | Content                                          |
|-------------------|--------------------------------------------------|
| Unassigned HLRs   | All HLRs not yet traced-to by any MODULE         |
| Existing MODULEs  | All MODULE nodes (id, title, content, traces)    |
| ARCHITECTURE      | ARCHITECTURE content (truncated to 2000 chars)   |

The agent needs the full assignment picture: which HLRs remain, which modules exist,
and what the architecture prescribes. Truncation keeps the prompt within budget while
preserving the module inventory from the architecture document.

## Agent Procedure

1. Read the ARCHITECTURE content to understand prescribed module boundaries.
2. Read all existing MODULEs and their current HLR assignments.
3. For each unassigned HLR:
   - **Preferred action:** assign it to an existing MODULE by adding a trace link.
   - **Fallback:** create a new MODULE node if no existing module fits.
4. When creating a MODULE, write a responsibility statement as its content and set
   `trace_to` to reference the HLRs it addresses.
5. Verify every HLR is now assigned to exactly one MODULE.

## Pipeline Steps

| Order | Step           | Purpose                                          |
|-------|----------------|--------------------------------------------------|
| 1     | batch_phase5     | Collect UNMODULARISED gaps, dispatch batch agent  |
| 2     | quality_gaps     | Check MODULE nodes for content and trace quality  |
| 3     | combined_quality | Batched LLM judging of authored nodes (title axes) |
| 4     | semantic         | Detect and remove semantic duplicate MODULEs      |

## Quality Checks

| Quality Gap         | Trigger                                               |
|---------------------|-------------------------------------------------------|
| STALE_NODE          | MODULE's provenance hash mismatches parent ARCHITECTURE content |
| EMPTY_CONTENT       | MODULE has blank or whitespace-only content            |
| INADEQUATE_CONTENT  | Content lacks a clear responsibility statement         |
| EMPTY_TRACE         | MODULE has no trace_to links (must trace to >= 1 HLR) |

EMPTY_TRACE is critical -- a MODULE that traces to no HLR serves no purpose and
indicates a bug in the assignment logic.

## Frontend Dashboard

**Phase Dashboard.** NodeTablePanel listing all MODULE nodes with columns:
title, HLR count (trace_to length), status badge, quality gaps.

**Architecture Dashboard (React Flow).** Displays MODULE → HLR trace links as
directed edges. Nodes are colour-coded: MODULE nodes in one colour, HLR nodes in
another. Clicking a MODULE highlights its traced HLRs.

## Relationship to Other Phases

- **Depends on:** Phase 4 (ARCHITECTURE must exist).
- **Blocks:** Phase 6 (each MODULE needs a CONTRACT).
- Modules are the organisational spine -- every downstream artifact (CONTRACT, LLR,
  code) is scoped to a module.
