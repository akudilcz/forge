# 05 — Command Centre

The Command Centre at route `/` is the user's default view. It is the live
cockpit for the Observe-Act loop.

## Layout

```
+------------------------------------------------------------+
| Pipeline rail: 15 phase segments | build status | wall time |
+------------------------------------------------------------+
| Header: Run All / Stop | Reset                              |
+------------------------------------------------------------+
| PIPELINE OVERVIEW — one card per phase (status, gaps, time) |
+------------------------------------------------------------+
| FORGE.MD EDITOR (fills remaining space)                     |
+------------------------------------------------------------+
| Console (log stream + request input)  |  Work Queue         |
+------------------------------------------------------------+
```

## Pipeline rail

- Shown at the top of **every** screen (mounted in the layout, not just the
  Command Centre). All 15 phases as a connected track.
- Per-phase status is encoded in form as well as colour: complete = check,
  active = spinner, awaiting approval = pause glyph, pending = hollow.
- Structural gap counts appear as a badge on the owning phase's segment.
- Wall time per phase is clocked client-side from websocket status
  transitions and shown in the segment tooltip; the active phase's elapsed
  time is shown live at the right of the rail.
- The rail also shows the loop state (Building / Idle) and iteration counter.
- Clicking a segment navigates to that phase's dashboard.

## Pipeline overview (hero)

- One card per phase: colour spine, phase icon, number and name, status
  glyph, open structural gap count, and elapsed wall time.
- The active phase's card is emphasised (accent border, subtle pulse —
  suppressed under `prefers-reduced-motion`).
- Clicking a card navigates to `/phase/N`.

## Header controls

- **Run All** starts the loop end to end; while running it is replaced by
  **Stop**. See [04-loop-control.md](04-loop-control.md).
- **Reset** clears all derived nodes, keeping Forge.md.

## Forge.md editor

- Edits the ingested DOCUMENT node in place. Saving cascades: derived nodes
  are purged and phases 2+ reset to pending.

## Console and Work Queue

- The bottom panel (visible on every screen) splits into the console log and
  the work queue.
- Console entries encode severity in form, not just colour: WARN and ERROR
  rows carry an icon and a tinted left rule.
- LLM and tool entries surface telemetry inline — model, prompt→completion
  tokens, and duration chips — from the FORGE_LOG payload.
- The console input sends free-text requests to the backend console agent.

## Sidebar

- The phase strip on the sidebar highlights the current phase and marks
  completed phases. Visible on every screen, not just the Command Centre.
- The sidebar footer hosts the light/dark theme toggle; the preference is
  persisted and otherwise follows the OS `prefers-color-scheme` hint.
