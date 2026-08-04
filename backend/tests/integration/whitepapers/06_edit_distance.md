# Levenshtein Edit Distance with Alignment Backtrace and Banded Optimisation

Python Library Specification

## Abstract

This document specifies a Python library computing the Levenshtein edit distance
between two sequences, together with a concrete edit script that transforms one
into the other. Three implementations are required: a full dynamic-programming
matrix supporting backtrace, a space-optimised two-row variant for distance only,
and a banded variant that terminates early when the distance exceeds a caller
supplied threshold. A Damerau extension adding transposition is also specified.

## 1. Overview and Design Rationale

Edit distance is the classic dynamic-programming exercise, and its classic bug is
the boundary row. The recurrence is trivial in the interior and subtle at the
edges, so this specification states the base cases explicitly and requires them to
be tested independently of the interior.

Three variants exist because they trade different resources:

- Full matrix: O(mn) space, but the only variant that can reconstruct the edit
  script.
- Two-row: O(min(m,n)) space, distance only.
- Banded: O(k·min(m,n)) where k is the threshold, and returns early once the true
  distance is provably above k. Used when the caller only cares "is this within
  k edits?".

## 2. Cost Model

Three operations, each with a configurable non-negative cost:

| Operation | Default cost | Meaning |
|---|---|---|
| insertion | 1 | insert a symbol into a |
| deletion | 1 | delete a symbol from a |
| substitution | 1 | replace a symbol of a with one of b |

Costs are supplied at call time. A substitution cost of 2 or more makes
substitution never preferable to a delete-then-insert pair, which is a meaningful
configuration and must work correctly. Negative costs raise `ValueError`, since
they permit unbounded-negative alignments and non-termination of the optimum.

Matching symbols always cost 0 and are not an operation.

## 3. Recurrence

Let `a` have length m and `b` length n. Define `D[i][j]` as the minimum cost to
transform `a[:i]` into `b[:j]`.

**Base cases**

```
D[0][0] = 0
D[i][0] = i * cost_delete      for i in 1..m
D[0][j] = j * cost_insert      for j in 1..n
```

**Interior**

```
D[i][j] = min(
    D[i-1][j]   + cost_delete,
    D[i][j-1]   + cost_insert,
    D[i-1][j-1] + (0 if a[i-1] == b[j-1] else cost_substitute),
)
```

Note the `i-1` / `j-1` indexing into the sequences versus `i` / `j` into the
matrix — the matrix is one larger in each dimension than the sequences. This
off-by-one is the primary source of defects and must be covered by tests that use
sequences of different lengths, so that a transposed index fails loudly.

### 3.1 Damerau Extension

With transposition enabled, add a fourth branch when `i > 1`, `j > 1`,
`a[i-1] == b[j-2]`, and `a[i-2] == b[j-1]`:

```
D[i][j] = min(D[i][j], D[i-2][j-2] + cost_transpose)
```

This is the *restricted* (optimal string alignment) form, in which no substring
is edited more than once. It is not the unrestricted Damerau distance, and the
difference is observable: for `a="CA"`, `b="ABC"`, restricted distance is 3 while
unrestricted is 2. The library implements the restricted form and documents this.

## 4. Backtrace

From `D[m][n]`, walk back to `D[0][0]`, at each cell choosing the predecessor that
produced its value:

- diagonal with equal symbols → `match`
- diagonal with unequal symbols → `substitute`
- from above → `delete`
- from the left → `insert`

Ties are broken in a fixed priority order — match, substitute, delete, insert — so
the returned script is deterministic. The resulting list is reversed to give
operations in forward order.

Each operation records its kind, the index in `a`, the index in `b`, and the
symbols involved.

### 4.1 Script Validity

Applying the returned script to `a` must produce exactly `b`. The library exposes
`apply_script` so this is directly testable, and the round-trip is a required
property test.

The sum of the costs of the non-match operations in the script must equal the
returned distance.

## 5. Banded Variant

Given threshold k, only cells within k of the diagonal can participate in an
optimal alignment of cost ≤ k. For each row i, compute columns from
`max(1, i - k)` to `min(n, i + k)`.

