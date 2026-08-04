# 06 — Per-Phase Dashboard

Route: `/phase/:phaseNum`. A dedicated view for a single phase, reachable from
the sidebar's phase strip.

## Purpose

The phase dashboard is the user's focused workspace for reviewing and
re-driving a specific phase. It complements the Command Centre (which is
phase-agnostic and live) by exposing phase-scoped artefacts and actions.

## What it shows

- **Summary header** — phase number, name, completion status, and the node
  types this phase creates (e.g. Phase 3 → HLR).
- **Artefact list** — every node produced by this phase, with title, status,
  and a link into the Graph Inspector.
- **Gap list** — open gaps this phase still owns, ordered by priority. Empty
  when the phase is complete.
- **Phase actions** — re-run the phase; re-trigger the phase auditor.
- **Phase auditor result** — the last audit's summary: blocking gaps found /
  zero / last run timestamp.

## Phase 0 and Phase 1

Because Phase 0 (Create Project) and Phase 1 (Ingest Document) are
deterministic and single-step, their dashboards show the PROJECT and DOCUMENT
nodes respectively and expose a re-ingest / re-create action. No agent
artefacts appear.

## Phase 14

The Phase 14 dashboard exposes a **Download Deliverables** action once the
phase is complete. See [09-deliverables.md](09-deliverables.md).

## Re-run behaviour

Re-running a phase is idempotent: if no gaps are open, the action is a no-op
and the user sees a "no work to do" indicator. The re-run respects the same
Observe-Act loop as a normal Play.
