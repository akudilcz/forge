# 04 — Observe-Act Loop Control

The Observe-Act loop is FORGE's engine: inspect the graph for gaps, dispatch
an agent to resolve the highest-priority gap, re-inspect, repeat. The user
controls the loop through a single pair of actions on the Command Centre
status bar.

## Loop states

| State      | Meaning                                                   |
|------------|-----------------------------------------------------------|
| `IDLE`     | Loop is stopped. No agent running. No iterations queued.  |
| `RUNNING`  | Loop is actively observing and dispatching.              |
| `STOPPING` | Stop requested; current iteration is allowed to finish.   |

State is shown on the status bar and is visible from every screen.

## Controls

- **Play** — transitions `IDLE → RUNNING`. Begins a new iteration of the loop,
  resuming from the current phase.
- **Pause** — transitions `RUNNING → STOPPING → IDLE`. The currently
  executing agent step is allowed to finish cleanly; no new dispatch happens.
- **Iteration counter** — increments once per observe-act cycle. Resets to
  zero when a new project is created.

## Safety

- Pressing Pause never corrupts graph state: in-flight agent actions either
  complete or are aborted at a tool-call boundary. Partial writes are avoided
  because each tool call is atomic at the graph layer.
- Pressing Play while the loop is already RUNNING is a no-op.
- Pressing Pause while the loop is already IDLE is a no-op.

## What "one iteration" means

One iteration is:

1. Gap Analyser scans the graph.
2. Highest-priority gap is selected.
3. An agent is dispatched with curated context for that gap.
4. Agent writes its artefacts and graph edges.
5. Gap Analyser re-scans; the loop continues or the phase completes.

When the current phase has no remaining blocking gaps, the loop pauses at the
human approval gate — see [03-build-pipeline.md](03-build-pipeline.md).

## Interruption semantics

If the backend is terminated mid-iteration, the append-only audit log records
the last completed tool call. On next launch, the user can inspect partial
state in the Graph Inspector and re-press Play; the Gap Analyser will
re-detect any remaining gaps.
