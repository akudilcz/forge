# 09 — Deliverables Bundle

Phase 14 is the user's takeaway: a single ZIP containing the generated
codebase plus a full set of rendered specifications. Phase 14 is deterministic
— given the same graph state, it always produces identical output. No LLM is
involved.

## When it runs

After Phase 13 (Workspace Sync) completes. The Phase 14 dashboard exposes a
**Download Deliverables** action once the phase is marked complete.

## Bundle layout

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

## Rendered documents

Each document is rendered from the graph, not hand-written. Renaming a node,
editing a paragraph, or adding a requirement and re-running Phase 14 produces
an updated document set automatically.

- **README.md** — from the PROJECT node (name + description) plus navigation.
- **01 Requirements Specification** — HLRs grouped by parent paragraph, LLRs
  nested under HLRs, each entry showing its trace chain.
- **06 Traceability Matrix** — bidirectional cross-reference: paragraph → HLR
  → LLR → DESIGN → code file → test case → result.
- **07 Coverage Report** — coverage stats, coverage gaps per module, and
  per-test-case pass/fail derived from the RESULT nodes recorded during
  Phase 13 workspace sync.

## Determinism

Phase 14 performs no LLM calls and no network I/O. Given the same graph
snapshot, two runs produce byte-identical output (modulo file timestamps
inside the ZIP).

## Re-running

Phase 14 is safe to re-run at any time after Phase 13 completes. It overwrites
the previous `deliverables.zip` in place.
