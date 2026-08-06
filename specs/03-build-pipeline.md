# 03 — The Build Pipeline (Phases 0–14)

FORGE drives the build as a strict, numbered sequence of phases. A phase
cannot begin until every blocking gap from all prior phases is closed. The
user sees phase progress in the sidebar strip on every screen, and can open a
dedicated dashboard per phase via `/phase/:phaseNum`.

## Summary

| # | Name                  | Driver          | Produces                                   | Complete when |
|---|-----------------------|-----------------|--------------------------------------------|---------------|
| 0 | Create Project        | Human + backend | PROJECT node                               | PROJECT exists |
| 1 | Ingest Document       | Deterministic   | DOCUMENT node from `forge.md`              | DOCUMENT exists |
| 2 | Parse Document        | Deterministic (markdown) / Agent (exception) | PARA tree (paragraph/section nodes) | No `UNCHUNKED_DOCUMENT` |
| 3 | Derive HLRs           | Agent (batch)   | High-level requirements                    | No `UNCOVERED_PARA` |
| 4 | Create Architecture   | Agent           | ARCHITECTURE node                          | No `UNARCHITECTED` |
| 5 | Assign Modules        | Agent (batch)   | MODULE nodes owning HLRs                   | No `UNMODULARISED` |
| 6 | Write Contracts       | Agent           | One CONTRACT per MODULE                    | No `UNCONTRACTED` |
| 7 | Derive LLRs           | Agent (batch)   | Low-level requirements                     | No `UNREFINED_HLR` |
| 8 | Create Designs        | Agent (batch)   | DESIGN specs per module                    | No `UNDESIGNED` |
| 9 | Write Test Strategy   | Agent           | SUITE node                                 | No `UNSUITED` |
|10 | Write Test Cases      | Agent (batch)   | CASE_HLR / CASE_LLR nodes                  | No `UNTESTED_HLR` / `UNTESTED_LLR` |
|11 | Render Documentation  | Deterministic   | 8 Markdown docs in `workspace/docs/`       | Render returns |
|12 | Generate Code         | Mission agent   | `src/`, `tests/`, build files, coverage    | Coverage gate (below) |
|13 | Workspace Sync        | Deterministic   | CODE / TEST / RESULT nodes                 | No `UNSYNCED_DESIGN` / `UNSYNCED_TEST` |
|14 | Build Deliverables    | Deterministic   | `deliverables/` + `deliverables.zip`       | Pack built |

Phases 0, 1, 11, 13, and 14 are deterministic — zero LLM calls. Phase 2 is
deterministic for markdown documents (the standard case) and agent-driven
only for documents without markdown structure. All other
phases dispatch agents over the Observe-Act loop: the Gap Analyser detects
typed gaps, an agent resolves them by writing to the graph, and resolution is
certified by re-analysis (see
[13-quality-and-convergence-guarantees.md](13-quality-and-convergence-guarantees.md)).
The architecture-first discipline is built into gap priority: the full
skeleton (ARCHITECTURE, MODULEs, CONTRACTs — Phases 4–6) exists before
detailed requirements are elaborated in Phase 7.

Within every agent-driven phase, quality runs **inline**: after structural
work, the phase runs deterministic quality checks, batched LLM quality
judging, and semantic duplicate removal over the node types it created.
Phases whose gaps are interdependent (3, 5, 7, 8, 10) use batch dispatch —
the agent sees all gaps and all existing target nodes at once, in chunks of
`llm.batch_author_chunk_size` (default 20); anything a batch cannot close
falls back to one-at-a-time dispatch, so no structural gap is ever left
unattempted.

## Phase-by-phase

**0 — Create Project.** Writes the single PROJECT root node (name +
description supplied by the user). Exactly one per workspace.

**1 — Ingest Document.** Deterministic read of `forge.md` into a DOCUMENT
node. A missing `forge.md` fails loudly; nothing proceeds without a source
specification.

**2 — Parse Document.** The DOCUMENT is chunked into a nested PARA tree
mirroring the document's structure. Each PARA carries its own text
verbatim (normative code blocks such as API signatures are kept, never
summarised) and a paragraph type (functional, rationale, constraint,
non-functional, heading).

