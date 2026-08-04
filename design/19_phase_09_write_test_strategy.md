# Phase 9 — Write Test Strategy

## Purpose

Phase 9 produces the SUITE node — a single, comprehensive test strategy document
that governs all subsequent test-case authoring. It defines test types, coverage
targets, environment setup, and the verification approach for every module in the
system. Because there is exactly one PROJECT there is exactly one SUITE, making
this a single-gap phase.

## Node Type

| Field | Value |
|-------|-------|
| Type | **SUITE** |
| Layer | 6 |
| Parent | PROJECT |
| trace_to | _(none)_ |

The SUITE node contains prose covering:

- **Test types** — unit, integration, system, and any specialised categories.
- **Coverage targets** — quantitative goals per test type and module.
- **Test environment** — tooling, fixtures, infrastructure, and data setup.
- **Verification approach** — how each MODULE's requirements will be verified
  and the mapping strategy from requirements to test cases.

## Gap Type

**`UNSUITED`** — raised when the PROJECT has no SUITE child or the existing
SUITE is stale relative to the current architecture.

## Dispatch Strategy

**Structural dispatch.** One PROJECT → one gap → one agent call. The phase
never dispatches more than a single task.

## Context Provided to the Agent

The agent receives a broad, system-wide view:

- **Ancestor chain** — PROJECT and its metadata.
- **ARCHITECTURE** node — system-level design decisions and constraints.
- **All MODULE nodes** — names, boundaries, and descriptions.
- **All HLR nodes** — the full set of high-level requirements the test strategy
  must ultimately cover.

This wide context ensures the strategy is written with full knowledge of the
system rather than a narrow slice.

## Tools

| Tool | Usage |
|------|-------|
| `graph_read` | Read PROJECT, ARCHITECTURE, MODULEs, and HLRs. |
| `graph_add_node` | Create the SUITE node as a child of PROJECT. |

No `graph_add_traces` — SUITE does not trace to individual requirements.

## Agent Procedure

1. Read PROJECT metadata and the ARCHITECTURE node.
2. Read all MODULE nodes to understand system boundaries.
3. Read all HLR nodes to understand what must be verified.
4. Author the SUITE content:
   - Define test types applicable to this system.
   - Set coverage targets per module and test type.
   - Describe the test environment and setup requirements.
   - Map each module to a verification approach (which test types apply,
     integration points, risk areas needing deeper coverage).
5. Create the SUITE node as a child of PROJECT.

## Pipeline Steps

| Step | Purpose |
|------|---------|
| `structural` | Detects the single UNSUITED gap (missing or stale SUITE). |
| `quality_gaps` | Raises quality gaps on the SUITE node (see below). |
| `semantic` | Validates the strategy is consistent with ARCHITECTURE and covers all MODULEs. |

## Quality Gaps

| Gap | Trigger |
|-----|---------|
| STALE_NODE | ARCHITECTURE or MODULE set changed after the SUITE was written. |
| EMPTY_CONTENT | SUITE body is blank or trivially short. |
| INADEQUATE_CONTENT | Strategy missing required sections (test types, coverage targets, environment, or verification approach). |

## Frontend Dashboard

**Phase Dashboard** — displays the SUITE node content in a full-width prose
panel. Status badge indicates whether the strategy is current or stale.

**Verification Dashboard** — shows the SUITE alongside the MODULE list so
reviewers can confirm every module is addressed by the strategy.
