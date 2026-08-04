# Binary Search Family: Bounds, Ranges, Rotation, and Real-Valued Search

Python Library Specification

## Abstract

This document specifies a Python library of binary search routines over sorted
sequences: exact search, lower and upper bounds, equal-range, insertion point,
search in a rotated sorted array, peak finding, and bisection over a monotone
predicate on the reals. Every routine is iterative and runs in O(log n). The
specification is deliberate about half-open intervals and loop termination
because this family of algorithms is defined by its boundary conditions.

## 1. Overview and Design Rationale

Binary search is famously easy to state and hard to write correctly; the standard
library's own implementation carried an overflow bug for years. The failure modes
are all at the edges: an inclusive versus exclusive high bound, a midpoint that
rounds the wrong way for right-biased searches, and a loop condition that either
skips the last candidate or never terminates.

This library therefore fixes a single convention and applies it uniformly:

- All ranges are **half-open**: `[lo, hi)`. `hi` is one past the last candidate.
- The loop condition is always `while lo < hi`.
- The midpoint is `lo + (hi - lo) // 2`, never `(lo + hi) // 2`. Python integers
  do not overflow, so this is a readability and portability convention rather
  than a correctness fix, and it must be used consistently.
- On exit, `lo == hi` and that common value is the answer.

Routines that are naturally right-biased (upper bound, peak finding) use the
midpoint `lo + (hi - lo + 1) // 2` and shrink with `hi = mid - 1`; mixing the two
midpoint forms with the wrong shrink rule is the classic infinite loop and must
be covered by a test that would hang if it regressed.

## 2. Ordering Contract

Sequences are assumed sorted non-decreasing under a `key` function, default
identity. The library does **not** validate sortedness — that would be O(n) and
defeat the purpose — except in an explicitly opt-in `validate=True` mode used by
tests. Behaviour on unsorted input is undefined but must terminate.

## 3. Core Routines

### 3.1 lower_bound(seq, value)

Return the smallest index i in `[0, len(seq)]` such that `seq[i] >= value`.
Equivalently, the leftmost position at which `value` could be inserted keeping
the sequence sorted.

```
lo, hi = 0, len(seq)
while lo < hi:
    mid = lo + (hi - lo) // 2
    if key(seq[mid]) < value: lo = mid + 1
    else:                     hi = mid
return lo
```

### 3.2 upper_bound(seq, value)

Return the smallest index i such that `seq[i] > value` — the rightmost insertion
point. Identical to lower_bound with `<` replaced by `<=`.

### 3.3 equal_range(seq, value)

Return `(lower_bound, upper_bound)`. The number of occurrences of `value` is the
difference. For an absent value the two are equal and the range is empty; the
shared value is still a valid insertion point.

### 3.4 search(seq, value)

Return an index of some element equal to `value`, or `None` if absent. Must be
implemented in terms of `lower_bound` plus a single bounds-checked equality test,
not as a separate hand-rolled loop.

### 3.5 contains(seq, value)

`search(...) is not None`, but must not build intermediate structures.

## 4. Rotated Array Search

`search_rotated(seq, value)` searches a sorted sequence that has been rotated at
an unknown pivot, e.g. `[4,5,6,7,0,1,2]`.

At each step exactly one half `[lo, mid]` or `[mid, hi]` is sorted; determine
which by comparing `seq[lo]` with `seq[mid]`, then test whether `value` lies
within the sorted half's range and recurse into it.

Duplicates degrade the guarantee: when `seq[lo] == seq[mid] == seq[hi]`, neither
half can be identified and the algorithm must shrink by one, giving O(n) worst
case. This degradation is required behaviour, must be documented, and must not be
"optimised" away by an incorrect early exit.

`find_rotation_point(seq)` returns the index of the smallest element — 0 if the
sequence is not rotated.

## 5. Peak Finding

`find_peak(seq)` returns an index i such that `seq[i] >= seq[i-1]` and
`seq[i] >= seq[i+1]`, treating out-of-range neighbours as −∞. A peak always
exists for a non-empty sequence, so this never returns None. When several peaks
exist, any is acceptable — but the returned index must genuinely satisfy the peak
property, which is what the test asserts rather than a specific index.

## 6. Real-Valued Bisection

`bisect_predicate(predicate, lo, hi, *, tolerance=1e-9, max_iterations=200)`
finds the boundary of a monotone boolean predicate over a real interval: the
predicate is False on `[lo, x)` and True on `[x, hi]`. Returns x within
`tolerance`.