The **primary route is deterministic**: a document *qualifies* when it has
at least 2 ATX markdown headings outside fenced code blocks (whitepapers
are markdown, and deterministic header splitting beats LLM chunking on
structured documents). Qualifying documents are split with zero LLM calls:
headings become empty heading PARAs nested by level; each paragraph,
bullet group, and fenced code block under a heading becomes one verbatim
body PARA whose type is assigned by conservative keyword heuristics
(normative wording → functional; limit wording → constraint; otherwise
rationale; code blocks are normative → functional). The **exception route**
— an LLM chunking agent — runs only for documents that do not qualify
(plain prose, setext-only markdown). This is a documented primary/exception
split, not a fallback: the build log states which route ran and why.
Completion criteria are identical on both routes, and all quality checks
run on the resulting PARAs either way.

**3 — Derive HLRs.** Every non-heading PARA with real body content receives
at least one high-level requirement. HLRs are EARS-style "The system
shall …" statements (enforced at write time) with a verification method
(test, analysis, review, or demo); requirements inferred rather than stated
are marked derived, with rationale. Normative details — exact exception
types, return-value contracts, ordering and tie-break rules — must be
captured, not paraphrased away.

**4 — Create Architecture.** A single ARCHITECTURE node decomposing the
system: module inventory, interfaces, cross-cutting concerns, rationale.
Traces to the HLRs it addresses.

**5 — Assign Modules.** MODULE nodes under the ARCHITECTURE, each with a
responsibility statement and traces to the HLRs it owns. Complete when every
HLR is owned by at least one MODULE; a MODULE tracing to nothing is itself a
gap.

**6 — Write Contracts.** One CONTRACT per MODULE: prose interface spec plus
a **structured `public_api`** (module, symbol, kind, signature per entry —
signatures transcribed verbatim from the source document) and optional
`prohibited_constructs`. Entries carry structured obligation fields —
`raises` (`{cls, base, when}` records), `preconditions`, `postconditions`,
`invariants` — transcribed verbatim from the source document wherever it
states them; the dividing rule is that anything expressible as
pre/post/raises/invariant is contract material, while DESIGN holds only
private structure and algorithm choice (see
[13-quality-and-convergence-guarantees.md](13-quality-and-convergence-guarantees.md)).
A CONTRACT without a well-formed `public_api` (or with malformed obligation
fields) is rejected at write time. Contracts are the coordination boundary
all downstream phases are checked against.

**7 — Derive LLRs.** Each HLR is refined into one or more atomic, testable,
EARS-form low-level requirements, scoped to the owning MODULE's CONTRACT.
This phase carries the highest quality bar, including a decomposition-
completeness check (do the LLRs jointly cover the HLR, given the contract?).

**8 — Create Designs.** DESIGN specs per MODULE (class names, method
signatures, responsibilities), each tracing to the LLRs it implements.
Complete when every LLR is covered by a DESIGN. LLRs that fit an existing
DESIGN are linked deterministically without an LLM call; a consolidation
step merges DESIGN sprawl — many LLRs deliberately share one DESIGN. DESIGNs
are checked against the CONTRACT's public surface and for cross-module
coupling.

**9 — Write Test Strategy.** A single SUITE node: test types, coverage
targets, environment, and per-module verification approach, authored with
full system context.

**10 — Write Test Cases.** Every HLR gets a CASE_HLR and every LLR a
CASE_LLR (level-specific — an HLR case never satisfies an LLR). Cases carry
preconditions, steps, expected results, and acceptance criteria, and must
encode the contract exactly (exact exception classes, exact return values,
full expected orderings). Case authoring receives the owning module's
structured CONTRACT records and must encode one case per `raises` entry
(If–then EARS shape) and one per stated postcondition. A coverage check
verifies each case actually
tests what it traces to; absent or unparseable verdicts never remove a
trace — they leave it unverified with a logged error.

**11 — Render Documentation.** Deterministic render of the graph into eight
Markdown files in `workspace/docs/`: `03-HLR`, `04-Architecture`,
`05-Modules`, `06-Contracts`, `07-LLR`, `08-Design`, `09-Test-Suite`,
`10-Verification`. Same graph, byte-identical docs; each section is
self-contained (requirements inlined where referenced). These docs are the
primary context for the Phase 12 agent.

