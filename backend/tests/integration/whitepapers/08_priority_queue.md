# Binary Min-Heap Priority Queue with Decrease-Key and Stable Tie-Breaking

Python Library Specification

## Abstract

This document specifies a Python library implementing an indexed binary min-heap
priority queue. The heap is an array-encoded complete binary tree; a hash map from
key to array index is kept alongside it so that `decrease_key`, `increase_key` and
`remove` reach an arbitrary entry in O(1) instead of scanning. Equal priorities are
dequeued in insertion order, and re-prioritising an entry does not change its place
in that order.

## 1. Overview and Design Rationale

A plain heap answers "what is the smallest item?" but not "make this already-queued
item smaller", which is what Dijkstra's algorithm, A*, event simulation and job
schedulers need. That requires knowing where each key lives in the array, which
requires an index map updated on **every** element movement. Therein lies the whole
difficulty: array and map must agree after every operation, and one swap that
rewrites the array but forgets the map leaves a key pointing at a stranger —
corruption that surfaces much later as a wrong dequeue order.

## 2. Array Encoding of the Tree

The heap is a Python `list` holding a complete binary tree in breadth-first order,
with no gaps and no sentinel at index 0. For an entry at index `i`:

| Relation | Index |
|---|---|
| parent | `(i - 1) // 2` |
| left child | `2 * i + 1` |
| right child | `2 * i + 2` |

Index `0` is the root and holds the minimum. Tree shape is implicit — array length
alone determines it — so no operation may leave a hole. Internal nodes are exactly
indices `0 .. len(heap) // 2 - 1`.

## 3. Entries and the Total Order

Each admitted key is stored with a priority and a **sequence number** from a counter
starting at 0 and incremented once per successful admission. Entries are ordered
lexicographically by `(priority, sequence)` — a total order, because sequence
numbers are unique.

- A sequence number is assigned on admission (by `push`, or by the constructor in
  iteration order) and is **never** rewritten by `decrease_key` or `increase_key`,
  so a re-prioritised key keeps its original place among equal priorities.
- `remove` discards the number; pushing that key again is a new admission with a
  fresh, larger one, placing it behind any equal-priority key already queued.
- Keys are never compared — only priorities and sequence numbers are — so keys must
  be hashable but need not be orderable.

## 4. The Index Map

`index[key]` is the current array position of that key's entry. The invariant is
mutual: `heap[index[key]].key == key` for every live key, and every array slot's key
appears in the map exactly once. The array is permuted through exactly one
primitive, and that primitive rewrites both map entries:

```
swap(i, j):
    heap[i], heap[j] = heap[j], heap[i]
    index[heap[i].key] = i          # both entries are rewritten,
    index[heap[j].key] = j          # on every single swap
```

Writing an entry into a slot without going through `swap` — filling the hole left by
`pop` or `remove` — must update that slot's map entry in the same step.

## 5. Sift-Up and Sift-Down

```
sift_up(i):
    while i > 0 and heap[i] < heap[(i - 1) // 2]:
        swap(i, (i - 1) // 2); i = (i - 1) // 2

sift_down(i):
    while True:
        l, r, m = 2 * i + 1, 2 * i + 2, i
        if l < len(heap) and heap[l] < heap[m]: m = l
        if r < len(heap) and heap[r] < heap[m]: m = r
        if m == i: break
        swap(i, m); i = m
```

`sift_down` must compare against the *smaller* of the two children; comparing only
the left child, or swapping with the first child that beats the parent, yields an
array that fails the heap property.

## 6. Operations

- **push(key, priority)** — append at index `len(heap)`, record the map entry,
  `sift_up` from there.
- **peek()** — return `heap[0]` as `(key, priority)`. No mutation, no comparison.
- **pop()** — read `heap[0]`, fill the hole per §6.1, return the former root.
- **decrease_key / increase_key** — replace the priority at `index[key]`, keeping the
  sequence number, then `sift_up` / `sift_down` respectively.
- **remove(key)** — fill the hole at `index[key]`, return the removed priority.

Pushing 5, 3, 8, 1 gives `[5]` → `[3, 5]` → `[3, 5, 8]` → `[1, 3, 8, 5]`, the last
push sifting 1 past index 1 and then past the root. Popping from `[1, 3, 8, 5]`
yields 1, moves 5 to the root giving `[5, 3, 8]`, then sifts it past the smaller
child to give `[3, 5, 8]`.

### 6.1 Hole Filling

To delete the entry at index `i`, drop `key` from the map and then:

1. If `i` is the last index, truncate the array and stop. Moving the final element
   onto itself and re-sifting is the classic off-by-one here.
2. Otherwise move the last element into slot `i`, update its map entry, truncate,
   and restore the heap property **in both directions**: `sift_down(i)` and
   `sift_up(i)`. At most one of them moves the element, but which one is not known
   in advance — the element that was last in the array may be smaller than slot
   `i`'s parent. Sifting down only is a correct-looking implementation that leaves
   the array violating §9.1.

## 7. Bulk Construction and Sorting

