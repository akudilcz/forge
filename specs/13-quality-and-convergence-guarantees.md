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
| Content matches one of the five EARS patterns (Mavin et al.) — Ubiquitous "The \<system\> shall …", State-driven "While \<state\>, the \<system\> shall …", Event-driven "When \<trigger\>, the \<system\> shall …", Optional-feature "Where \<feature\>, the \<system\> shall …", Unwanted-behaviour "If \<condition\>, then the \<system\> shall …" — or a Complex combination (optional While-clause first, then When/Where/If…then clauses, always ending in the shall-clause). Classified deterministically by `classify_ears`; the detected pattern is stamped as `properties.ears_pattern` by the engine (agents never supply it). A rejection names the nearest pattern template. No raw `PARA-nnnn` placeholders. *(Historical note: this invariant previously forced a "The system shall" prefix, which forbade four of the five real EARS patterns and conflicted with the EARS quality axis — wording-repair churn measured ~919 calls per build family.)* | HLR, LLR |
| Non-empty content at least 50 characters | ARCHITECTURE, MODULE, CONTRACT, DESIGN, SUITE, CASE nodes |
| Content not identical to a same-type sibling's | All types except PARA (document mirrors) |
| `trace_to` non-empty and correctly typed (CASE_HLR→HLR, CASE_LLR→LLR) | Test cases |
| `properties.non_normative` marking well-formed: allowed on PARA nodes only; `non_normative: true` requires a `non_normative_rationale` naming a documented reason kind (`background/context`, `duplicate-of-<PARA-id>`, `example/illustration`, `meta/document-structure`) | PARA |
| `properties.derived` marking well-formed: allowed on requirement nodes only; boolean; `derived: true` requires a non-empty `derived_rationale` explaining the design necessity the requirement emerged from (a DO-178C derived requirement) | HLR, LLR |
| `properties.verification_method`, when present, names one of the four standard methods (IEEE 29148): `test`, `analysis`, `inspection`, `demonstration` (case-insensitive). Optional — legacy graphs carry none; allowed on requirement nodes only | HLR, LLR |
| Structured `properties.public_api` present and well-formed (module, symbol, kind, signature per entry; optional per-entry `raises` / `preconditions` / `postconditions` / `invariants` shape-checked when present); `prohibited_constructs` well-formed when present | CONTRACT |

Batched writes are validated as a whole and rejected atomically — a bad
operation never leaves a batch half-applied.

## Paragraph coverage — cover or classify

Phase 3's coverage certificate is `UNCOVERED_PARA`-free, and the gap has
"covered or explicitly non-normative" semantics: it fires for a body PARA
that neither carries an HLR child nor a valid `non_normative` marking.
A valid marking (flag plus documented rationale, table above) exempts the
paragraph; a marking whose rationale is missing or invalid is reported
loudly as `INADEQUATE_CONTENT` with the exact shape-check message — an
invalid classification is never a silent exemption and never a plain
coverage gap. Heading and empty PARAs remain exempt as before. This
replaces the old one-HLR-per-paragraph quota, which manufactured
near-duplicate requirements (a recognised requirements defect class) that
the duplicate-removal machinery then had to delete.

## Derived requirements and verification methods

What derivation decides is persisted, never discarded. The
`derive_requirement` tool returns `req_text`, `verification_method`,
`derived`, and `derived_rationale`; the authoring prompts (per-gap and
batch, phases 3 and 7) instruct the agent to persist the last three as
node properties, and to mark `derived: true` plus a rationale itself
whenever a requirement has no direct provenance in the parent text — it
emerges from design necessity rather than restating a stated obligation
(the DO-178C derived-requirement concept).

Consequences the analyser guarantees:

- **Staleness exemption.** A requirement validly marked `derived: true`
  is exempt from parent-content-hash staleness (`STALE_NODE`) — it
  legitimately lacks tight parent-text provenance, so a parent edit is
  not evidence it is out of date.
- **A derived marking without its rationale is a loud gap.** The
  exemption is never silent: `derived: true` with a missing or invalid
  `derived_rationale` (or a marking on a non-requirement node) is
  reported as `INADEQUATE_CONTENT` naming the exact property to fix —
  and the node stays in the normal staleness regime until fixed.
- **Phase 10 consumes the method.** Case authoring receives each
  requirement's `verification_method` and derived status: a
  `test`-method requirement needs an executable case, while `analysis`
  / `inspection` / `demonstration` requirements get a case documenting
  that obligation and the evidence that discharges it.

## LLM quality judging

Semantic quality (things static checks cannot see) is judged by batched LLM
calls, one verdict per node per axis:

- **Axes**: ATOMIC (one obligation), EARS (pattern *choice* only — is the
  requirement using the right EARS pattern for its semantics: unwanted/error
  behaviour must be "If …, then …", triggered behaviour "When …", stateful
  behaviour "While …"; surface syntax is the write-time classifier's job,
  and an EARS FAIL names the expected pattern), MATCH (title reflects
  content), SPECIFIC (title is concrete). Requirement nodes get all four;
  other authored nodes get the title axes.
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

## Oracle validation — independent CASE-oracle judging (phase 10)

Most LLM test-generation errors are wrong **oracles**: the case exercises
the right topic but asserts an outcome the requirement never states, so a
wrong implementation passes and the defect silently steers code generation.
Phase 10 therefore ends with an independent judge (`oracle_check`) that
validates every CASE against its traced requirement text and the owning
module's CONTRACT record (CASE_HLR: the MODULE whose `trace_to` owns the
HLR; CASE_LLR: via the LLR's parent HLR), on three axes:

