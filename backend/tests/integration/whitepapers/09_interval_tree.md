# Half-Open Interval Algebra, Overlap Indexing, and a Sweep-Line Scheduler

Python Library Specification

## Abstract

This document specifies a Python library for reasoning about intervals on the
real line: coalescing ranges, answering overlap queries against a mutable index,
and scheduling ranges into the fewest conflict-free lanes. Every interval is
**half-open**, `[start, end)`. The whole difficulty lies in the distinction
between intervals that *overlap* (share a point) and those that merely *touch*
(one ends exactly where the next begins) — a single `<` versus `<=`, pulling in
opposite directions. An implementation that conflates them looks right, is
self-consistent, and is wrong.

## 1. The Half-Open Convention

`[s, e)` denotes `{x : s <= x < e}`. Four consequences everything below depends on:

1. `e` is **not** a member of `[s, e)`; a point query at `e` must not return it.
2. `[1, 3)` and `[3, 5)` have empty intersection — they do **not** overlap.
3. `[1, 3)` and `[3, 5)` have contiguous union `[1, 5)` — they **do** merge.
4. `e <= s` denotes the empty set, which is not representable. No operation may
   produce one: a result that would be empty is omitted from the output instead.

Points 2 and 3 are not in conflict. They are why `overlaps` and `mergeable_with`
must be two separate predicates.

## 2. The Interval Type

`Interval` subclasses `collections.namedtuple("Interval", ["start", "end"])` and
overrides `__new__` to validate — the `collections` form, not
`typing.NamedTuple`, which forbids overriding `__new__`. Instances are therefore
hashable, equal to the plain tuple `(start, end)`, and ordered lexicographically.
Validation, in order: each endpoint is an `int` or `float`, else `TypeError`;
neither is NaN, else `ValueError`; `start < end`, else `ValueError`.

`as_interval(value)` returns an `Interval` unchanged, converts any other 2-item
sequence via `Interval(value[0], value[1])`, and raises `ValueError` otherwise.
**Every public entry point accepts either form**; `IntervalLike` below means
"whatever `as_interval` accepts".

### 2.1 Boundary Predicates

For `A = [1, 3)` the predicates take exactly these values, and all three are
symmetric in their arguments:

| B | `A.overlaps(B)` | `A.touches(B)` | `A.mergeable_with(B)` | `A.intersection(B)` |
|---|---|---|---|---|
| `[3, 5)` | False | True | True | `None` |
| `[-1, 1)` | False | True | True | `None` |
| `[2, 5)` | True | False | True | `[2, 3)` |
| `[4, 6)` | False | False | False | `None` |
| `[1, 3)` | True | False | True | `[1, 3)` |
| `[0, 9)` | True | False | True | `[1, 3)` |

- `overlaps`: `self.start < other.end and other.start < self.end` — strict
- `mergeable_with`: `self.start <= other.end and other.start <= self.end` — loose
- `touches`: `self.end == other.start or other.end == self.start`
- `contains_point(x)`: `self.start <= x < self.end`; `length`: `end - start`
- `intersection`: `Interval(max(starts), min(ends))` if non-empty, else `None`
- `union`: `Interval(min(starts), max(ends))`, but `ValueError` when
  `not mergeable_with(other)` — the union of separated intervals is not one

## 3. Merging and Set Algebra

`merge_intervals(intervals)` returns the canonical form: ascending, pairwise
non-overlapping **and** non-touching, so a positive gap separates each member
from the next.

```
merge_intervals(xs):
    out = []
    for iv in sorted(as_interval(x) for x in xs):    # lexicographic (start, end)
        if out and iv.start <= out[-1].end:          # <= : touching coalesces (§1.3)
            out[-1] = Interval(out[-1].start, max(out[-1].end, iv.end))
        else:
            out.append(iv)
    return out
```

The `max(...)` is required: sorting by `(start, end)` does not order the ends, so
`[(1, 10), (2, 3)]` merges to `[(1, 10)]`, not `[(1, 3)]`.

`intersect_all(a, b)` and `subtract_all(a, b)` normalise both arguments with
`merge_intervals`, sweep the two lists linearly, and return the normalised union
of `a` intersected with — or minus — the union of `b`. Boundaries follow §2.1: a
member of `b` that merely touches a member of `a` contributes nothing to the
intersection and removes nothing in the subtraction, and adjacent subtrahends
such as `[3, 5)` and `[5, 7)` leave no zero-width sliver behind at 5.

