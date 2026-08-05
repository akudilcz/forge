# Phase 12 — Generate Code

## Overview

Phase 12 transforms a validated project graph into a working, fully-traced
codebase. A single **mission agent** — one capable LLM in one continuous
ReAct conversation — receives full graph context up front and uses real
tools to write source files, write tests, run them, fix failures, and
close coverage gaps. The graph acts as **scoreboard**: a deterministic
value function checks completeness and feeds remaining gaps back to the
agent whenever it calls `evaluate_progress`.

### Traceability invariant

Phase 12 is complete only when **all four** of the following hold
simultaneously on the generated workspace:

1. **Statement coverage = 100%** — every source line is exercised by a
   passing test.
2. **MC/DC coverage = 100%** — every boolean sub-condition has
   independently affected the outcome.
3. **Every LLR is traced** — every low-level requirement has at least
   one passing test that carries a matching `@traces(LLR-…)` annotation.
4. **Every function is traced** — every function in `src/` (including
   `__init__`, dunder methods, and private helpers) carries
   `@traces(LLR-…)`.

The four conditions are not independent thresholds to be balanced — they
are a single joint invariant. A function that cannot contribute to at
least one of them is **not required** and must be removed (inlined into
its caller or deleted). A helper that is exercised by a public method
inherits that method's LLR trace; there is no "implementation detail"
exemption.

The minimum-across-dimensions value function (below) enforces the joint
nature directly: the score is `1.0` only when every dimension is at
100%.

This approach mirrors how a developer works with Claude Code: full
context, real tools, tight feedback loops, and the freedom to decide the
order of work. There is no planner, no per-gap dispatch, no triage step.
The agent accumulates understanding across the entire session.

The pipeline is **idempotent** — re-running Phase 12 on a complete
workspace finds zero gaps and exits immediately.

**Implementation:** `backend/codegen/mission_agent.py`

---

## Pipeline

```
Phase 12: Code Gen (mission agent)
    1. Init workspace (dirs, tracing decorator, Bazel scaffold)
    2. Remove broken files (syntax errors, dangling imports)
    3. Assemble full context from graph
    4. Mission agent: single continuous ReAct conversation
    5. Post-agent: tidy-up (deterministic cleanup)
    6. Persist line traces to graph nodes
    7. Trace audit (LLM verifies completeness)
    8. Record test RESULT nodes + coverage metrics
```

### Step 1 — Init Workspace

Creates the workspace directory structure if it does not exist:

- `src/`, `tests/`, `docs/`, `tracing/`, `.forge/`
- `MODULE.bazel`, `.bazelrc`, `requirements.txt`, `BUILD.bazel`
- Seeds the `tracing/` package (the `@traces` decorator source)
- Generates `BUILD.bazel` files for `src/` and `tests/`

### Step 2 — Remove Broken Files

Scans existing test files for syntax errors (`ast.parse()`) and dangling
workspace imports. A file is removed only when it fails to parse or when
it imports a `src.*` module that no longer exists in the workspace.
Imports are enumerated with `ast.parse` (never regex), so import-shaped
lines inside docstrings are ignored. Unknown third-party roots are
**never** grounds for deletion — they surface later as `TEST_ENV_BROKEN`
gaps instead. Stdlib recognition uses the single shared constant in
`backend/codegen/known_modules.py` (`sys.stdlib_module_names` plus the
workspace-internal modules `src`/`tests`/`tracing`/`conftest`), which is
also the allowlist used by `bazel_gen` and `build_env` — one list, no
drift.

### Step 3 — Assemble Context

