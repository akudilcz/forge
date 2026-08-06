# 13 — Quality and Convergence Guarantees

FORGE's quality model rests on one principle: **a failed check is never a
passed check**. Verification never fails open, silence is never scored as
clean, and destructive actions require confirmed evidence. This spec lists
the guarantees an operator or auditor can rely on.

## Write-time invariants (correct by construction)

Every deterministic quality rule is enforced at the moment an agent writes
to the graph, not just detected afterwards. A violating write is **rejected**
with an actionable error, and the agent corrects it in the same turn. The
same rule set backs the Gap Analyser, so enforcement and detection can never
disagree. Enforced on every add/update:

| Invariant | Applies to |
|-----------|-----------|
| Title present, at most 7 words | All authored types |
| Title unique among siblings (case/whitespace-insensitive) | All authored types |
| Title distinct from the parent's title | All authored types |
| Content starts with "The system shall …"; no raw `PARA-nnnn` placeholders | HLR, LLR |
| Non-empty content at least 50 characters | ARCHITECTURE, MODULE, CONTRACT, DESIGN, SUITE, CASE nodes |
| Content not identical to a same-type sibling's | All types except PARA (document mirrors) |
| `trace_to` non-empty and correctly typed (CASE_HLR→HLR, CASE_LLR→LLR) | Test cases |
| Structured `properties.public_api` present and well-formed (module, symbol, kind, signature per entry; optional per-entry `raises` / `preconditions` / `postconditions` / `invariants` shape-checked when present); `prohibited_constructs` well-formed when present | CONTRACT |

Batched writes are validated as a whole and rejected atomically — a bad
operation never leaves a batch half-applied.

## LLM quality judging

Semantic quality (things static checks cannot see) is judged by batched LLM
calls, one verdict per node per axis:

- **Axes**: ATOMIC (one obligation), EARS (template conformance), MATCH
  (title reflects content), SPECIFIC (title is concrete). Requirement nodes
  get all four; other authored nodes get the title axes.
- **A missing verdict is never a pass.** Nodes the judge failed to score are
  re-asked exactly once; anything still unjudged fails the step loudly
  (`UnjudgedQualityError`) rather than being scored clean. Verdicts naming
  hallucinated node IDs are discarded with a logged warning.
- **Chunking**: candidates are judged in chunks (default 25 nodes per call,
  `llm.quality_judge_batch_size`) so large phases never exceed the model's
  output limit.
- **Verdict caching**: a PASS is sticky per `(node, content-hash)` — an
  unchanged node is never re-judged in later cycles. A FAIL is **never**
  cached: a repaired node is always re-judged. The cache is in-memory only;
  a restart costs at most one re-judging sweep.

## Duplicate removal — deletion safety

Requirement text is never destroyed on a single model opinion:

- **Byte-identical siblings** (same parent, same type, identical normalised
  content) are resolved deterministically without any LLM: traces are merged
  into the canonical (oldest) node, then the younger node is deleted.
- **Semantic duplicates** require the same DUPLICATE verdict from **two
  independent LLM calls** before deletion. A UNIQUE verdict is sticky per
  `(node, content-hash)`; an unparseable judge response is retried once and
  then treated as UNJUDGED — the node is **kept** and the failure logged.
  Unjudged candidates never halt the build (keeping a node is always safe).
- A cheap deterministic lexical prescreen skips clearly-dissimilar pairs
  before the judge; it can only *reduce* LLM calls, never authorise a
  deletion.
- **PARA nodes are exempt** from duplicate deletion entirely: they mirror
  document structure, where repeated text and empty headings are legitimate,
  and deleting one would flatten the document tree.

## Contract enforcement

CONTRACTs are the coordination boundary between modules, enforced in three
stages:

1. **Write time** (Phase 6): every CONTRACT must declare a structured
   `public_api`; malformed declarations are rejected. Each entry may
   additionally carry structured **obligation fields** — `raises` (a list of
   `{cls, base, when}` records), `preconditions`, `postconditions`, and
   `invariants` (lists of strings) — shape-checked at write time when
   present. Function/method entries should carry them whenever the
   whitepaper states any (prompt-enforced: presence cannot be verified
   deterministically).
