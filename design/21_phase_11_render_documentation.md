# Phase 11 — Render Documentation

## Overview

Phase 11 is a **deterministic template render**. No LLM is involved. It
reads every graph node produced by Phases 3-10 and renders one Markdown
file per phase into `[workspace]/docs/`. These rendered documents become
the primary context the mission agent reads during Phase 12 code
generation.

**Handler:** `_run_dashboard_phase` in `backend/pipeline/flow.py`
**Renderer:** `render_dashboard` in `backend/rendering/dashboard.py`

---

## Rendered Files

| File | Source Phase | Content |
|------|-------------|---------|
| `03-HLR.md` | Phase 3 | All HLR nodes with titles, content, trace links |
| `04-Architecture.md` | Phase 4 | ARCHITECTURE node(s) with full rationale |
| `05-Modules.md` | Phase 5 | MODULE nodes with parent links |
| `06-Contracts.md` | Phase 6 | CONTRACT nodes (public interface specifications) |
| `07-LLR.md` | Phase 7 | LLR nodes with inlined HLR requirement text |
| `08-Design.md` | Phase 8 | DESIGN specs with inlined LLRs and CONTRACT interfaces |
| `09-Test-Suite.md` | Phase 9 | SUITE test strategy |
| `10-Verification.md` | Phase 10 | CASE nodes with inlined requirement text |

All files are written to `[workspace]/docs/`.

---

## Context-Rich Rendering

Each node section is **self-contained**. Traced requirements are inlined
with their full text rather than bare IDs. This eliminates the need for
the Phase 12 agent to cross-reference nodes:

- **LLR nodes** inline the HLR text they trace to, so the agent sees the
  high-level intent alongside the detailed requirement.
- **DESIGN nodes** inline the full LLR text they implement, plus sibling
  CONTRACT nodes (the public interface specification). The agent sees
  what to build, what requirement it satisfies, and what API it must
  conform to — all in one section.
- **CASE nodes** inline the full requirement text they verify, so the
  agent can write a test that checks the actual requirement, not just a
  test ID.

Low-value metadata (lifecycle, version, type) is omitted. Every token
in the rendered docs should help the LLM write correct code.

---

## Why Phase 11 Exists

Phase 12 (code generation) uses a single mission agent with a finite
context window. Pre-rendering the graph into structured Markdown:

1. **Reduces tool calls** — the agent reads a few docs instead of
   querying dozens of individual graph nodes.
2. **Provides structure** — headings, tables, and inlined traces give
   the agent a coherent narrative rather than raw node data.
3. **Is deterministic** — no LLM variability. Same graph always produces
   identical docs.
4. **Is idempotent** — re-running Phase 11 overwrites docs with the same
   content if the graph has not changed.

---

## Node Types

Phase 11 does not create new graph nodes. It reads existing nodes of
these types:

| Node Type | Layer | Read From |
|-----------|-------|-----------|
| HLR | 1 | Phase 3 |
| ARCHITECTURE | 2 | Phase 4 |
| MODULE | 2 | Phase 5 |
| CONTRACT | 3 | Phase 6 |
| LLR | 3 | Phase 7 |
| DESIGN | 4 | Phase 8 |
| SUITE | — | Phase 9 |
| CASE_HLR, CASE_LLR | 4 | Phase 10 |

---

## Dashboard: DocoRenderPanel

When Phase 11 is selected in the frontend, the right panel renders
`DocoRenderPanel.tsx`. This component:

- Lists all 8 rendered doc files as expandable rows.
- Expanding a row shows an inline Markdown preview of the document
  content.
- Each row shows the file name and a checkmark indicating the file
  exists on disk.
- The panel fetches doc content via the existing workspace file API
  (`GET /api/workspace/file`).

---

## Pipeline Position

```
Phase 10: Verification (CASE nodes)
    |
    v
Phase 11: Render Documentation (deterministic)
    |  Reads: HLR, ARCHITECTURE, MODULE, CONTRACT, LLR, DESIGN, SUITE, CASE
    |  Writes: [workspace]/docs/*.md
    |
    v
Phase 12: Code Generation (mission agent reads these docs)
```

---

## Deterministic Guarantee

Phase 11 makes zero LLM calls. Given the same graph state, it always
produces identical output. This means:

- Re-running Phase 11 is instant and safe.
- Output is auditable — every line traces to a graph node.
- No API keys or network access required.