## 4. The Interval Index

`IntervalIndex` is a mutable multiset supporting overlap queries: a binary search
tree keyed by `(start, end, seq)`, where `seq` is a monotonically increasing
insertion counter that totalises the key and lets equal intervals be stored more
than once. Each node caches `max_end`, the maximum `end` over its subtree,
maintained on every insertion and deletion.

```
query_point(x):                                 # out = [], visit(root), return out
    visit(node):
        if node is None or node.max_end <= x:   # <= : an interval ending at x
            return                              # does not contain x (§1.1)
        self.nodes_visited += 1
        visit(node.left)
        if node.interval.start <= x < node.interval.end:
            out.append(node.interval)
        if node.interval.start <= x:            # else every right key starts > x
            visit(node.right)
```

`query_range(start, end)` is the same descent with the strict test of §2.1: prune
on `node.max_end <= start`, keep when
`node.interval.start < end and start < node.interval.end`, stop descending right
when `node.interval.start >= end`. A merely touching interval is not a result;
the range is itself an interval and is validated as one. In-order traversal makes
both result lists ascending, so output depends only on what is stored.

`nodes_visited` is a public integer, initialised to 0, incremented once per node
whose stored interval a query inspects; subtrees eliminated by the `max_end` test
or the `start` bound must not contribute, and the index never resets it. It makes
pruning observable — a query that touches every node is a correct-looking
implementation of the wrong algorithm.

`remove(interval)` deletes exactly one node matching `(start, end)` (copies are
indistinguishable, so which one is unspecified) and raises `KeyError` when none
matches. `check_invariants()` raises `AssertionError` if BST ordering or any
node's `max_end` is wrong.

## 5. Sweep Line: Maximum Concurrency

`max_concurrency(intervals)` returns `(count, witness)`. Build a `+1` event at
each `start` and a `-1` at each `end`, then **apply every event at a coordinate
before reading the running count** — equivalently, sort by `(coordinate, delta)`
so `-1` precedes `+1`. This is what makes `[1, 3)` and `[3, 5)` report a maximum
of 1 rather than 2: at coordinate 3 the first interval has already ended.

After applying all deltas at coordinate `c`, if the running count *strictly*
exceeds the best so far, record it with witness `Interval(c, c')` where `c'` is
the next event coordinate. Strict `>` makes the witness the leftmost
maximal-concurrency region, never extended across an intermediate coordinate even
where the count is unchanged. Duplicates count separately: `[[0, 1), [0, 1)]` has
concurrency 2.

## 6. Lane Partitioning

`partition_into_lanes(intervals)` assigns every interval, duplicates included, to
a lane in which no two intervals overlap. Process in ascending `(start, end)`
order; place each into the **lowest-indexed** lane whose last interval satisfies
`last.end <= iv.start` — touching may share a lane, overlapping may not — and
open a new lane only when none qualifies. Lanes are returned in index order, each
ascending. Greedy colouring of an interval graph is optimal, so the lane count
equals the count from `max_concurrency`: an independent cross-check on both.

## 7. Complexity

| Operation | Time | Space |
|---|---|---|
| `merge_intervals`, `max_concurrency` | O(n log n) | O(n) |
| `intersect_all`, `subtract_all` | O((n + m) log (n + m)) | O(n + m) |
| `IntervalIndex.add` / `remove` | O(h), h = height | O(1) |
| `query_point` / `query_range` | O(h + k), k = results | O(k) |
| `partition_into_lanes` | O(n log n + n·L), L = lanes | O(n) |

Queries must be output-sensitive — `O(h + k)`, not `O(n)`. Random insertion order
gives `h = O(log n)`; no rebalancing is required, but `add` and `remove` must not
recurse to a depth that risks `RecursionError` on sorted input.

## 8. Correctness Properties

1. **Canonical form** — merged output is ascending, pairwise non-overlapping and
   pairwise non-touching.
2. **Union preservation** — a point lies in some input interval iff it lies in
   some output interval of `merge_intervals`.
3. **Idempotence** — `merge_intervals(merge_intervals(xs)) == merge_intervals(xs)`.
4. **Order independence** — every function depends only on the multiset of its
   inputs; index queries only on what is stored, not the order it arrived or left.
5. **No empty results** — no returned interval has `end <= start`.
6. **Query exactness** — `query_point(x)` returns, ascending and with
   multiplicity, exactly the stored intervals with `start <= x < end`;
   `query_range(s, e)` exactly those overlapping `[s, e)` in the strict sense.