2. **Design time** (Phase 8): a DESIGN that redefines a public symbol with a
   disagreeing signature raises `CONTRACT_VIOLATION`. Private helpers are
   never violations — contracts constrain the public surface only. A
   deterministic **misplaced-obligation** check also raises
   `CONTRACT_VIOLATION` when DESIGN text confidently asserts observable
   behaviour for a public symbol ("raises SomeError" / "returns None"
   patterns — never prose guesses) that the symbol's CONTRACT record does
   not carry; the gap says exactly which obligation to move into the
   contract (or align).
3. **Code time** (Phase 12): the workspace must actually expose every
   `public_api` entry (`API_SURFACE_MISMATCH`) and must not use any
   contract-banned construct in `src/` (`PROHIBITED_CONSTRUCT`; tests are
   exempt — bans constrain implementation, not verification). For every
   entry with `raises` records, the **raises gate** verifies statically
   (AST, no code executed) that each named exception class exists in
   `src/` with the declared base class and is actually raised in the
   entry's defining module — a mismatch is an `API_SURFACE_MISMATCH` gap
   quoting the contract record. Deeper pre/postcondition checking is
   future work.

**Dividing rule (CONTRACT vs DESIGN):** anything expressible as a
precondition, postcondition, raises obligation, or invariant is contract
material and belongs in the structured `public_api` fields; DESIGN holds
only private structure and algorithm choice. Phase 10 consumes the records:
case authoring receives the contract record for the symbol under test and
must encode one case per `raises` entry (If–then EARS shape) and one per
stated postcondition.

## Resolution certificates

A gap is declared resolved only when it is **proven** resolved: after each
dispatch the Gap Analyser re-runs, and the gap's exact `(type, node)` key
must be absent from the fresh analysis. A write that does not actually close
the dispatched gap can never count as progress — graph activity is not
evidence, absence of the gap is.

## Convergence bounds

The system assumes convergence but bounds every loop, so a non-converging
build halts loudly instead of spending indefinitely:

| Loop | Bound | At the bound |
|------|-------|--------------|
| Pipeline cycles per phase (deletion-triggered re-runs) | 12 | Forced exit, logged |
| Dispatch attempts per gap per pass | 3 | Gap abandoned for the pass; the phase audit then fails loudly |
| Batch-authoring retries per chunk | 3 | Stragglers fall back to per-gap dispatch — no structural gap is ever left undispatched |
| Phase 12 mission passes | 4 | Stops with remaining gaps reported; the coverage gate still decides acceptance |
| Phase 12 tool calls per pass | 200 | Pass ends; next pass re-scans |
| Conversation history per dispatch | `llm.dispatch_token_budget` (24k tokens) | Oldest messages trimmed deterministically |
| Phase 12 mission history per call | `llm.mission_token_budget` (60k tokens) | Oldest tool output pruned; recent turns preserved |

Cost efficiency inside the bounds: small same-family fixes (title and
requirement-wording repairs) are batched into one LLM call per family when
three or more accumulate, with per-node failures falling back to normal
dispatch — never silently dropped.

## Fail-loud principles

- **Missing preconditions halt.** No `forge.md`, no API key (unless the
  endpoint is explicitly declared keyless), a missing node — all produce
  immediate loud errors, never degraded output.
- **Step exceptions propagate.** A failing pipeline step marks the phase
  `awaiting_approval` and re-raises; the run never continues on an
  unverified graph.
- **Quota exhaustion halts the run** — it is never converted into an empty
  gap list or a "check complete" message.
- **Transient LLM failures are retried a bounded number of times** (client
  retries plus one application-level retry where applicable); a persistent
  failure propagates rather than being swallowed.
- **The Phase 12 coverage gate is absolute**: the generated codebase is
  rejected unless all tests pass, statement and (where measurable)
  MC/DC branch coverage are 100%, and every requirement has both an
  implementation trace and passing test evidence
  (see [03-build-pipeline.md](03-build-pipeline.md)).
- **Test evidence is always fresh**: Phase 13 purges stale test artifacts and
  re-runs the full suite before recording RESULT nodes — evidence is never
  carried over from a previous run.