- **OUTCOME** — the expected outcome actually *follows from the traced
  requirement text* — judged against the text, never against what a typical
  implementation would plausibly do (the plausible-but-wrong oracle is the
  failure this axis exists to catch).
- **CONTRACT** — where the CONTRACT record states exception or return
  semantics for the symbol under test, the case encodes them exactly
  (exception class and base class, exact return values); a record stating
  no applicable obligation passes.
- **DISCRIMINATES** — the case names a *concrete discriminating input*:
  real data a specific wrong implementation would fail on. The authoring
  prompts already demand this (contract-encoding rules); the judge verifies
  it is real, not boilerplate.

Mechanics mirror the combined quality judge:

- **Chunking**: cases are judged in chunks of `llm.quality_judge_batch_size`
  (default 25) so large phases never exceed the model's output limit.
- **A missing verdict is never a pass**: cases without a full verdict are
  re-asked exactly once; anything still unjudged raises
  `UnjudgedQualityError` and the step fails loudly. Verdicts naming
  hallucinated case ids are discarded with a logged warning.
- **Verdict caching**: a PASS is sticky per (case, hash of case + traced
  requirement text + contract record) — an edit to *any* of the three
  rotates the key and forces a re-judgement. A FAIL is never cached.
- **Gate semantics**: oracle quality *gates* phase 10 completion. A failed
  axis emits one `INCONSISTENT_CONTENT` repair gap on the CASE (existing
  taxonomy; the gap carries the judge's per-axis findings, and its repair
  prompt orders a requirement-faithful rewrite), dispatched before the
  phase completes. This is deliberately stricter than duplicate removal:
  keeping an unjudged node is safe, but an unvalidated oracle is live
  evidence phase 12 will code against — silence never passes.

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
| Phase 12 mission passes | 4 | Stops with remaining gaps reported; the requirement-coverage gate still decides acceptance |
| Phase 12 tool calls per pass | 200 | Pass ends; next pass re-scans |
| Phase 12 repair passes per gap cluster | 2 | Next pass REGENERATEs the slice from scratch (fresh thread; contract + design + failing evidence only; temperature bump) instead of repairing — regeneration still counts within the 4-pass bound |
| Phase 12 mutation rounds per completion attempt | 1 | Surviving mutants on traced lines become `WEAK_CASE` gaps ("write a test case this diff fails") with one remediation pass; the round is never repeated |
| Phase 12 mutation round runtime | 300 s (and 20 mutants/file) | Remaining mutants skipped with a loud WARN — a skip never blocks completion and is never a silent pass |
| Conversation history per dispatch | `llm.dispatch_token_budget` (24k tokens) | Oldest messages trimmed deterministically |
| Phase 12 mission history per call | `llm.mission_token_budget` (60k tokens) | Compaction escalates until the history fits (below) |

### Phase 12 mission history

The mission thread runs one continuous conversation for up to 200 tool calls,
so its prompt is compacted before every LLM call. `llm.mission_token_budget` is
a **binding** cap, not a target: a preserved message is excerpted rather than
sent whole when that is what fitting the budget requires. Messages are never
dropped or reordered — every tool call keeps its matching result — so only
message *contents* change, and every step is deterministic.

Compaction escalates only as far as the budget demands:

1. **Stub old tool results.** Non-preserved tool outputs, oldest first, are
   replaced by a one-line note saying the tool can be re-run.
2. **Truncate preserved tool results.** Preserved outputs become a head+tail
   excerpt carrying a marker that names how many characters were elided and
   that the tool can be re-run. Excerpt sizes are a max-min fair split of the
   budget left after everything undroppable is counted, so the biggest outputs
   (a full test-suite log, a whole-file read) give up the most and small ones
   stay verbatim. The latest `evaluate_progress` result — the agent's
   current-state signal — is served first and yields **last**, only once every
   other preserved output is already at its minimum useful excerpt.
3. **Shrink the preserved window.** If four preserved turns cannot all keep a
   usable excerpt, the window drops to 2, then 1, re-running steps 1-2 at each
   size. The most recent turn is always preserved.
4. **Cut the initial mission context** back to its first 4k tokens.

The system prompt and the initial context's first 4k tokens are the floor and
are never removed. If the floor plus the undroppable message envelopes still
exceed the budget, the history is sent over budget and an **ERROR** names the
floor's composition — that is a configuration problem the operator must fix
(raise the budget, shrink the system prompt or mission context, or lower the
tool-calls-per-pass bound), never a silent overrun.

Cost efficiency inside the bounds: small same-family fixes (title and
requirement-wording repairs) are batched into one LLM call per family when
three or more accumulate, with per-node failures falling back to normal
dispatch — never silently dropped.


### Hard per-call deadline

Every LLM call is additionally bounded by a hard wall-clock deadline
(`call_deadline_seconds`, default 900s, enforced with `asyncio.wait_for` at the
transport seam). The HTTP read timeout alone cannot bound a trickling or wedged
connection (live evidence: a call sat in the event loop's selector for 43+
minutes); the deadline aborts the call loudly and the caller's normal
retry/failure handling applies.

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
- **The Phase 12 requirement-coverage gate is absolute**: the generated
  codebase is rejected unless all tests pass and every requirement has
  both an implementation trace and passing test evidence. Statement and
  MC/DC percentages are report-only — measured, persisted, and WARNed on
  shortfall, never blocking (see [03-build-pipeline.md](03-build-pipeline.md)
  §The Phase 12 acceptance gate).
- **Test evidence is always fresh**: Phase 13 purges stale test artifacts and
  re-runs the full suite before recording RESULT nodes — evidence is never
  carried over from a previous run.
