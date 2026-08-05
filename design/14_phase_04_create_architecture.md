# Phase 4: Create Architecture

## Purpose

Phase 4 produces the ARCHITECTURE node -- the system-level decomposition document that
bridges high-level requirements to modular design. It enforces architecture-first
discipline: the full skeleton (Phases 4-6) must be complete before LLRs in Phase 7.

## Node Type Created

| Field     | Value                          |
|-----------|--------------------------------|
| Type      | ARCHITECTURE                   |
| Layer     | 3                              |
| Parent    | PROJECT                        |
| trace_to  | HLR[] (all HLRs it addresses) |

There is exactly one ARCHITECTURE node per project.

## Gap Type

**UNARCHITECTED** -- raised when a PROJECT exists with HLRs but no ARCHITECTURE child.
Since there is one PROJECT and one ARCHITECTURE, this phase produces a single gap.

## Dispatch Strategy

**Structural dispatch.** One gap, one agent call. The gap scanner walks the tree
structurally: if PROJECT has no ARCHITECTURE child, emit the gap.

## Tools

- `graph_read` -- read PROJECT content and all HLR nodes.
- `graph_add_node` -- create the ARCHITECTURE node.
- `graph_update_node` -- revise ARCHITECTURE content on re-runs.

## Context Provided to the Agent

| Slot             | Content                                      |
|------------------|----------------------------------------------|
| Ancestor chain   | PROJECT node (id, title, content)            |
| HLR roster       | All HLR nodes (id, parent, title, content)   |

The architect needs the full requirements landscape to decompose the system into
modules. Every HLR is included so the architecture can account for all of them.

## Agent Procedure

1. Read all HLRs from the graph.
2. Identify natural module boundaries: cohesive clusters of requirements, shared data
   flows, independent deployment units.
3. Write a modular architecture document covering:
   - Module inventory with one-paragraph responsibility statement each.
   - Key interfaces between modules.
   - Cross-cutting concerns (auth, logging, error handling).
   - Rationale linking module boundaries back to requirement clusters.
4. Create the ARCHITECTURE node as child of PROJECT.
5. Set `trace_to` to reference every HLR the architecture addresses.

## Pipeline Steps

| Order | Step           | Purpose                                         |
|-------|----------------|--------------------------------------------------|
| 1     | structural     | Detect UNARCHITECTED gap                         |
| 2     | quality_gaps   | Check ARCHITECTURE for content quality           |
| 3     | semantic        | Embed ARCHITECTURE node for similarity search   |

## Quality Checks

| Quality Gap         | Trigger                                              |
|---------------------|------------------------------------------------------|
| STALE_NODE          | ARCHITECTURE's provenance hash mismatches parent PROJECT content |
| EMPTY_CONTENT       | ARCHITECTURE node has blank or whitespace-only content |
| INADEQUATE_CONTENT  | Content is too short or lacks module decomposition detail |

The architecture must be substantive -- a few bullet points do not qualify.
INADEQUATE_CONTENT fires when the content lacks the structural depth expected
of a system decomposition document.

## Frontend Dashboard

**Phase Dashboard.** Displays the ARCHITECTURE node content in a read-only panel.
Status badge shows whether the node is current or has quality gaps.

**Architecture Dashboard (React Flow).** A directed graph visualization:
- ARCHITECTURE node at the root.
- MODULE nodes (created in Phase 5) as children.
- Edges from each MODULE back to the HLRs it traces to.

This graph is empty after Phase 4 alone; it populates as Phases 5-6 execute.

## Relationship to Other Phases

- **Depends on:** Phase 3 (HLRs must exist).
- **Blocks:** Phase 5 (module assignment), Phase 6 (contracts), Phase 7 (LLRs).
- The architecture is the load-bearing skeleton. All downstream decomposition
  references it.
