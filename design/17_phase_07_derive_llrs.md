# Phase 7: Derive LLRs

## Purpose

Phase 7 derives Low-Level Requirements (LLRs) from HLRs. Each LLR is an atomic,
testable requirement written within the context of the MODULE that owns the parent
HLR and that module's CONTRACT interface. This is why architecture comes before LLRs.

## Node Type Created

| Field     | Value        |
|-----------|--------------|
| Type      | LLR          |
| Layer     | 2            |
| Parent    | HLR          |
| trace_to  | (none)       |

Multiple LLRs per HLR. Each LLR is a child of the HLR it refines.

## Gap Type

**UNREFINED_HLR** -- raised for each HLR that has no LLR children, or whose LLR
children have been invalidated by quality checks. All unrefined HLRs are collected
into a single batch gap.

## Dispatch Strategy

**Chunked batch dispatch.** LLRs from different HLRs can overlap -- two HLRs may
decompose into the same low-level behaviour. The prompt's static snapshot (all
existing LLRs + MODULE/CONTRACT context) gives the agent the full picture, while
the unrefined-HLR list is processed in chunks of
`LLMConfig.batch_author_chunk_size` (one agent call per chunk) so the authored
LLR output never hits the provider output-token limit. Per-chunk retry;
stragglers fall back to per-gap structural dispatch (see design/02 §Batch
prompts).

## Tools

- `graph_read` -- read HLRs, existing LLRs, MODULEs, and CONTRACTs.
- `derive_requirement` -- produce atomic LLRs from an HLR (specialised tool).
- `graph_add_node` -- create new LLR nodes.
- `graph_reparent_node` -- move an existing LLR to a different HLR parent.

## Context Provided to the Agent

| Slot               | Content                                           |
|--------------------|---------------------------------------------------|
| Unrefined HLRs     | All HLRs with no (valid) LLR children            |
| Existing LLRs      | All LLR nodes (id, parent, title, content)        |
| MODULEs            | All MODULE nodes (id, title, content, traces)     |
| CONTRACTs          | All CONTRACT nodes (id, parent, content)          |

LLR derivation must account for existing LLRs (to avoid duplication) and
architectural boundaries (to respect module/contract scope).

## Agent Procedure

1. Read all unrefined HLRs and group them by their owning MODULE (via trace links).
2. For each HLR, read the MODULE content and its CONTRACT.
3. Use `derive_requirement` to decompose the HLR into atomic LLRs that:
   - Map to specific functions/interfaces in the CONTRACT.
   - Are independently testable.
   - Follow the EARS pattern (Event, Action, Response, State).
4. For each derived LLR:
   - **Preferred action:** re-parent an existing LLR if one already covers the
     same behaviour (avoids duplication).
   - **Fallback:** create a new LLR node as child of the source HLR.
5. Verify decomposition completeness -- every aspect of the HLR must be covered.

## Pipeline Steps

| Order | Step              | Purpose                                                   |
|-------|-------------------|-----------------------------------------------------------|
| 1     | batch_phase7      | Collect UNREFINED_HLR gaps, dispatch batch agent          |
| 2     | quality_gaps      | Deterministic graph-integrity checks on LLRs              |
| 3     | combined_quality  | One batched LLM call judges atomicity + EARS + title axes |
| 4     | semantic          | Detect and remove semantic duplicate LLRs                 |

## Quality Checks

### Requirement + title quality (combined_quality step — one batched LLM call)

| Quality Gap                  | Trigger                                        |
|------------------------------|------------------------------------------------|
| MALFORMED_REQUIREMENT        | LLR does not follow requirement syntax          |
| NON_ATOMIC_REQUIREMENT       | LLR contains multiple independent conditions    |
| NON_EARS_REQUIREMENT         | LLR does not use EARS pattern                   |
| VAGUE_REQUIREMENT            | LLR uses ambiguous terms (e.g. "appropriate")   |
| UNTESTABLE_REQUIREMENT       | LLR cannot be verified by a concrete test        |
| CONTRADICTORY_REQUIREMENTS   | Two LLRs specify conflicting behaviour           |
| INCOMPLETE_DECOMPOSITION     | HLR has aspects not covered by any child LLR     |

### General Quality (quality_gaps step)

| Quality Gap         | Trigger                                       |
|---------------------|-----------------------------------------------|
| STALE_NODE          | LLR's provenance hash mismatches parent HLR content |
| EMPTY_CONTENT       | LLR has blank or whitespace-only content       |
| INADEQUATE_CONTENT  | LLR content is too brief to be actionable      |
| TITLE_COLLIDES_WITH_PARENT | LLR title duplicates parent HLR title — scope not narrowed |
| SIBLING_TITLE_DUPLICATE | Two LLRs under the same HLR share identical titles |
| STALE_TITLE | LLM-judged: LLR title no longer matches its current content scope |
| VAGUE_TITLE | LLM-judged: LLR title is a generic label rather than a concrete noun phrase |

## Frontend Dashboard

**Phase Dashboard.** NodeTablePanel listing LLR nodes with columns: parent HLR
title, LLR title, content preview, quality gaps, status badge.

**Type filter chips** at the top: HLR | LLR. Toggling shows HLRs (with their LLR
children nested) or a flat LLR list. This lets reviewers switch between the
decomposition view and the full LLR inventory.

## Relationship to Other Phases

- **Depends on:** Phase 6 (CONTRACTs must exist -- architectural skeleton complete).
- **Blocks:** Downstream phases that consume LLRs (test derivation, implementation).
- Phase 7 is where architecture meets detailed specification. The quality bar is
  highest here -- every quality check category applies.
