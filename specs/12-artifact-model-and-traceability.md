# 12 — Artifact Model and Traceability

Everything FORGE produces is a node in the **Project Graph** — a single-rooted
tree persisted in SQLite (`.forge/forge.db`). The graph, plus the workspace
directory on disk, is the *entire* persistent state of a build. This spec
defines the node types, the rules that bind them together, and the
traceability guarantees an auditor can rely on.

## Node types and layers

16 node types across 9 abstraction layers:

| Layer | Type | Role |
|-------|------|------|
| 0 | **PROJECT** | Root. Exactly one per workspace; the only parentless node. |
| 1 | **DOCUMENT** | The source specification (`forge.md`). |
| 1 | **PARA** | Addressable paragraph/section within a document. |
| 2 | **HLR** | High-level requirement derived from a paragraph. |
| 2 | **LLR** | Low-level requirement derived from an HLR. |
| 3 | **ARCHITECTURE** | System decomposition rationale. |
| 4 | **MODULE** | Major component; owns a set of HLRs and one CONTRACT. |
| 4 | **CONTRACT** | Interface specification of a module. |
| 5 | **DESIGN** | Design spec for an implementation unit. |
| 5 | **CODE** | Workspace source-file reference linked to a DESIGN. |
| 6 | **SUITE** | Test strategy document. |
| 6 | **CASE_HLR** / **CASE_LLR** | Test case verifying an HLR / LLR. |
| 6 | **TEST** | Workspace test-file reference linked to a CASE. |
| 7 | **RESULT** | Test execution outcome, one per test function. Stable identity; replaced in place on re-record, never duplicated. |
| 8 | **RECORD** | Assurance record (review, baseline, problem, change). |

## Parent rules

Every node except PROJECT has exactly one structural parent (`parent_id`):

| Type | Parent | Type | Parent |
|------|--------|------|--------|
| DOCUMENT | PROJECT | DESIGN | MODULE |
| PARA | DOCUMENT or PARA | CODE | DESIGN |
| HLR | PARA | SUITE | PROJECT |
| LLR | HLR | CASE_HLR / CASE_LLR | SUITE |
| ARCHITECTURE | PROJECT | TEST | CASE_HLR or CASE_LLR |
| MODULE | ARCHITECTURE | RESULT | TEST |
| CONTRACT | MODULE | RECORD | any node |

Writes that would violate a parent rule are rejected at the tool boundary —
an agent cannot create a mis-parented node.

## The five trace pairs

Cross-branch semantic references use `trace_to`. Exactly five relationships
are permitted; no others exist in a valid graph:

| Source | Target | Meaning |
|--------|--------|---------|
| ARCHITECTURE | HLR (1+) | The architecture addresses these requirements |
| MODULE | HLR (1+) | This module owns these requirements |
| DESIGN | LLR (1+) | This design implements these requirements |
| CASE_HLR | HLR | This test case verifies this requirement |
| CASE_LLR | LLR | This test case verifies this requirement |

CASE `trace_to` membership is validated at write time: a CASE_HLR may only
trace to HLRs, a CASE_LLR only to LLRs, and neither may trace to nothing.

## Node IDs

IDs are `{TYPE}-{seq:04d}` (e.g. `HLR-0007`, `CASE_LLR-0042`). Counters are
per-type, monotonically increasing, and **never reused** — a deleted node's ID
never reappears, so audit references stay stable forever.

## The bidirectional traceability chain

The trace pairs plus the parent chain give a continuous, walkable path in
both directions between the source specification and the test evidence:

```
forge.md paragraph (PARA)
  → HLR → LLR                       (requirements)
  → MODULE / CONTRACT → DESIGN      (architecture, via trace_to)
  → CODE file, @traces(LLR-…)       (implementation)
  → CASE → TEST → RESULT            (verification evidence)
```

Concretely, an auditor can answer both directions of every question:

- **Forward**: for any paragraph of the whitepaper — which requirements did
  it produce, which module owns them, which functions implement them
  (every function in `src/` carries a `@traces(LLR-…)` decorator), and which
  passing test results verify them.
- **Backward**: for any line of generated code or any test result — which
  LLR justified it, which HLR that LLR refines, and which paragraph of
  `forge.md` that HLR came from.

Phase 12 accepts the codebase only when this chain is closed at 100% (see
[03-build-pipeline.md](03-build-pipeline.md)): every LLR implemented and
covered by passing test evidence, every function traced, no untraced code.

## Change propagation (staleness)

Every derived node records the hash of the parent content it was authored
against. When a parent's *content* changes, descendants whose recorded hash
no longer matches are flagged stale (`STALE_NODE`) and repaired by the
pipeline. Guarantees:

- **Only content changes propagate.** Metadata, title, or trace edits to a
  parent never cascade staleness onto children.
- **CONTRACT changes** additionally stale all sibling DESIGNs in the module.
- Propagation stops at RESULT nodes; workspace-reference types (CODE, TEST,
  RESULT) are exempt — their validity is governed by the workspace-sync
  checks instead.
- A "reviewed, no change needed" verdict is recorded without rewriting
  content, so review never triggers a false change cascade.

## Deletion and history

Deletion is **soft**: a deleted node and its descendants are retained in
history, never hard-deleted. Node rewrites are versioned (`pg_node_history`),
which is what makes post-run waste analysis and undo possible.

## Resume from any phase

The persistence contract: **the graph DB plus the workspace directory are the
entire state**. A new FORGE process pointed at the same pair continues
exactly where the previous one stopped — no state loss, no duplicate nodes,
no phase resets. Corollaries:

- Re-running an already-complete phase is a no-op (the Gap Analyser finds
  nothing to do).
- A crash mid-phase loses no committed work; the next run re-detects the
  remaining gaps and finishes them.
- In-memory caches (quality verdicts, dedup verdicts) are rebuilt on
  restart; the worst case is one re-judging sweep, never wrong output.
