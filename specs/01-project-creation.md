# 01 — Project Creation

The user creates exactly one project per workspace. Project creation is the
only fully human-driven step in the pipeline; no agent and no LLM are involved.

## Trigger

Either:

- `POST /api/phases/start` with a project name and description, or
- the project creation flow in the Command Centre UI on first launch.

## Inputs

| Field         | Required | Notes                                    |
|---------------|----------|------------------------------------------|
| `name`        | yes      | Becomes the PROJECT node's `title`.      |
| `description` | yes      | Becomes the PROJECT node's `content`.    |

## Effects

- A **PROJECT** node is created as the root of the Project Graph. It has no
  parent and never traces to another node.
- The project is marked initialised; the Command Centre unlocks the **Play**
  button.
- The SQLite Project Graph database is initialised at `.forge/forge.db`,
  the pretty-print log at `.forge/forge.log`, and the structured log store
  at `.forge/forge.logs.db`.

## Idempotency

Project creation runs at most once per workspace. If a PROJECT node already
exists, re-submission is a no-op that returns the existing project.

## Errors

- If the workspace directory is not writable, the request fails loudly with a
  filesystem error; no partial state is left behind.
- If `name` or `description` is empty, the request is rejected before any node
  is created.

## Not covered here

- What the user does next (drop `forge.md`, press Play) — see
  [02-whitepaper-ingest.md](02-whitepaper-ingest.md) and
  [04-loop-control.md](04-loop-control.md).