**12 — Generate Code.** A single long-running mission agent generates
`src/` (one file per DESIGN), `tests/`, a tracing package, and Bazel/pip
build scaffolding, then works a prioritised gap list detected by
deterministic workspace scanning: broken test environment, syntax errors,
missing sources/tests, API-surface mismatches (including unmet contract
`raises` obligations — exception class missing, wrong base class, or never
raised in its defining module), prohibited constructs,
failing tests, invalid or missing traces, coverage shortfalls,
unimplemented/uncovered requirements, weak traces, and scope creep. The
agent runs at most 4 passes (fresh conversation per pass, workspace
re-scanned between passes) of up to 200 tool calls each.

**13 — Workspace Sync.** Deterministic reconciliation of the workspace into
the graph: a CODE node per generated source file (under its DESIGN), a TEST
node per test file (under its CASE), then a fresh full test run — stale test
artifacts are purged first, evidence is never reused — recording one RESULT
node per test function (passed/failed/skipped/error). Re-runs refresh
rather than duplicate; RESULT nodes have stable IDs and misparented RESULTs
from interrupted runs are healed deterministically on resume. Test files no
CASE owns are skipped with a warning — they are not evidence.

**14 — Build Deliverables.** Deterministic packaging of
`workspace/deliverables/` and `deliverables.zip`: a README manifest; seven
rendered documents (`01-Requirements-Specification`, `02-Architecture`,
`03-Interface-Specification`, `04-Design-Specification`, `05-Test-Plan`,
`06-Traceability-Matrix`, `07-Coverage-Report`); the full `src/`, `tests/`,
and `tracing/` trees; and any project config files present (`pyproject.toml`,
`setup.py`, `setup.cfg`, `Makefile`, `requirements.txt`). Rebuilt clean on
every run; downloadable via the Deliverables screen
(see [09-deliverables.md](09-deliverables.md)). Note: Phase 14 renders
whatever the graph holds — run it after Phase 13 so the traceability matrix
and coverage report carry real evidence.

## The Phase 12 acceptance gate

The generated codebase is accepted only when all of these hold
simultaneously; otherwise the phase fails loudly:

1. **All tests pass** (a skipped test is not a pass), and at least one test
   ran if any LLRs exist.
2. **Statement coverage = 100%** of generated source.
3. **MC/DC branch coverage = 100%** wherever branches exist to measure.
4. **Every LLR is implemented** — cited by `@traces(LLR-…)` on at least one
   source function.
5. **Every LLR is verified** — covered by at least one passing traced test.
6. **Every function is traced** — every function in `src/`, including
   dunders and private helpers, carries `@traces(LLR-…)`.

These are a joint invariant, not independent thresholds. A function that
contributes to none of them is excess code; FORGE removes it rather than
retaining untraced implementation. The delivered workspace is the **minimal**
code satisfying the requirement graph, with every line auditable back to an
LLR — and via the graph, back to a paragraph of `forge.md`
(see [12-artifact-model-and-traceability.md](12-artifact-model-and-traceability.md)).

## Human approval gates

Phase transitions are gated. When a phase completes, the loop pauses and the
user sees a phase-complete indicator in the sidebar. The user presses Play
again to advance into the next phase — a review point before committing LLM
time to the next stage.

## Phase auditor

When a phase stabilises, a deterministic auditor re-runs the Gap Analyser
and requires the completion gap types for this phase **and all prior phases**
to be absent (the audit is cumulative — later phases can never paper over
earlier holes). If residual gaps remain, the phase is not marked complete.

## Idempotency and re-runs

Each phase is safe to re-run on a complete graph: the Gap Analyser finds
zero gaps and the phase exits immediately. The user can re-run any phase
from its dashboard without corrupting prior work, and a new FORGE process
over the same graph DB and workspace resumes exactly where the last one
stopped (see [12-artifact-model-and-traceability.md](12-artifact-model-and-traceability.md)).

## What the user controls

- **Play / Pause** — see [04-loop-control.md](04-loop-control.md).
- **Re-run a phase** — from the phase dashboard.
- **Human approval at phase gates** — by pressing Play to advance.
- **Which model runs each phase** — per-phase model configuration in
  Settings (see [07-settings.md](07-settings.md)).

## What the user does not control directly

- Which agent runs next within a phase — driven by gap priority.
- The order of gap resolution — driven by the Gap Analyser.
