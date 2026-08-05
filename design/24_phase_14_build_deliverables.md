# Phase 14 — Build Deliverables

## Overview

Phase 14 is **deterministic packaging**. No LLM is involved. It reads the
project graph and workspace, renders structured documentation, and bundles
everything into a deliverables ZIP archive. Given the same graph state, it
always produces identical output.

**Handler:** `_run_deliverables_phase` in `backend/crew/flow.py`
**Module:** `backend/rendering/deliverables.py`

---

## ZIP Structure

```
deliverables/
    README.md                              — project overview + navigation
    docs/
        01-Requirements-Specification.md   — HLR + LLR with trace chains
        02-Architecture.md                 — architecture decisions + modules
        03-Interface-Specification.md      — contract (public API) specs
        04-Design-Specification.md         — design specs with traced reqs
        05-Test-Plan.md                    — test strategy + verification cases
        06-Traceability-Matrix.md          — full bidirectional cross-reference
        07-Coverage-Report.md              — coverage stats, gaps, metrics
    src/                                   — generated source code
    tests/                                 — generated test code
    pyproject.toml                         — build configuration
```

---

## Documents

### README.md

Generated from the PROJECT node:
- Project name and description.
- Document manifest: every file in the pack with a one-line description.
- Quick-start: how to install, run tests, and read the docs.
- Generation timestamp.

### 01-Requirements-Specification.md

Two sections:

**High-Level Requirements** — one entry per HLR node: ID, title, full
content, parent PARA reference.

**Low-Level Requirements** — one entry per LLR node: ID, title, full
content, traced HLR(s) with title, parent MODULE.

### 02-Architecture.md

**Architecture Decisions** — one entry per ARCHITECTURE node: ID, title,
full rationale.

**Module Decomposition** — one entry per MODULE node: ID, title,
description, child counts (LLRs, CONTRACTs, DESIGNs).

### 03-Interface-Specification.md

One entry per CONTRACT node: ID, title, full specification, parent MODULE
name, sibling DESIGN nodes that implement against this interface.

### 04-Design-Specification.md

One entry per DESIGN node: ID, title, full design content, traced LLR(s)
with titles, parent MODULE and sibling CONTRACT, linked CODE node file
path (if workspace file exists).

### 05-Test-Plan.md

**Test Strategy** — from SUITE node(s): full strategy content, coverage
targets.

**Verification Cases** — one entry per CASE node: ID, title, full test
specification, traced requirement(s) with titles, linked TEST node file
path, latest RESULT status (pass/fail) if available.

### 06-Traceability-Matrix.md

Bidirectional cross-reference matrix rendered as Markdown tables.

**Forward trace (requirements -> implementation):**

| HLR | LLR | DESIGN | Source File | CASE | Test File | Status |
|-----|-----|--------|-------------|------|-----------|--------|
| HLR-001 | LLR-001 | DESIGN-001 | src/planner.py | CASE_LLR-001 | tests/test_planner.py | PASS |

**Reverse trace (implementation -> requirements):**

| Source File | DESIGN | LLR(s) | HLR(s) |
|-------------|--------|--------|--------|
| src/planner.py | DESIGN-001 | LLR-001, LLR-002 | HLR-001 |

**Orphan detection:**
- LLRs with no DESIGN (unimplemented requirements).
- DESIGNs with no CODE (ungenerated source).
- CASEs with no TEST (ungenerated tests).
- CASEs with failing or missing RESULTs.

### 07-Coverage-Report.md

Coverage summary with metrics:
- **Requirement coverage**: LLRs traced by at least one CASE / total LLRs.
- **Function coverage**: traced functions / total functions.
- **Test pass rate**: passing RESULTs / total RESULTs.
- **Gaps list**: any unresolved gaps from the gap analyser, grouped by type.

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/deliverables/download` | GET | Serve the deliverables ZIP for download |
| `/deliverables/manifest` | GET | Return JSON list of `{path, size, exists}` for the dashboard |

---

## Dashboard: DeliverablesPanel

When Phase 14 is selected, the right panel renders `DeliverablesPanel.tsx`:

```
+--------------------------------------------------+
|  Deliverables Pack                               |
|  Phase 14 — deterministic documentation bundle   |
+--------------------------------------------------+
|  Summary:  7 Docs | 5 Source | 4 Tests | 1 Config|
+--------------------------------------------------+
|  docs/                                           |
|    > 01-Requirements-Specification.md         OK |
|    > 02-Architecture.md                       OK |
|    > 03-Interface-Specification.md            OK |
|    > 04-Design-Specification.md               OK |
|    > 05-Test-Plan.md                          OK |
|    > 06-Traceability-Matrix.md                OK |
|    > 07-Coverage-Report.md                    OK |
|  src/    5 files                                 |
|  tests/  4 files                                 |
|  pyproject.toml                               OK |
+--------------------------------------------------+
|  [ Download All (.zip) ]                         |
+--------------------------------------------------+
```

Features:
- **Expandable doc rows** — same pattern as `DocoRenderPanel.tsx`. Expanding
  a row shows an inline Markdown preview of the document.
- **Summary metrics bar** — total docs, source files, test files, coverage %.
- **Download All button** — fetches `/api/workspace/deliverables/download`.
- **File counts** for `src/`, `tests/`, config sections.

---

## Pipeline Position

```
Phase 13: Workspace Sync (CODE/TEST nodes created)
    |
    v
Phase 14: Build Deliverables (deterministic)
    |  Reads: project graph (all node types)
    |  Reads: workspace files (src/, tests/, pyproject.toml)
    |  Writes: deliverables/ directory + deliverables.zip
    |
    v
Done — ZIP ready for download
```

---

## Deterministic Guarantee

Phase 14 makes zero LLM calls. Given the same graph state, it always
produces identical output. This means:

- Re-running Phase 14 is instant and safe.
- Output is auditable — every line traces to a graph node.
- No API keys or network access required.
