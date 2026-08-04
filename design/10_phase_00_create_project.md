# Phase 0 -- Create Project

**Related docs**: [Architecture](./01_architecture.md)

---

## What This Phase Does

Phase 0 is human-initiated. The user creates a project via the UI or CLI.
This establishes the PROJECT node -- the single root of the entire project
graph. Every other node in the system is a descendant of PROJECT.

There is no agent, no gap type, and no LLM involvement. The phase completes
when the PROJECT node exists.

---

## Node Type Created

| Field | Value |
|-------|-------|
| Node type | **PROJECT** |
| Layer | 0 |
| Parent | None (`parent_id` is null) |
| `trace_to` | Empty (PROJECT never traces to anything) |

PROJECT is the only node in the graph with no parent. It is created once per
workspace and never duplicated. Its `content` field holds the project
description. Its `title` holds the project name.

---

## Gap Type

None. Phase 0 has no gap type. The Gap Analyser does not produce gaps for
this phase -- project creation is a prerequisite for the system to function,
not a gap to be resolved.

---

## Dispatch Strategy

None. No agent is dispatched. The PROJECT node is created directly by the
backend when the user initiates a new project via `POST /api/phases/start`
or the UI's project creation flow.

---

## Context Provided

None. There is no agent to receive context.

---

## Agent Procedure

None. Phase 0 is handled by `ForgeFlow` directly. The handler marks the
project as initialised and creates the PROJECT node if it does not already
exist.

```
ForgeFlow._run_phase(0):
  1. Check if PROJECT node exists
  2. If not, create PROJECT node with user-provided name and description
  3. Mark phase 0 complete
```

---

## Pipeline Steps

None. Phase 0 does not use the phase pipeline. It is a special-case phase
handled inline by `ForgeFlow.kickoff_async()`.

---

## Quality Checks

None. PROJECT is a container type and is exempt from `EMPTY_CONTENT` checks.
It is also exempt from `UNTITLED_NODE` checks. No quality gaps surface in
this phase.

---

## Cumulative Audit

Phase 0 has no completion criteria in `PHASE_COMPLETION_CRITERIA`. The
cumulative audit for phases 0-1 requires no gap types to be absent.

---

## Frontend Dashboard

**Route**: `/phase/0`

The Phase 0 dashboard is the **Command Centre** entry point. It shows:

- **StatusBar**: Project name (from PROJECT.title), loop status
  (IDLE / RUNNING / STOPPING), Play/Pause control, iteration counter.
- **Work Queue**: Empty at Phase 0 (no gaps exist yet).
- **Arena**: Inactive (no agent running).
- **System Log**: Shows project creation event.

The sidebar phase strip highlights Phase 0 as complete once the PROJECT node
exists. The Play button on the StatusBar starts the full build flow from
Phase 1 onward.

```
+---------------------------------------------------------+
| StatusBar: Project Name | IDLE | [Play]                 |
+-----------------+-------------------+-------------------+
| WORK QUEUE      | ARENA             | SYSTEM LOG        |
|                 |                   |                   |
| (empty)         | (inactive)        | INFO project      |
|                 |                   |   created         |
+-----------------+-------------------+-------------------+
```
