# 03 — The Build Pipeline (Phases 0–14)

FORGE drives the build as a strict, numbered sequence of phases. A phase
cannot begin until every blocking gap from all prior phases is closed. The
user sees phase progress in the sidebar strip on every screen, and can open a
dedicated dashboard per phase via `/phase/:phaseNum`.

## The phases

| # | Name                  | Driver          | Produces (user-visible)                   |
|---|-----------------------|-----------------|-------------------------------------------|
| 0 | Create Project        | Human + backend | PROJECT node                              |
| 1 | Ingest Document       | Backend only    | DOCUMENT node from `forge.md`             |
| 2 | Parse Document        | Agent           | PARA tree (paragraph nodes)               |
| 3 | Derive HLRs           | Agent           | High-level requirements                   |
| 4 | Create Architecture   | Agent           | Architecture decisions                    |
| 5 | Assign Modules        | Agent           | Module decomposition                      |
| 6 | Write Contracts       | Agent           | Public interfaces                         |
| 7 | Derive LLRs           | Agent           | Low-level requirements                    |
| 8 | Create Designs        | Agent           | DESIGN specs per module                   |
| 9 | Write Test Strategy   | Agent           | Test strategy nodes                       |
|10 | Write Test Cases      | Agent           | Test case nodes                           |
|11 | Render Documentation  | Deterministic   | Structured docs rendered from the graph   |
|12 | Generate Code         | Mission agent   | `src/`, `tests/` files, passing tests     |
|13 | Workspace Sync        | Deterministic   | Graph ↔ workspace reconciliation          |
|14 | Build Deliverables    | Deterministic   | `deliverables.zip`                        |

Phases 0, 1, 11, 13, and 14 are deterministic — no LLM. All other phases
dispatch one or more agents over the Observe-Act loop until their gap type is
resolved.

## Human approval gates

Phase transitions are gated. When a phase completes, the loop pauses and the
user sees a phase-complete indicator in the sidebar. The user presses Play
again to advance into the next phase. This gives the user a review point
before committing LLM time to the next stage.

## Idempotency and re-runs

Each phase is safe to re-run on a complete graph: the Gap Analyser finds zero
gaps and the phase exits immediately. The user can re-run any phase from its
dashboard without corrupting prior work.

## Phase auditor

After each phase, a deterministic auditor re-runs the Gap Analyser to confirm
all blocking gaps are resolved. If the auditor finds residual gaps, the phase
is not marked complete and the loop continues working on them.

## Traceability invariant (Phase 12)

The generated codebase is accepted only when all four of these hold
simultaneously:

1. **Statement coverage = 100%** — every source line is exercised by a
   passing test.
2. **MC/DC coverage = 100%** — every boolean sub-condition has
   independently affected the outcome.
3. **Every LLR is traced** — every requirement has at least one passing
   test carrying a matching `@traces(LLR-…)` annotation.
4. **Every function is traced** — every function in `src/`, including
   `__init__`, other dunders, and private helpers, carries
   `@traces(LLR-…)`.

These are a joint invariant, not four independent thresholds. A
function that contributes to none of them is excess code; FORGE
removes it (inlines into the caller or deletes it) rather than
retaining untraced implementation details. The user can rely on the
generated workspace being the **minimal** code that satisfies the
requirement graph, with every line auditable back to an LLR.

## What the user controls

- **Play / Pause** — see [04-loop-control.md](04-loop-control.md).
- **Re-run a phase** — from the phase dashboard.
- **Human approval at phase gates** — by pressing Play to advance.

## What the user does not control directly

- Which agent runs next within a phase — driven by gap priority.
- The order of gap resolution — driven by the Gap Analyser.
