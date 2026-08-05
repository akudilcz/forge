# Phase 6: Write Contracts

## Purpose

Phase 6 writes a CONTRACT for each MODULE. A contract is the public interface
specification: function signatures, pre/post conditions, invariants. Contracts are
the last phase of the architectural skeleton -- they must be complete before LLRs.

## Node Type Created

| Field     | Value           |
|-----------|-----------------|
| Type      | CONTRACT        |
| Layer     | 4               |
| Parent    | MODULE          |
| trace_to  | (none)          |

One CONTRACT per MODULE. The contract does not use trace_to because its scope is
defined entirely by its parent MODULE (which already traces to HLRs).

## Gap Type

**UNCONTRACTED** -- raised for each MODULE that has no CONTRACT child. The gap
scanner walks every MODULE and checks for the presence of a CONTRACT child node.

## Dispatch Strategy

**Structural dispatch.** One gap per MODULE, one agent call per gap. Contracts are
independent of each other -- MODULE A's interface does not constrain MODULE B's
interface at authoring time. Parallelism is safe.

## Tools

- `graph_read` -- read MODULE, ARCHITECTURE, and traced HLRs.
- `graph_add_node` -- create the CONTRACT node.

## Context Provided to the Agent

| Slot              | Content                                            |
|-------------------|----------------------------------------------------|
| Ancestor chain    | MODULE (id, title, content) + PROJECT (id, title)  |
| ARCHITECTURE      | Full ARCHITECTURE content                          |
| Traced HLRs       | HLRs traced to this MODULE (id, title, content)   |

The contract must reflect the module's requirements within the system architecture.
The agent needs the module's responsibility statement, the broader architecture for
cross-module interface alignment, and the specific HLRs the module must satisfy.

## Agent Procedure

1. Read the MODULE content (responsibility statement).
2. Read the ARCHITECTURE content for system-wide interface conventions.
3. Read all HLRs traced to this MODULE.
4. Write the CONTRACT specifying:
   - Public function/method signatures with parameter and return types.
   - Preconditions (what callers must guarantee).
   - Postconditions (what the module guarantees on success).
   - Invariants (properties maintained across all operations).
   - Error behaviour (failure modes and their handling).
5. Create the CONTRACT node as child of the MODULE.

## Pipeline Steps

| Order | Step           | Purpose                                         |
|-------|----------------|--------------------------------------------------|
| 1     | structural     | Detect UNCONTRACTED gaps                         |
| 2     | quality_gaps   | Check CONTRACT nodes for content quality         |
| 3     | semantic        | Embed CONTRACT nodes for similarity search      |

## Quality Checks

| Quality Gap         | Trigger                                               |
|---------------------|-------------------------------------------------------|
| STALE_NODE          | CONTRACT's provenance hash mismatches parent MODULE content |
| EMPTY_CONTENT       | CONTRACT has blank or whitespace-only content          |
| INADEQUATE_CONTENT  | Content lacks function signatures or conditions        |

INADEQUATE_CONTENT is strict for contracts. A contract that says "handles user
authentication" without specifying function signatures, parameters, return types,
and error cases is inadequate. Precision is the entire point.

## Frontend Dashboard

**Phase Dashboard.** NodeTablePanel listing CONTRACT nodes alongside their parent
MODULE names. Columns: MODULE title, CONTRACT title, content preview, status badge,
quality gaps.

Users can expand a row to see the full contract content inline. This pairs the
contract with its module context for easy review.

## Relationship to Other Phases

- **Depends on:** Phase 5 (MODULEs must exist with HLR assignments).
- **Blocks:** Phase 7 (LLR derivation uses contracts as boundaries).
- Contracts close the architectural skeleton. After Phase 6, the system has:
  PROJECT → ARCHITECTURE → MODULE[] → CONTRACT[]. Phase 7 fills in the
  detailed requirements (LLRs) within this structure.
