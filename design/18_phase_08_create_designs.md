# Phase 8 — Create Designs

## Purpose

Phase 8 produces DESIGN nodes that describe how each MODULE will implement its
assigned Low-Level Requirements. Every LLR must trace to at least one DESIGN;
multiple LLRs may consolidate into a single DESIGN when they share a class or
responsibility boundary. The phase enforces consolidation so that a MODULE's
class plan stays cohesive rather than proliferating one-DESIGN-per-LLR.

## Node Type

| Field | Value |
|-------|-------|
| Type | **DESIGN** |
| Layer | 5 |
| Parent | MODULE |
| trace_to | LLR[] |

A DESIGN specifies the class name, method signatures, and responsibilities that
realise the traced LLRs within its parent MODULE.

## Gap Type

**`UNDESIGNED`** — raised for every LLR that is not yet traced-to by any DESIGN
node within its MODULE.

## Dispatch Strategy

**Batch dispatch, per-MODULE.** LLRs compete for DESIGNs inside a module
boundary, so the agent must see every undesigned LLR in a module at once. One
agent call per MODULE that contains at least one UNDESIGNED gap.

### Fast-Path

When a MODULE already owns a DESIGN whose scope covers an incoming LLR, the
pipeline links the LLR directly via `trace_to` without invoking the LLM. This
avoids redundant agent calls for incremental re-plans.

## Context Provided to the Agent

Per-MODULE batch context:

- **MODULE** node (name, boundary description).
- **CONTRACT** node for the module (interface obligations).
- **Undesigned LLRs** — the batch of LLRs that still lack a DESIGN trace.
- **Existing DESIGNs** — all current DESIGN nodes under the MODULE so the agent
  can extend or reuse them rather than create duplicates.

## Tools

| Tool | Usage |
|------|-------|
| `graph_read` | Read MODULE, CONTRACT, LLR, and existing DESIGN nodes. |
| `graph_add_node` | Create new DESIGN nodes as children of MODULE. |
| `graph_add_traces` | Link DESIGN → LLR via `trace_to`. |

## Agent Procedure

1. Read the MODULE, its CONTRACT, and all existing DESIGNs.
2. Read the batch of undesigned LLRs.
3. Group LLRs by shared responsibility — if an existing DESIGN already covers
   an LLR's concern, extend its trace list instead of creating a new node.
4. For genuinely new concerns, create a DESIGN node specifying class name,
   method signatures, and responsibilities.
5. Add `trace_to` links from each DESIGN to the LLRs it implements.

## Pipeline Steps

| Step | Purpose |
|------|---------|
| `batch_phase8` | Groups UNDESIGNED gaps by MODULE; dispatches one agent call per MODULE. Gaps still unresolved after batch attempts exhaust fall back to per-gap structural dispatch. |
| `quality_gaps` | Raises quality gaps on DESIGN nodes (see below). |
| `semantic` | Validates semantic consistency between DESIGNs and their traced LLRs. |
| `design_consolidation` | Merges DESIGN sprawl within each MODULE — if the agent created too many small DESIGNs, this step consolidates them into fewer, cohesive nodes. |

## Quality Gaps

| Gap | Trigger |
|-----|---------|
| STALE_NODE | DESIGN's provenance hash mismatches parent MODULE content. |
| EMPTY_CONTENT | DESIGN body is blank or trivially short. |
| INADEQUATE_CONTENT | Missing class name, method signatures, or responsibilities. |
| CONTRACT_VIOLATION | DESIGN contradicts the CONTRACT's `properties.public_api` surface: it declares an annotated signature reusing a public function's name whose parameter names (and stated return type, when both sides state one) disagree with the `public_api` entry. Internal helpers a DESIGN declares (methods of private classes, underscore names) are **not** violations — the CONTRACT lists only the public surface. Legacy contracts without `public_api` fall back to the older token-subset check against the CONTRACT text. |
| CROSS_MODULE_COUPLING | DESIGN references internals of another MODULE. |
| INCONSISTENT_CONTENT | Two DESIGNs under the same MODULE overlap or conflict. |

## Frontend Dashboard

**Phase Dashboard** — NodeTablePanel listing all DESIGN nodes with columns for
parent MODULE, traced LLR count, and quality status.

**Implementation Dashboard** — shows DESIGN → LLR trace links alongside the
MODULE's CONTRACT context, letting reviewers verify that every LLR is covered
and that each DESIGN respects its contract.
