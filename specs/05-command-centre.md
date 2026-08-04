# 05 — Command Centre

The Command Centre at route `/` is the user's default view. It is the live
cockpit for the Observe-Act loop.

## Layout

```
+------------------------------------------------------------+
| StatusBar: Project | IDLE/RUNNING | [Play/Pause] | iter #N |
+----------------+--------------------+----------------------+
| WORK QUEUE     | ARENA              | SYSTEM LOG           |
|                |                    |                      |
| Prioritised    | Live view of the   | Append-only log of   |
| open gaps      | currently running  | backend events and   |
|                | agent              | phase transitions    |
+----------------+--------------------+----------------------+
```

## StatusBar

- Shows project name (from `PROJECT.title`).
- Shows current loop state (`IDLE` / `RUNNING` / `STOPPING`).
- Hosts the single Play/Pause control — see
  [04-loop-control.md](04-loop-control.md).
- Shows the iteration counter, incremented once per observe-act cycle.

## Work Queue

- Lists open gaps in priority order.
- Each entry shows gap type, target node, and current age.
- At Phase 0 the work queue is empty (no gaps exist yet).
- Items disappear from the queue as agents resolve them.

## Arena

- Inactive when the loop is IDLE.
- When an agent is dispatched, the arena shows:
  - Which agent is running (role + name).
  - The gap it is resolving.
  - A live stream of tool calls and their results.

## System Log

- Streams backend events: project created, phase started/completed, provider
  changed, errors.
- Timestamped, append-only. The user cannot edit the log from the UI.

## Sidebar

- The phase strip on the sidebar highlights the current phase and marks
  completed phases. Visible on every screen, not just the Command Centre.