Builds the initial message for the mission agent. See
[Context Pre-Loading](#context-pre-loading) below.

### Step 4 — Mission Agent

The core of Phase 12. See [Mission Agent Architecture](#mission-agent-architecture).

### Steps 5-9 — Post-Agent

All post-agent steps except the trace audit are deterministic (no LLM):

1. **Tidy-up** — remove `__pycache__`/`.pyc`, ensure `src/__init__.py`.
2. **Persist traces** — parse `@traces` decorators, store `LineTrace`
   data and coverage stats in node properties, clear stale props from
   nodes no longer in the result set.
3. **Trace audit** (LLM) — verify trace completeness per file, suggest
   missing traces, persist results as `trace_audit` in node properties.
4. **Record RESULT nodes** — one RESULT per test function with status
   (`passed`/`failed`/`skipped`/`error`), closing the traceability
   chain: `HLR -> LLR -> DESIGN -> CODE` and `CASE -> TEST -> RESULT`.

#### Fresh test evidence guarantee

Every bazel invocation made by the workspace scanner or the result
recorder must produce evidence describing the *current* workspace
revision. Three rules enforce this:

1. **BUILD regeneration** — `init_bazel_workspace(workspace)`
   (idempotent) is called before every `bazel test` run, so tests
   written via file tools between runs always have bazel targets.
2. **Artifact purge** — before each run, leftover
   `bazel-testlogs/**/test.xml`, `coverage.lcov`,
   `coverage-test-results.xml`, and the bazel LCOV report are deleted
   (`purge_stale_test_artifacts`). Bazel leaves prior-run `test.xml`
   files for targets that later fail to build; without the purge those
   parse as current results.
3. **Loud failures** — a nonzero bazel exit with no freshly produced
   XML is a `test_run_error` (scanner) or a raised `RuntimeError`
   (result recorder), never an empty result list. Coverage measurement
   never falls back to a stale on-disk LCOV: a missing `coverage`
   binary or a failed LCOV export raises instead of degrading.

There is deliberately no test-deduplication step. Earlier pipelines
removed tests that shared an LLR set under the theory they were
redundant, but LLR identity does not imply coverage identity (MC/DC
tests all trace to the same LLR while exercising distinct branches).
Removing any one of them drops statement or branch coverage below 100%,
breaking the traceability invariant. All passing tests stay.

Statement and branch coverage percentages are persisted on the DESIGN
node for the web UI.

---

## Mission Agent Architecture

Phase 12 uses a **single long-lived mission agent** — one capable LLM in
one continuous conversation with real tools and graph-derived feedback.
This replaces a fragmented pipeline (separate planner, per-slice agents,
LLM triage) with one agent that handles the entire code generation
session.

### Why One Agent Instead of Many

When a developer uses Claude Code to build a module, they do not fragment
the work into separate "source writer", "test writer", and "bug fixer"
agents. One agent:

- Writes a function, runs the test, sees it fail, fixes it.
- Remembers what it already tried — no context lost between calls.
- Decides its own workflow — when to test, when to refactor, what order.

The graph's role changes from **project manager** (prescribing workflow)
to **scoreboard** (checking completeness). The agent works freely; the
graph evaluates results.

### Execution Model

```
Mission agent receives: full graph context + initial gaps
    |
    v
Agent works continuously:
    - Writes source files (src/*.py)
    - Writes test files (tests/test_*.py)
    - Runs tests via shell_exec (bazel test)
    - Reads output, fixes failures
    - Calls evaluate_progress for gap feedback
    - Calls check_trace_quality for per-function verdicts
    - Keeps going until all gaps closed or agent stops
    |
    v
Post-agent: tidy-up, dedup, persist, audit
```

There is no outer round loop. The agent runs as a single continuous
LangGraph invocation with a recursion limit of 200 tool calls. The agent
uses `evaluate_progress` to check its own score whenever it wants.

### Persistent Conversation (LangGraph Checkpointing)

The agent uses `MemorySaver` checkpointing with a unique `thread_id`.
This means the agent retains full history of what it wrote, what failed,
and why. With a large context window, the agent maintains understanding
across the entire session without context resets.

### Termination

| Condition | Outcome |
|-----------|---------|
| Zero gaps | Phase 12 complete (all quality gates met) |
| Agent stops itself | Phase 12 complete, remaining gaps logged |
| Recursion limit (200 tool calls) | Phase 12 complete with warnings |

There is no stall detection or round counting. The agent runs
continuously until it finishes or hits the recursion limit.

---

## Context Pre-Loading

The initial message pre-loads everything the agent needs to avoid
wasting tool calls discovering what to build:

- **All DESIGN specs** with LLR traces — what to implement.
- **All MODULE nodes** with CONTRACT children — public API interfaces.
- **All HLR and LLR nodes** — the full requirement hierarchy.
- **All CASE_HLR/CASE_LLR nodes** — test acceptance criteria.
- **Rendered docs** from Phase 11 (07-LLR.md, 08-Design.md,
  06-Contracts.md) — structured context.
- **Tracing decorator source** — so the agent knows the `@traces` API.
- **Contents of any existing workspace files** — for re-run scenarios
  where partial work already exists.

Context assembly is in `build_mission_context()`
(`backend/codegen/mission_context.py`, re-exported by
`mission_agent.py`).

---

## Tool Set

| Tool | Purpose |
|------|---------|
| `file_write` | Create or overwrite files in the workspace |
| `multi_file_write` | Write several files in one call (validated as a batch) |
| `file_read` | Read files with line numbers |
| `file_patch` | Targeted edits to existing files |
| `shell_exec` | Run `bazel test`, `bazel coverage`, shell commands |
| `list_dir` / `list_files` | Explore workspace directory structure |
| `read_docs` | Read rendered docs from Phase 11 |
| `python_lint` | Check syntax without running (fast feedback) |
| `workspace_doctor` | Diagnose persistent build issues |
| `evaluate_progress` | Run all tests + full coverage analysis, return gaps |
| `check_trace_quality` | Per-function trace quality verdicts on a source file |

All write tools (`file_write`, `multi_file_write`, `file_patch`) enforce
the same guarantees, implemented in `backend/tools/write_validation.py`:

- **Workspace containment** — the resolved target path must lie inside
  the workspace; a path that escapes (via `..` or an absolute path)
  raises a loud error and nothing is written.
- **Python syntax gate** — `.py` content must pass `ast.parse` before it
  is persisted. `file_write` rejects the write; `file_patch` never
  persists a post-patch `.py` that fails to parse (the parse error, with
  line number, is returned instead); `multi_file_write` validates every
  entry first — required keys present, path contained, syntax valid —
  and rejects the whole batch atomically on any violation, so a bad
  entry can never truncate or partially write files.
- Non-Python files skip the syntax gate.

The agent calls tools freely. `evaluate_progress` is the primary
feedback mechanism — it runs pytest, computes all coverage dimensions,
identifies gaps, and returns a structured report with a numeric score.

**Required tools.** `file_write`, `shell_exec`, and `evaluate_progress`
are mandatory: `create_mission_agent` raises `RuntimeError` if the
filtered tool set lacks any of them. Tool registration is never
silently optional — both entry points (the server's `lifespan.py` and
`ForgeBuilder`, used by e2e/integration runs) must register
`evaluate_progress`, `check_trace_quality`, and `workspace_doctor` in
addition to the file/shell tools. `run_code_gen` takes `config` and
`tool_instances` as required arguments (no defaults), and
`ForgeFlow._get_tool_instances` raises rather than returning an empty
list when the registry is unavailable.

---

## Value Function

The value function is deterministic (no LLM). It computes the **minimum**
across all coverage dimensions:

```python
value = min(test_pass_rate, trace_coverage, decorator_coverage,
            statement_coverage, mcdc_coverage)
```

Where:

| Metric | Definition |
|--------|------------|
| `test_pass_rate` | tests_passed / tests_total |
| `trace_coverage` | traced_llrs / total_llrs |
| `decorator_coverage` | traced_functions / total_functions |
| `statement_coverage` | coverage_pct / 100 |
| `mcdc_coverage` | branch_coverage_pct / 100 |

Using `min()` means the score only reaches 1.0 when **all** dimensions
hit 100%. A single lagging dimension holds the score down, focusing the
agent on its weakest area. This prevents the agent from inflating the
score by over-investing in one dimension while ignoring another.

---

## Gap Taxonomy

Code generation gaps have their own taxonomy, distinct from the
structural and quality gaps used in Phases 2-10. Gaps are detected by
deterministic workspace scanning (AST parsing, pytest results, coverage
reports) and fed back to the mission agent.

**Implementation:** `backend/codegen/gap_finder.py` (facade; the
`Gap`/`GapKind` model lives in `gap_model.py`, failing-test clustering in
`failure_clustering.py`, and LLR coverage checks in
`requirement_coverage.py`)

### GapKind Enum (Priority Order)

| Priority | Kind | Detection | Meaning |
|----------|------|-----------|---------|
| 0 | `TEST_ENV_BROKEN` | Tests cannot run (import errors, missing deps) | Environment blocks all verification |
| 1 | `SYNTAX_ERROR` | `ast.parse()` fails on a `.py` file | File cannot be imported or tested |
| 2 | `MISSING_SOURCE` | DESIGN node with no file on disk | Agent has not written source yet |
| 3 | `MISSING_TEST` | CASE node with no test file on disk | Agent has not written tests yet |
| 4 | `FAILING_TESTS` | `bazel test` reports failure | Tests do not pass |
| 5 | `INVALID_TRACES` | `@traces` references non-existent LLR ID | Trace annotation is wrong |
| 6 | `UNTRACED_FUNCTIONS` | Public function without `@traces` | Function coverage gap |
| 7 | `LOW_STRUCTURAL_COVERAGE` | `bazel coverage` < 100% for a file | Statement coverage gap |
| 8 | `LOW_BRANCH_COVERAGE` | MC/DC branch coverage < 100% | DO-178C certification blocker |
| 9 | `UNIMPLEMENTED_REQUIREMENT` | LLR absent from all source-file `@traces` | No implementing code exists |
| 10 | `UNCOVERED_REQUIREMENT` | LLR has no passing test evidence | Requirement coverage gap |
| 11 | `WEAK_TRACE` | Function traces to LLR but does not implement it | Misleading trace attribution |
| 12 | `SCOPE_CREEP` | Function not backed by any requirement | Unrequired code |

The ordering reflects dependency: higher-priority gaps block meaningful
verification of lower-priority ones. Environment and syntax issues (0-1)
must be fixed before tests can run. Files must exist (2-3) before they
can be tested. Tests must pass (4) before coverage is meaningful. Trace
validity (5-6) must be correct before coverage metrics are trustworthy.
Coverage dimensions (7-10) are measured per file and per requirement.
Semantic quality checks (11-12) only run once all structural gaps close.

### Quality Gate Sequencing

The `WEAK_TRACE` and `SCOPE_CREEP` gap kinds represent a semantic
quality gate that runs after all structural/coverage gaps close. The
agent can call `check_trace_quality(file_path)` on any source file to
get per-function verdicts:

- **PASS** — function implements the traced LLR.
- **WEAK_TRACE** — function references a valid LLR but does not
  meaningfully implement it.
- **SCOPE_CREEP** — function performs work not described by any
  requirement.

`SCOPE_CREEP` is also detected by a deterministic rule-based check:
function names containing patterns like "fallback", "retry", "cache"
are flagged if no requirement mentions that concept.

---

## Coverage Model

Phase 12 measures four distinct coverage types, all gated at 100%:

| Type | Question | Gap Kind | Measurement |
|------|----------|----------|-------------|
| **Function** | Does every function declare its requirement? | `UNTRACED_FUNCTIONS` | traced_functions / total_functions per file |
| **Structural** | Is every source line exercised? | `LOW_STRUCTURAL_COVERAGE` | `bazel coverage` LCOV per file |
| **Requirement** | Does every LLR have implementing code AND a passing test? | `UNIMPLEMENTED_REQUIREMENT` / `UNCOVERED_REQUIREMENT` | `@traces` in source functions AND `@traces` in passing tests vs all LLR nodes |
| **MC/DC** | Has every boolean sub-condition independently affected the outcome? | `LOW_BRANCH_COVERAGE` | `bazel coverage` branch instrumentation |

Function coverage exempts Protocol stubs and abstract methods, but not
private functions — every function in `src/` must have `@traces`.

**Single coverage definition.** An LLR is *covered* iff both legs hold:
at least one source function carries `@traces` citing it, AND at least
one passing test function carries `@traces` citing it. `find_gaps`
(`UNIMPLEMENTED_REQUIREMENT` + `UNCOVERED_REQUIREMENT`), the value
function's `trace_coverage`, and the phase-12 coverage gate
(`compute_requirement_coverage_detail` / `_enforce_coverage_gate`) all
use this same definition — an LLR with passing test evidence but no
implementing source `@traces` fails the gate loudly.

---

## Quality Gates

| Gate | Threshold | Enforcement |
|------|-----------|-------------|
| Function coverage | 100% — every function annotated | Gap: `UNTRACED_FUNCTIONS` |
| Trace validity | All `@traces` LLR IDs exist in graph | Gap: `INVALID_TRACES` |
| Test pass rate | 100% | Gap: `FAILING_TESTS` |
| Test environment | Tests can run | Gap: `TEST_ENV_BROKEN` |
| Statement coverage | 100% per file | Gap: `LOW_STRUCTURAL_COVERAGE` |
| MC/DC coverage | 100% branch coverage | Gap: `LOW_BRANCH_COVERAGE` |
| Requirement implementation | Every LLR cited by a source `@traces` | Gap: `UNIMPLEMENTED_REQUIREMENT` |
| Requirement coverage | Every LLR has passing test | Gap: `UNCOVERED_REQUIREMENT` |
| Trace quality | Every function implements its traced LLR | Gap: `WEAK_TRACE` |
| Scope integrity | No functions beyond specification | Gap: `SCOPE_CREEP` |

---

## Line-Level LLR Traceability

### The `@traces` Decorator

Every function in generated source code uses the `@traces` decorator
from the tracing package (seeded into the workspace at init):

```python
@traces("LLR-0003")                              # source function
def validate_token(self, token: str) -> bool: ...

@traces("LLR-0001", "LLR-0002")                  # multiple LLRs
def authenticate(self, user, password): ...

@traces("LLR-0003", case="CASE-0042")            # test function
def test_validate_token_rejects_empty(): ...
```

### No exemptions

Every function in `src/` must have `@traces` — this is the fourth leg
of the traceability invariant. That includes:

- `__init__` and other dunder methods (`__repr__`, `__eq__`, etc.).
- Private helpers (`_foo`).
- Nested and inner functions.

A helper inherits the LLR(s) of the public method that calls it. If a
function cannot be traced to any LLR, it is not required; inline it
into the caller or delete it. There is no "implementation detail"
exemption — "implementation detail of LLR-X" just means "traces
LLR-X." This satisfies DO-178C: every line of code maps to a
requirement via an explicit, auditable trace.

### LineTrace Dataclass

The trace parser (`backend/workspace/trace_parser.py`) uses Python AST to
extract `LineTrace` records from every decorated function:

```python
@dataclass
class LineTrace:
    start: int            # first line of function definition
    end: int              # last line of function body
    llr_ids: list[str]    # ["LLR-0001", "LLR-0003"]
    symbol: str           # function/method name
    case_ids: list[str]   # ["CASE-0042"] (if present)
```

These are stored in `GeneratedFile.line_traces` and later persisted to
CODE/TEST node `properties.line_traces` in the graph (Phase 13).

---

## Workspace Layout

```
[workspace]/
    MODULE.bazel      — bazel module definition
    .bazelrc          — bazel configuration
    requirements.txt  — pip dependencies for rules_python
    BUILD.bazel       — root build file
    src/
        BUILD.bazel   — py_library targets
        *.py          — generated source files (one per DESIGN)
    tests/
        BUILD.bazel   — py_test targets
        conftest.py   — test infrastructure (do not delete)
        test_*.py     — generated test files (one per CASE)
    docs/             — rendered documentation (Phase 11)
    tracing/          — tracing decorator package (seeded from backend)
    .forge/           — context files and internal config
```

### Build System — Bazel

Generated workspaces use **Bazel** for hermetic, reproducible builds.
This eliminates environment isolation issues — `bazel test` runs in a
sandbox with explicit dependencies.

- **Source targets**: each `.py` in `src/` gets a `py_library` target.
- **Test targets**: each `test_*.py` in `tests/` gets a `py_test` target.
- **Dependencies**: managed via `requirements.txt` + `rules_python`.
- **Test execution**: `bazel test //tests/...`
- **Coverage**: `bazel coverage //tests/...`

BUILD files are generated deterministically by the orchestrator.

### Target Paths

Source files: `src/<slugified_title>.py`
Test files: `tests/test_<slugified_title>.py`

Slugification: lowercase, strip trailing "design"/"implementation"/"spec",
replace non-alphanumeric chars with underscores.

---

## Node Types

Phase 12 creates two node types in the project graph:

| Node Type | Layer | Parent | Description |
|-----------|-------|--------|-------------|
| CODE | 5 | DESIGN | Represents a generated source file |
| TEST | 6 | CASE | Represents a generated test file |

CODE nodes store `file_path`, `line_traces`, and coverage metrics.
TEST nodes store `file_path`, `line_traces`, and linked CASE IDs.
RESULT nodes (one per test function) are children of TEST nodes with
status: `passed`, `failed`, `skipped`, or `error`. RESULT node IDs are
`RESULT-{slug[:60]}-{sha256(test_id)[:8]}` — the truncated slug keeps
IDs readable while the hash suffix keeps long test IDs sharing a
60-char prefix collision-free (the graph stores nodes with
`INSERT OR REPLACE`, so an ID collision would silently overwrite
evidence).

---

## Dashboard: CodeGenPanel

When Phase 12 is selected, the right panel renders `CodeGenPanel.tsx`
with a stats bar and two-column body.

### Stats Bar

```
[ACTIVE]  5 src  4 test  |  Fn 23/25  Stmt 92%  Req 18/20  MC/DC 71%  2 gaps  [Run Phase]
```

Four coverage metrics inline with phase status:

| Label | Type | Derivation |
|-------|------|------------|
| **Fn** | Function | traced_functions / total_functions |
| **Stmt** | Structural | overall `bazel coverage` percentage |
| **Req** | Requirement | LLRs with passing test / total LLRs |
| **MC/DC** | Branch | branches independently varied / total branches |

### Enriched Source Tree (WorkspaceTreePanel)

Three levels of depth:

**Level 1 — Directories and Files.** Mirrors `src/` and `tests/` from
the workspace. Per-file badges: green (fully traced), amber (partially
traced), red (untraced public functions), grey (no graph node).

**Level 2 — Functions.** Expanding a file reveals its functions. Each
shows function name with `f` prefix, status dot, and line number. For
test files: green = traced + passing RESULT, amber = only one of traced
or passing, red = neither or failing.

**Level 3 — Traced Graph Nodes.** Expanding a function reveals the LLR,
CASE, and RESULT nodes it traces to, each with coloured chip and title.

### CodeTraceView

The right column shows source code with:

- Line numbers and coloured gutter bars (each LLR gets a distinct colour
  from a 10-colour palette).
- Breadcrumb bar: `file -> node -> LLRs`.
- `scrollToLine` prop for auto-scroll when selecting a function in the
  tree.
- LLR legend sidebar: clickable badges to highlight all lines
  implementing a given LLR.

### Requirement Selection and Dimming

The NodeTablePanel (left side) supports multi-select. Selecting
requirements filters the source tree and code view:

- **Files** not tracing to any selected requirement are dimmed
  (`opacity-30`) but remain visible and clickable.
- **Functions** within matching files are dimmed if their `llr_ids` do
  not intersect the selection.
- **Code view** highlights only lines traced to selected requirements;
  non-selected trace badges render greyed out.
- **Empty selection** = unfiltered: everything at full brightness.

---

## Design Decisions

1. **Mission agent, not fragmented pipeline** — one agent in one
   continuous conversation handles the entire code generation session.
   No planner, no per-gap dispatch, no triage overhead.

2. **Graph as scoreboard, not project manager** — the graph provides a
   deterministic value function and gap list. The agent calls
   `evaluate_progress` when it wants feedback. The graph never
   prescribes workflow or ordering.

3. **min() value function** — the score is the minimum across all
   coverage dimensions, not a weighted average. One lagging dimension
   holds the entire score down.

4. **Context upfront** — Round 1 pre-loads full graph context so the
   agent does not waste tool calls discovering what it needs to build.
   Broader project docs are available on demand via `read_docs`.

5. **Sequential generation** — source files are generated sequentially.
   Each DESIGN may depend on types/imports from earlier modules.
   Sequential generation avoids conflicts.

6. **Bazel for hermetic builds** — eliminates environment isolation
   issues between the forge environment and the generated workspace.
