# Phase 13 — Workspace Sync

## Overview

Phase 13 is a **deterministic file scan**. No LLM is involved. It reads
every file in the workspace, matches each to its source graph node, parses
`@traces` decorators, and creates or updates CODE and TEST nodes in the
project graph. This closes the full traceability chain from requirements
through to test evidence.

**Pipeline steps:** `workspace_sync`, then `record_results_step`

---

## What Phase 13 Does

For each generated file in the workspace:

1. **Match to source node** — match the file to its parent DESIGN or CASE
   node by slugified name. `src/motion_planner.py` matches DESIGN node
   "Motion Planner"; `tests/test_motion_planner.py` matches the
   corresponding CASE node.

2. **Parse `@traces` decorators** — use Python AST to inspect every
   function and method definition. Extract `LineTrace` records containing
   start line, end line, LLR IDs, symbol name, and CASE IDs.

3. **Store trace data** — write `line_traces` (list of `LineTrace`
   records) and `file_path` into the node's `properties`.

4. **Create/update CODE nodes** — for each source file in `src/`, create
   a CODE node (Layer 5) as a child of the matched DESIGN node. If the
   CODE node already exists, update its properties.

5. **Create/update TEST nodes** — for each test file in `tests/`, create
   a TEST node (Layer 6) as a child of the matched CASE node.

6. **Record RESULT nodes** — a second pipeline step
   (`record_results_step`) that runs strictly **after** TEST sync:

   1. **Heal parentage** — any existing RESULT node whose parent is not
      a TEST node (e.g. CASE-parented RESULTs written by a pre-fix build
      being resumed) is re-parented onto the TEST node resolved from its
      `file_path`/`function_name` properties, and the TEST id is merged
      into its `trace_to`. Healing is deterministic (no test run needed)
      so a resumed Phase 13 repairs an existing bad graph before any new
      evidence is recorded.
   2. **Record** — run the test suite via bazel and create one RESULT
      node per test function with status `passed`, `failed`, `skipped`,
      or `error`. Evidence is always parsed fresh from this run — never
      cached from Phase 12 — so RESULTs describe the tidied workspace.

   A RESULT's only valid parent is a TEST node, and an invalid RESULT is
   never written. Two distinct no-parent situations exist:

   - **Auxiliary test file** — no CASE node owns the file at all (e.g. an
     import-sanity or re-export test the mission agent added as
     infrastructure). Such files are exercised by the test run but are
     **not traceability evidence**: recording skips them with a WARN log
     naming each skipped file, and reports the skip count. This is an
     explicit, documented category — not a fallback.
   - **Owned file, unresolvable TEST** — a CASE owns the file but the
     function is missing from its `line_traces`, or no TEST node traces
     the CASE. Because this step runs after TEST sync, this indicates a
     real sync or traceability bug: recording raises a `RuntimeError`
     and halts the phase loudly.

---

## Gap Types

Phase 13 detects two structural gap types:

| Gap Type | Meaning | Resolution |
|----------|---------|------------|
| `UNSYNCED_DESIGN` | DESIGN node has no CODE child | Scan finds the source file and creates the CODE node link |
| `UNSYNCED_TEST` | CASE node has no TEST child | Scan finds the test file and creates the TEST node link |

These gaps indicate that the graph has not yet been updated to reflect
files written during Phase 12. Phase 13 resolves them by scanning the
workspace and creating the missing node links.

---

## Traceability Chain

Phase 13 closes the full bidirectional traceability chain:

```
Forward (requirements -> implementation):
    HLR -> LLR -> DESIGN -> CODE (source file)

Forward (requirements -> verification):
    CASE -> TEST -> RESULT (test evidence)

Reverse (code -> requirements):
    CODE -> DESIGN -> LLR -> HLR
    RESULT -> TEST -> CASE -> LLR/HLR
```

After Phase 13, every source file in the workspace has a CODE node
that links back through DESIGN and LLR to the original HLR. Every test
file has a TEST node linking through CASE to the requirement it verifies.
Every test function has a RESULT node recording its pass/fail status.

---

## LineTrace Records

The trace parser extracts one `LineTrace` per decorated function:

```python
@dataclass
class LineTrace:
    start: int            # first line of function definition
    end: int              # last line of function body
    llr_ids: list[str]    # ["LLR-0001", "LLR-0003"]
    symbol: str           # function/method name
    case_ids: list[str]   # ["CASE-0042"] (if present)
```

These records are stored in `node.properties.line_traces` as a list of
dicts. The frontend uses them to render coloured gutter bars in the
code trace view and to compute function-level coverage metrics.

---

## Node Types Created

| Node Type | Layer | Parent | Properties |
|-----------|-------|--------|------------|
| CODE | 5 | DESIGN | `file_path`, `line_traces`, `coverage_pct`, `branch_coverage_pct` |
| TEST | 6 | CASE | `file_path`, `line_traces`, `case_ids` |
| RESULT | 7 | TEST | `status` (passed/failed/skipped/error), `test_name`, `duration` |

---

## Pipeline Position

```
Phase 12: Code Generation (mission agent writes files)
    |
    v
Phase 13: Workspace Sync (deterministic)
    |  Reads: workspace files (src/*.py, tests/test_*.py)
    |  Reads: pytest results
    |  Creates: CODE, TEST, RESULT nodes
    |  Resolves: UNSYNCED_DESIGN, UNSYNCED_TEST gaps
    |
    v
Phase 14: Build Deliverables (deterministic packaging)
```

---

## Dashboard

When Phase 13 is selected, the phase dashboard shows CODE and TEST nodes
with their file paths. Each node row displays:

- Node ID and type (CODE or TEST).
- File path linking to the workspace file.
- Number of traced functions.
- Coverage percentage (for CODE nodes).
- Pass/fail status (for TEST nodes, from child RESULT nodes).

The standard NodeTablePanel is used — no bespoke panel is needed for
Phase 13.

---

## Deterministic Guarantee

Phase 13 makes zero LLM calls. Given the same workspace files and graph
state, it always produces identical node updates. This means:

- Re-running Phase 13 is instant and safe.
- Node creation is idempotent — existing nodes are updated, not
  duplicated.
- No API keys or network access required.