- If `abs(m - n) > k`, return early: the distance exceeds k by the length
  difference alone, without computing anything.
- If every cell in a row exceeds k, return early with "greater than k".
- The banded result equals the full result whenever the true distance ≤ k. When
  it exceeds k, the function returns `k + 1` as a sentinel meaning "more than k",
  not the true distance.

## 6. Normalisation

`normalized_distance(a, b)` returns distance divided by `max(len(a), len(b))`,
yielding a value in [0, 1]. Two empty sequences have normalized distance 0.0, not
a ZeroDivisionError. `similarity(a, b)` returns `1 - normalized_distance`.

## 7. Generality

The library operates on any sequence of hashable, equality-comparable elements —
strings, lists of tokens, tuples. It must not assume `str`. A test comparing lists
of words rather than characters is required.

## 8. Complexity

| Variant | Time | Space |
|---|---|---|
| full matrix | O(mn) | O(mn) |
| two-row | O(mn) | O(min(m,n)) |
| banded, threshold k | O(k · min(m,n)) | O(min(m,n)) |

The two-row variant must swap row references rather than reallocating per row.

## 9. Correctness Properties

1. **Identity** — `distance(x, x) == 0` for every x.
2. **Symmetry** — `distance(a, b) == distance(b, a)` when insertion and deletion
   costs are equal. When they differ, symmetry does not hold and must not be
   assumed.
3. **Triangle inequality** — `distance(a, c) <= distance(a, b) + distance(b, c)`
   with default unit costs.
4. **Empty boundary** — `distance("", b) == len(b) * cost_insert`, and
   `distance(a, "") == len(a) * cost_delete`.
5. **Variant agreement** — full-matrix and two-row distances are identical for
   all inputs; the banded variant agrees whenever the true distance ≤ k.
6. **Script validity** — §4.1.
7. **Upper bound** — distance never exceeds `max(m, n) * max(cost_insert,
   cost_delete, cost_substitute)`.

## 10. Failure Modes and Edge Cases

- Both sequences empty: distance 0, empty script.
- One empty: distance is the other's length times the relevant cost.
- Identical sequences: distance 0, script is all matches.
- Completely disjoint alphabets of equal length: distance equals the length,
  achieved by substitutions when `cost_substitute <= cost_insert + cost_delete`.
- Sequences of length 10,000: the two-row variant must complete without
  exhausting memory; the full-matrix variant may legitimately be slow but must
  not raise `RecursionError` — the implementation is iterative.
- Unicode strings with combining characters are compared by code point; no
  normalisation is performed, and this is documented.
- Negative cost: `ValueError`.
- Threshold k < 0: `ValueError`.

## 11. Public API

```python
def distance(
    a: Sequence[Any],
    b: Sequence[Any],
    *,
    cost_insert: float = 1,
    cost_delete: float = 1,
    cost_substitute: float = 1,
    transpositions: bool = False,
) -> float: ...

def distance_banded(
    a: Sequence[Any], b: Sequence[Any], max_distance: float
) -> float:
    """Return the distance, or max_distance + 1 if it provably exceeds the bound."""

def edit_script(a: Sequence[Any], b: Sequence[Any], **costs: float) -> list[EditOp]: ...

def apply_script(a: Sequence[Any], script: Sequence[EditOp]) -> list[Any]: ...

def normalized_distance(a: Sequence[Any], b: Sequence[Any]) -> float: ...

def similarity(a: Sequence[Any], b: Sequence[Any]) -> float: ...

class EditOp:
    kind: str          # "match" | "insert" | "delete" | "substitute" | "transpose"
    index_a: int
    index_b: int
    symbol_a: Any | None
    symbol_b: Any | None
```

## 12. Implementation Notes

- Do not use `difflib`, `Levenshtein`, or `rapidfuzz`; the recurrence is the
  subject of the specification.
- All variants must be iterative.
- The full matrix is required for backtrace; do not attempt to reconstruct a
  script from the two-row variant.