7. **Index invariants** — after any sequence of `add` and `remove`, BST ordering
   holds and every node's `max_end` is the maximum `end` in its subtree.
8. **Partition identity** — `intersect_all(a, b)` and `subtract_all(a, b)`
   partition `merge_intervals(a)`: their union merges back to it, their
   intersection is empty.
9. **Lane optimality** — `len(partition_into_lanes(xs)) == max_concurrency(xs)[0]`
   for non-empty `xs`, each lane is internally non-overlapping, and the lanes
   together contain every input exactly once.
10. **Pruning** — a point query does not inspect every node of the index.

## 9. Failure Modes and Edge Cases

- `Interval(3, 3)`, `Interval(5, 1)` and a NaN endpoint raise `ValueError`; a
  non-numeric endpoint raises `TypeError`. `as_interval` raises `ValueError` on
  anything that is not an `Interval` or a 2-item sequence — a scalar, a 3-tuple.
- `Interval.union` raises `ValueError` on separated intervals, succeeds on
  touching ones.
- Empty input: `merge_intervals([]) == []` and `partition_into_lanes([]) == []`,
  but `max_concurrency([])` raises `ValueError` — there is no interval over which
  to report a maximum.
- `IntervalIndex.remove` of an absent interval raises `KeyError`; removing one of
  two identical stored intervals leaves the other queryable. `query_range(5, 5)`
  raises `ValueError`. A point below every `start`, or at or above every `end`,
  returns `[]`.
- Nesting (`[0, 100)` containing `[40, 41)`) must not truncate the enclosing
  interval when merging, and a point query inside the inner one must return both.
- Float endpoints are supported throughout; nothing may assume integer endpoints
  or enumerate the points of an interval.

## 10. Public API

```python
class Interval(namedtuple("Interval", ["start", "end"])):   # §2: validating __new__
    length: float                                           # property, end - start
    def contains_point(self, x: float) -> bool: ...
    def overlaps(self, other: IntervalLike) -> bool: ...
    def touches(self, other: IntervalLike) -> bool: ...
    def mergeable_with(self, other: IntervalLike) -> bool: ...
    def intersection(self, other: IntervalLike) -> Interval | None: ...
    def union(self, other: IntervalLike) -> Interval: ...

def as_interval(value: IntervalLike) -> Interval: ...
def merge_intervals(intervals: Iterable[IntervalLike]) -> list[Interval]: ...
def intersect_all(a: Iterable[IntervalLike], b: Iterable[IntervalLike]) -> list[Interval]: ...
def subtract_all(a: Iterable[IntervalLike], b: Iterable[IntervalLike]) -> list[Interval]: ...
def max_concurrency(intervals: Iterable[IntervalLike]) -> tuple[int, Interval]: ...
def partition_into_lanes(intervals: Iterable[IntervalLike]) -> list[list[Interval]]: ...

class IntervalIndex:
    nodes_visited: int
    def __init__(self, intervals: Iterable[IntervalLike] = ()) -> None: ...
    def add(self, interval: IntervalLike) -> Interval: ...
    def remove(self, interval: IntervalLike) -> None: ...       # KeyError if absent
    def query_point(self, x: float) -> list[Interval]: ...
    def query_range(self, start: float, end: float) -> list[Interval]: ...
    def check_invariants(self) -> None: ...
    def __len__(self) -> int: ...
    def __iter__(self) -> Iterator[Interval]: ...               # ascending
    def __contains__(self, interval: object) -> bool: ...
```

## 11. Implementation Notes

- **Do not delegate to any interval library.** `intervaltree`, `portion`,
  `pyinterval`, `interlap`, `ncls`, `pyranges` and `quicksect` implement this
  specification already, as does `pandas.IntervalIndex(closed="left")` with
  `numpy`. Importing any of them, or `sortedcontainers`, reduces the deliverable
  to a wrapper that passes every functional test while implementing none of the
  boundary logic that is the point of the library. Do not name your own modules
  after those packages either. `sorted`, `heapq` and `bisect` are permitted — the
  prohibition is on interval semantics, not on sorting.
- Write `overlaps` and `mergeable_with` as two distinct functions and call the
  right one at each site: §3 needs the loose comparison, every query path the
  strict one. Deriving one from the other with a fudge factor (`end + epsilon`)
  is wrong for float endpoints.
- Never construct an interval to represent an empty result; return `None` or omit
  it, so the constructor's validation stays a genuine invariant.
