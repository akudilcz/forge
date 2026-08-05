# Phase 1 -- Ingest Document

**Related docs**: [03 Graph Design](01_architecture.md#2-project-graph) -- [05 Backend Architecture](01_architecture.md#9-backend-infrastructure) -- [09 Frontend Design](01_architecture.md#11-frontend-architecture)

---

## What This Phase Does

Phase 1 is a deterministic file read. It reads `forge.md` from the workspace
directory and creates a DOCUMENT node as a child of PROJECT. The entire file
content is stored in the DOCUMENT node's `content` field.

There is no agent and no LLM involvement. The handler is
`_run_ingest_phase` in `backend/pipeline/special_phases.py` (mixed into
`ForgeFlow`). If `forge.md` does not exist at the
expected workspace path, the phase fails with a clear error message.

The DOCUMENT node is the entry point for all downstream analysis -- every
requirement, design, and test case ultimately traces back through DOCUMENT
to the source specification.

---

## Node Type Created

| Field | Value |
|-------|-------|
| Node type | **DOCUMENT** |
| Layer | 1 |
| Parent | PROJECT |
| `trace_to` | Empty (DOCUMENT never traces to anything) |

DOCUMENT nodes carry a `properties["slug"]` field that stores the document
slug for lookup. One or more DOCUMENT nodes may exist under PROJECT (one per
source specification file), but the standard flow creates one from
`forge.md`.

---

## Gap Type

None. Phase 1 has no gap type. Like Phase 0, document ingestion is a
prerequisite step, not a gap to be resolved by an agent.

---

## Dispatch Strategy

None. No agent is dispatched. The ingestion handler reads the file directly
from the filesystem and creates the DOCUMENT node via the ProjectGraph
engine.

---

## Context Provided

None. There is no agent to receive context.

---

## Agent Procedure

None. Phase 1 is handled by a dedicated handler in `ForgeFlow`:

```
ForgeFlow._run_ingest_phase():
  1. Resolve workspace path to forge.md
  2. If file does not exist -> fail with error
  3. Read file content as UTF-8 text
  4. Create DOCUMENT node:
     - parent_id = PROJECT node ID
     - content   = full file text
     - title     = filename or document heading
     - slug      = "forge" (stored in properties)
  5. Mark phase 1 complete
```

The ingestion is also available as an operator tool (`ingest_document`) and
via `POST /api/phases/1/ingest` for manual re-ingestion.

---

## Pipeline Steps

None. Phase 1 does not use the phase pipeline. It is a special-case phase
with a dedicated handler, like Phase 0.

---

## Quality Checks

None. DOCUMENT is a container type and is exempt from `EMPTY_CONTENT`
checks. It is also exempt from `UNTITLED_NODE` checks. No quality gaps
surface in this phase.

---

## Cumulative Audit

Phase 1 has no completion criteria in `PHASE_COMPLETION_CRITERIA`. The
cumulative audit for phases 0-1 requires no gap types to be absent.

---

## Frontend Dashboard

**Route**: `/phase/1`

The Phase 1 dashboard uses the standard **PhaseDashboard** layout:

- **PhaseLifecyclePanel** (left): Shows phase status (pending / complete /
  error). No pipeline steps to display -- ingestion is a single atomic
  operation.
- **NodeTablePanel** (right): Shows the DOCUMENT node once ingestion
  completes. Selecting the DOCUMENT node displays its full content in the
  detail panel (Header, Content, Properties sections).

When ingestion fails (missing `forge.md`), the phase status shows `error`
with the failure reason displayed in the lifecycle panel.

```
+-----------------------------+----------------------------------+
| PhaseLifecyclePanel         | NodeTablePanel                   |
|                             |                                  |
| Phase 1: Ingest Document    | [DOCUMENT] forge.md              |
| Status: complete            |                                  |
|                             | Content:                         |
| (no pipeline steps)         |   (full forge.md text, scrollable|
|                             |    in detail panel)              |
+-----------------------------+----------------------------------+
```
