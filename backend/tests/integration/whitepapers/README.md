# Integration Whitepaper Corpus

Input specifications for the end-to-end integration suite. Each file is a
`forge.md`-style whitepaper that FORGE ingests at Phase 1 and builds through to
the Phase 14 deliverables bundle.

The corpus exists to answer one question: **does FORGE reliably turn a
specification into a correct, traced, tested codebase?** Each whitepaper stresses
a different capability, so a regression in any one area shows up as a specific
failing build rather than a vague drop in quality.

| # | Whitepaper | Primary stress | Why it is here |
|---|---|---|---|
| 01 | [Stable merge sort](01_stable_merge_sort.md) | Recursion + a cross-cutting invariant | Stability is a property no single function owns; it must survive decomposition into modules |
| 02 | [LRU cache](02_lru_cache.md) | Mutable state, structural invariants | Two data structures kept in sync; exposes whether generated code maintains invariants across operations |
| 03 | [Expression evaluator](03_expression_evaluator.md) | Parsing, layered error taxonomy | Four distinct error classes with positional reporting — tests whether error paths get implemented, not just happy paths |
| 04 | [Topological sort](04_topological_sort.md) | Graph traversal, determinism | Requires iterative algorithms and reproducible output; a `set`-iteration shortcut fails the determinism property |
| 05 | [Online statistics](05_online_statistics.md) | Floating-point numerical stability | The naive formula passes casual tests and fails the accuracy criteria; separates real implementation from plausible-looking code |
| 06 | [Edit distance](06_edit_distance.md) | Dynamic programming + reconstruction | Matrix boundary off-by-ones; the backtrace must produce a script that actually transforms a into b |
| 07 | [Binary search family](07_binary_search_family.md) | Boundary conditions, termination | Nine routines whose entire difficulty is edge cases; an infinite loop is a real failure mode |
| 08 | [Priority queue](08_priority_queue.md) | Array-encoded tree, index bookkeeping | Sift-up/sift-down arithmetic plus an index map that must stay consistent through every swap |
| 09 | [Interval algebra](09_interval_tree.md) | Half-open boundary semantics | Touching versus overlapping is the entire difficulty, and it is exactly what gets confused |
| 10 | [CSV parser](10_csv_parser.md) | Character-level state machine | Quoted fields containing delimiters, quotes and newlines — the cases naive splitting silently corrupts |
| 11 | [Union-find](11_union_find.md) | Amortised complexity | The structure must genuinely compress paths, not merely return correct answers |
| 12 | [Rational arithmetic](12_rational_arithmetic.md) | Invariant maintenance | Always lowest terms, positive denominator, exact equality and hashing consistency |
| 13 | [Prefix trie](13_trie.md) | Recursive structure with pruning | Deletion must not orphan or over-prune branches shared with other keys |
| 14 | [Circular buffer](14_circular_buffer.md) | Wraparound arithmetic | The full-versus-empty ambiguity when head meets tail |
| 15 | [SemVer](15_semver.md) | Precedence rules widely got wrong | Pre-release ordering per SemVer 2.0.0, which most implementations get subtly wrong |

## Shape of a whitepaper

Every file follows the structure FORGE's Phase 2 parser expects, and which the
demo whitepapers in [`demos/`](../../../../demos/) also use:

- **Abstract** — one paragraph stating what the library does.
- **Numbered sections** — the substance. Algorithms are given as pseudocode or
  explicit recurrences so requirements are derivable rather than guessable.
- **Complexity** — a table. Gives Phase 3 concrete non-functional requirements.
- **Correctness Properties** — numbered, testable claims. These map most directly
  onto generated test cases and are the main lever on output quality.
- **Failure Modes and Edge Cases** — the error paths. Without this section,
  generated suites cover only happy paths.
- **Public API** — exact signatures, so the Phase 6 contracts are unambiguous.
- **Implementation Notes** — a prohibition on delegating to the stdlib module
  that would trivially solve the problem (`list.sort`, `bisect`, `heapq`, `csv`,
  `fractions`, `re`, `graphlib`, `statistics`, `deque`, `eval`).

That last point is deliberate and load-bearing. Without it the pipeline can emit
a one-line wrapper that passes every functional test while implementing nothing,
so each whitepaper names the shortcut it forbids and the matching oracle asserts
the shortcut was not taken.

## Oracles

Each whitepaper has an oracle in [`../oracles/`](../oracles/) authored **from the
whitepaper only** and never shown to any agent. It is the one quality gate FORGE
cannot grade itself on — every other signal (tests pass, coverage is high) comes
from tests the same agent wrote alongside the code, so a misread spec produces a
build that is wrong and green.

Every oracle is itself validated in
[`../../test_oracle_conformance.py`](../../test_oracle_conformance.py) against a
reference implementation. An oracle that has never executed would fail a
*correct* build hours into a paid run, which is worse than having no oracle at
all.

## Cost

These drive real LLM calls through all 15 phases and are correspondingly slow and
expensive. They are marked `integration`, excluded from the default pytest run
and from CI. Run them deliberately:

```bash
make test-integration                                   # everything
uv run pytest backend/tests/integration/test_algorithm_builds.py \
  -m integration -k merge_sort                          # one case
```

Configuration comes from `.env` via the `FORGE_TEST_*` variables documented in
[`../conftest.py`](../conftest.py) and [`.env.example`](../../../../.env.example).
