# Stable Merge Sort with Insertion-Sort Cutoff and Galloping Merge

Python Library Specification

## Abstract

This document specifies a Python library implementing a stable, comparison-based
sorting algorithm. The algorithm is a top-down merge sort that switches to binary
insertion sort for small runs and uses a galloping (exponential search) merge to
exploit partially ordered input. Sorting is stable: elements comparing equal
retain their original relative order. The library sorts a list in place and also
offers a non-mutating variant.

## 1. Overview and Design Rationale

Merge sort guarantees O(n log n) comparisons in the worst case, unlike quicksort,
and is stable, unlike heapsort. Its weakness is constant-factor overhead on small
inputs and on data that is already largely ordered. This specification addresses
both:

- Sub-arrays of length below a cutoff `MIN_RUN` are sorted with binary insertion
  sort, which has lower overhead at small n and is itself stable.
- The merge step uses galloping mode: when one side wins several comparisons
  consecutively, the merge switches to exponential search to locate the insertion
  point, copying a block at a time.

## 2. Parameters

| Name | Value | Meaning |
|---|---|---|
| `MIN_RUN` | 32 | Sub-arrays shorter than this use binary insertion sort |
| `MIN_GALLOP` | 7 | Consecutive wins by one side before entering galloping mode |

Both are module-level constants. `MIN_RUN` must be at least 2. `MIN_GALLOP` must
be at least 1.

## 3. Ordering Contract

Elements are ordered by a `key` function and a strict-weak-ordering `<`
comparison on the key results.

- The default `key` is the identity function.
- A `reverse` flag reverses the final ordering, and the result must still be
  stable. Two tempting implementations are both wrong:
  - Flipping the comparison inside the merge breaks stability, because equal
    elements then take the right run's element first.
  - Sorting ascending and reversing the output also breaks stability, because
    reversing the whole list also reverses the relative order of equal elements.

  The stability-preserving construction is a **double reversal**: reverse the
  input, sort ascending, then reverse the output. Equal elements are reversed
  twice and so end up back in their original relative order, while unequal
  elements come out descending.
- If `key` raises for any element, the exception propagates and the input list is
  left unmodified.

### 3.1 Stability

For any two indices i < j in the input where `key(a[i])` and `key(a[j])` compare
equal, the element originally at i must appear before the element originally at j
in the output. This property holds for both the ascending and the reversed form.

## 4. Binary Insertion Sort

For a slice `a[lo:hi]` with `hi - lo < MIN_RUN`:

1. For each index i from lo+1 to hi-1, let `v = a[i]`.
2. Binary search the sorted region `a[lo:i]` for the leftmost position p such that
   `key(a[p]) > key(v)`. Searching for the *leftmost strictly-greater* position,
   rather than the first greater-or-equal position, is what preserves stability.
3. Shift `a[p:i]` right by one and place `v` at p.

Comparisons: O(n log n). Moves: O(n²). Acceptable because n < MIN_RUN.

## 5. Merge

Merging two adjacent sorted runs `a[lo:mid]` and `a[mid:hi]`:

1. Copy the left run into a temporary buffer. The right run is merged in place
   from the front, so only the left run needs buffering.
2. Maintain counters `left_wins` and `right_wins`. On each comparison, take from
   the left run when `key(left) <= key(right)` — the `<=` is required for
   stability — otherwise take from the right.
3. When either counter reaches `MIN_GALLOP`, enter galloping mode.

### 5.1 Galloping

In galloping mode, to place a value `v` from one run into the other:

1. Exponential search: probe offsets 1, 2, 4, 8, ... until a bracketing interval
   is found.
2. Binary search within that interval for the exact insertion point, again using
   the leftmost-strictly-greater rule on the left side and the leftmost
   greater-or-equal rule on the right side, so equal elements keep left-run
   priority.
3. Copy the whole block at once.
4. Exit galloping mode when a galloping pass copies fewer than `MIN_GALLOP`
   elements.

## 6. Top-Level Algorithm

```
sort(a, key=identity, reverse=False):
    if len(a) < 2: return
    if reverse: a.reverse()          # double reversal (§3): equal elements are
    _sort_range(a, 0, len(a), key)   # reversed twice and so keep their original
    if reverse: a.reverse()          # relative order

_sort_range(a, lo, hi, key):
    n = hi - lo
    if n < MIN_RUN:
        binary_insertion_sort(a, lo, hi, key); return
    mid = lo + n // 2
    _sort_range(a, lo, mid, key)
    _sort_range(a, mid, hi, key)
    if key(a[mid - 1]) <= key(a[mid]): return   # already ordered, skip merge
    merge(a, lo, mid, hi, key)
```

The early-exit check on line `key(a[mid-1]) <= key(a[mid])` makes already-sorted
input run in O(n) merge time.

## 7. Complexity

- Comparisons: O(n log n) worst case, O(n) on already-sorted input.
- Auxiliary space: O(n/2) for the merge buffer, plus O(log n) recursion depth.
- Recursion depth is bounded by ceil(log2(n)); for n = 10^6 this is 20 frames.

## 8. Correctness Properties

1. **Sortedness** — for all consecutive pairs in the output, `key(a[i]) <= key(a[i+1])`
   (or `>=` when reversed).
2. **Permutation** — the output is a permutation of the input; no element is lost
   or duplicated. A multiset comparison of input and output must be equal.
3. **Stability** — as defined in §3.1.
4. **Termination** — each recursive call strictly reduces the range length, and
   ranges below MIN_RUN do not recurse.
5. **Idempotence** — sorting an already-sorted list leaves it unchanged.

## 9. Failure Modes and Edge Cases

- Empty list: returns immediately, no error.
- Single element: returns immediately.
- All elements equal: must not degrade below O(n log n); stability is trivially
  observable and must hold.
- Elements whose keys are mutually incomparable (e.g. `int` and `str` mixed):
  the underlying `<` raises `TypeError`; the exception propagates. The list may
  be left partially reordered in this case, and this is documented behaviour.
- `key` returning `NaN`: comparisons with NaN are all False, which violates the
  strict weak ordering precondition. Behaviour is undefined but must not hang or
  raise `RecursionError`.
- Very large lists must not exceed the recursion limit; depth is logarithmic.

## 10. Public API

```python
def sort(
    data: list[Any],
    *,
    key: Callable[[Any], Any] = lambda x: x,
    reverse: bool = False,
) -> None:
    """Sort `data` in place. Stable. Raises TypeError on incomparable keys."""

def sorted_copy(
    data: Iterable[Any],
    *,
    key: Callable[[Any], Any] = lambda x: x,
    reverse: bool = False,
) -> list[Any]:
    """Return a new sorted list, leaving the input untouched."""

def is_sorted(
    data: Sequence[Any],
    *,
    key: Callable[[Any], Any] = lambda x: x,
    reverse: bool = False,
) -> bool:
    """Return True if `data` is in non-decreasing (or non-increasing) key order."""
```

## 11. Implementation Notes

- `merge` must never compare an element with itself.
- The temporary buffer should be allocated once per `merge` call, not per
  comparison.
- Do not delegate to the built-in `list.sort` or `sorted`; the point of the
  library is the explicit algorithm.