- `predicate(lo)` must be False and `predicate(hi)` must be True; otherwise raise
  `ValueError` describing which endpoint violated the assumption. Searching a
  non-monotone predicate silently returns a meaningless answer, so the endpoint
  check is mandatory.
- The loop halves the interval until `hi - lo < tolerance` or `max_iterations` is
  reached. Hitting the iteration cap raises `ConvergenceError` rather than
  returning a wrong answer.
- `tolerance <= 0` raises `ValueError`.

## 7. Complexity

| Routine | Time | Space |
|---|---|---|
| lower_bound, upper_bound, search | O(log n) | O(1) |
| equal_range | O(log n) | O(1) |
| search_rotated (distinct) | O(log n) | O(1) |
| search_rotated (duplicates) | O(n) worst | O(1) |
| find_peak | O(log n) | O(1) |
| bisect_predicate | O(log((hi-lo)/tol)) | O(1) |

Every routine is iterative; none may recurse.

## 8. Correctness Properties

1. **Bound correctness** — for every value v and sorted seq:
   `all(seq[i] < v for i in range(lower_bound))` and
   `all(seq[i] >= v for i in range(lower_bound, len(seq)))`.
2. **Insertion invariant** — inserting v at `lower_bound(seq, v)` keeps the
   sequence sorted. Same for `upper_bound`.
3. **Range consistency** — `lower_bound <= upper_bound` always, and their
   difference equals `seq.count(v)`.
4. **Agreement with the standard library** — `lower_bound` matches
   `bisect.bisect_left` and `upper_bound` matches `bisect.bisect_right` for all
   inputs. (The stdlib is an oracle for tests only; the implementation may not
   call it.)
5. **Termination** — every routine terminates for every input, including unsorted
   and adversarial ones. This must be enforced with a test timeout.
6. **Log bound** — the comparison count for `lower_bound` on a sequence of length
   n never exceeds `ceil(log2(n + 1))`. Testable by injecting a counting `key`.

## 9. Failure Modes and Edge Cases

- Empty sequence: all bounds return 0; `search` returns None; `find_peak` raises
  `ValueError`.
- Single element, both matching and non-matching.
- Value smaller than every element → bound 0. Value larger than every element →
  bound `len(seq)`. Both directions must be tested; a wrong `hi` initialiser
  fails only one of them.
- All elements equal to the search value: `lower_bound` is 0, `upper_bound` is
  `len(seq)`.
- Two adjacent distinct elements — the smallest case where a midpoint rounding
  error is observable.
- Rotated array rotated by 0 and by `len(seq)` (both are the unrotated case).
- Rotated array of all-identical elements.
- Peak at the first index, at the last index, and a strictly monotone sequence.
- `bisect_predicate` where the boundary sits exactly at `lo` or at `hi`.
- Sequences of 10^6 elements complete in logarithmic comparisons.

## 10. Public API

```python
def lower_bound(seq: Sequence[Any], value: Any, *, key: Callable = ..., 
                lo: int = 0, hi: int | None = None) -> int: ...
def upper_bound(seq: Sequence[Any], value: Any, *, key: Callable = ...,
                lo: int = 0, hi: int | None = None) -> int: ...
def equal_range(seq: Sequence[Any], value: Any, *, key: Callable = ...) -> tuple[int, int]: ...
def search(seq: Sequence[Any], value: Any, *, key: Callable = ...) -> int | None: ...
def contains(seq: Sequence[Any], value: Any, *, key: Callable = ...) -> bool: ...

def search_rotated(seq: Sequence[Any], value: Any) -> int | None: ...
def find_rotation_point(seq: Sequence[Any]) -> int: ...
def find_peak(seq: Sequence[Any]) -> int: ...

def bisect_predicate(
    predicate: Callable[[float], bool],
    lo: float,
    hi: float,
    *,
    tolerance: float = 1e-9,
    max_iterations: int = 200,
) -> float: ...

class ConvergenceError(RuntimeError): ...
```

The optional `lo`/`hi` parameters restrict the search to a sub-range, defaulting
to the whole sequence. `hi=None` means `len(seq)`.

## 11. Implementation Notes

- Do not call the `bisect` module from library code; it is permitted in tests as
  an oracle only.
- Use `lo + (hi - lo) // 2` uniformly, per §1.
- Every loop must have a clearly decreasing measure so termination is evident by
  inspection.