`PriorityQueue(entries)` loads entries into the array in iteration order, assigning
sequence numbers as it goes, then heapifies bottom-up (Floyd): `sift_down(i)` for
`i` from `len(heap) // 2 - 1` down to `0`. This is O(n); building by n successive
`push` calls is Θ(n log n) on descending input and is not an acceptable constructor.

`heapsort(entries)` builds such a queue and drains it, giving entries in
non-decreasing priority order with ties in input order. For
`[("a",5), ("b",1), ("c",5), ("d",3), ("e",1)]` the result is
`[("b",1), ("e",1), ("d",3), ("a",5), ("c",5)]`.

## 8. Complexity

| Operation | Time | Priority comparisons |
|---|---|---|
| `push`, `decrease_key` | O(log n) | at most ⌈log₂(n+1)⌉ |
| `pop`, `increase_key`, `remove` | O(log n) | at most 2⌊log₂ n⌋ |
| `peek`, `__len__`, `__contains__`, `index_of`, `priority_of` | O(1) | 0 |
| `PriorityQueue(entries)` | O(n) | at most 2n |
| `heapsort` | O(n log n) | — |

Space is O(n) for the array plus O(n) for the map. `index_of` must be a map lookup;
a linear scan would make churn quadratic.

## 9. Correctness Properties

1. **Heap property** — for every `i > 0`, the entry at `(i - 1) // 2` compares less
   than or equal to the entry at `i` under the §3 order.
2. **Index coherence** — the array has exactly `len(queue)` slots and no holes, and
   `index_of(k)` is the array position of `k` for every live key; the map holds no
   key absent from the array and none twice.
3. **Ordering** — draining the queue yields non-decreasing priorities.
4. **Stability** — equal-priority keys are dequeued in admission order, and
   `decrease_key`/`increase_key` preserve the admitting sequence number.
5. **Conservation** — every admitted key is dequeued exactly once unless removed.
6. **Atomicity** — an operation that raises a §10 error leaves the queue exactly as
   it was, including the sequence counter.
7. **Determinism** — the same operation sequence gives the same array layout and the
   same dequeue order on every run.
8. **Construction equivalence** — `PriorityQueue(entries)` and pushing the same
   entries in the same order give the same dequeue sequence.

## 10. Failure Modes and Edge Cases

- Empty queue: `len` is 0, `heap_array()` is `[]`, `check_invariants()` succeeds, and
  `pop()`/`peek()` raise `EmptyQueueError`.
- `remove` of the only key empties the queue; `remove` of the key currently in the
  last array slot must not reinsert it (§6.1 case 1).
- Pushing a key already present raises `DuplicateKeyError` and leaves the queue
  unchanged; so does bulk-loading an `entries` iterable that repeats a key.
- `decrease_key` with a priority that is not strictly smaller raises
  `InvalidPriorityError` — it is not a silent no-op; `increase_key` likewise requires
  a strictly larger priority.
- Any operation naming an absent key raises `MissingKeyError`.
- A NaN priority breaks the total order and is rejected with `InvalidPriorityError`,
  on admission and on re-prioritisation.
- Priorities that are not mutually comparable raise `TypeError` from the underlying
  comparison; the queue may then be left in an unspecified state, and that is
  documented behaviour. An unhashable key also raises `TypeError`.
- Negative and floating-point priorities are ordinary values, not sentinels.
- A queue of 10⁵ entries must behave correctly at logarithmic per-operation cost.

## 11. Public API

```python
class EmptyQueueError(IndexError): ...
class DuplicateKeyError(KeyError): ...
class MissingKeyError(KeyError): ...
class InvalidPriorityError(ValueError): ...
class InvariantError(RuntimeError): ...

class PriorityQueue:
    def __init__(self, entries: Iterable[tuple[Any, Any]] = ()) -> None: ...
    def push(self, key: Any, priority: Any) -> None: ...
    def pop(self) -> tuple[Any, Any]: ...
    def peek(self) -> tuple[Any, Any]: ...
    def decrease_key(self, key: Any, priority: Any) -> None: ...
    def increase_key(self, key: Any, priority: Any) -> None: ...
    def remove(self, key: Any) -> Any: ...     # returns the priority it held
    def priority_of(self, key: Any) -> Any: ...
    def index_of(self, key: Any) -> int: ...   # array index, per the index map
    def heap_array(self) -> list[tuple[Any, Any]]: ...  # copy, (key, priority) pairs
    def check_invariants(self) -> None: ...    # raises InvariantError on §9.1-§9.2
    def __len__(self) -> int: ...
    def __contains__(self, key: Any) -> bool: ...

def heapsort(entries: Iterable[tuple[Any, Any]]) -> list[tuple[Any, Any]]: ...
```

## 12. Implementation Notes

- Do not import or call `heapq` in any form. `heappush`, `heappop`, `heapify`,
  `heapreplace` and `heappushpop` would satisfy the ordering contract while
  implementing none of §2, §4 or §5 — and `heapq` cannot support `decrease_key` at
  all, so a wrapper around it is a different data structure, not a shortcut.
- Do not maintain a sorted list instead: `sorted`, `list.sort` and `bisect` are
  forbidden anywhere in the module — right answers, wrong complexity, no index
  arithmetic.
- Validate before mutating, so §9.6 holds. `heap_array()` returns a copy; mutating
  it must not affect the queue.
