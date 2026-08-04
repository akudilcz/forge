# Fixed-Capacity LRU Cache with O(1) Operations and Eviction Callbacks

Python Library Specification

## Abstract

This document specifies a Python library implementing a fixed-capacity Least
Recently Used (LRU) cache. All primary operations — get, put, delete, and
membership test — run in O(1) expected time. The cache is backed by a hash map
from key to node, plus an intrusive doubly linked list that maintains recency
order. Eviction of the least recently used entry occurs automatically when a
`put` would exceed capacity, and may notify a caller-supplied callback.

## 1. Overview and Design Rationale

A cache must answer two questions in constant time: "where is the value for key
k?" and "which entry is least recently used?". A hash map answers the first; a
doubly linked list ordered by recency answers the second. The two structures
share node objects, so moving an entry to the most-recent position is a pointer
rewrite rather than a search.

The list is maintained with sentinel head and tail nodes. Sentinels remove all
null-checking from the splice operations, which is the usual source of bugs in
linked-list code.

- The node immediately after `head` is the MOST recently used.
- The node immediately before `tail` is the LEAST recently used, and is the
  eviction victim.

## 2. Data Structures

### 2.1 Node

Each node holds: `key`, `value`, `prev`, `next`. Sentinel nodes hold no
meaningful key or value and are never returned to callers or evicted.

### 2.2 Invariants

The following must hold after every public operation:

1. `len(map) == number of non-sentinel nodes in the list`.
2. `len(map) <= capacity`.
3. The list is acyclic and doubly consistent: for every node n reachable from
   head, `n.next.prev is n` and `n.prev.next is n`.
4. Every key in the map maps to a node that is currently linked into the list.
5. `head.prev is None` and `tail.next is None`.

Property 3 is the one that breaks first when splice logic is wrong, so it should
be directly testable via an internal consistency check.

## 3. Recency Semantics

An entry becomes the most recently used when:

- `get(key)` returns a hit.
- `put(key, value)` inserts a new entry.
- `put(key, value)` overwrites an existing entry.

An entry's recency is NOT changed by:

- `get(key)` missing (no entry exists).
- `contains(key)` / `__contains__` — membership testing is explicitly a
  non-touching read. This is a deliberate departure from `get` and must be
  covered by tests.
- `peek(key)`, which returns the value without promoting it.
- Iterating the cache.

## 4. Operations

### 4.1 get(key, default=None)

1. If key not in map, increment `misses` and return `default`.
2. Otherwise unlink the node and splice it directly after `head`.
3. Increment `hits` and return `node.value`.

### 4.2 put(key, value)

1. If key already in map: update `node.value`, move node after `head`, return.
   Capacity is not consulted, and no eviction occurs.
2. Otherwise create a node, insert into the map, splice after `head`.
3. If `len(map) > capacity`: take the node before `tail`, unlink it, delete its
   key from the map, increment `evictions`, and invoke `on_evict(key, value)` if
   a callback was supplied.

Exactly one entry is evicted per overflowing `put`, never zero and never two.

### 4.3 delete(key)

Remove the entry if present and return True; return False if absent. Deletion
does not fire the eviction callback — the callback signals capacity pressure, not
explicit removal.

### 4.4 clear()

Remove all entries, reset the list to just the two sentinels, and reset `hits`,
`misses`, and `evictions` to zero. The eviction callback is not fired.

## 5. Capacity

Capacity is fixed at construction and must be a positive integer.

- `capacity < 1` raises `ValueError` at construction.
- A non-integer capacity raises `TypeError`.
- Capacity of exactly 1 is legal and is an important edge case: every `put` of a
  new key evicts the previous entry.

## 6. Statistics

The cache exposes counters `hits`, `misses`, `evictions`, and a derived
`hit_rate` property equal to `hits / (hits + misses)`. When no lookups have
occurred, `hit_rate` returns 0.0 rather than raising ZeroDivisionError.

## 7. Iteration

`keys()`, `values()`, and `items()` yield entries in recency order, most recent
first. Iteration does not alter recency. Mutating the cache during iteration
raises `RuntimeError`, mirroring the behaviour of Python's own dict.

## 8. Complexity

| Operation | Time | Space |
|---|---|---|
| get | O(1) expected | O(1) |
| put | O(1) expected | O(1) |
| delete | O(1) expected | O(1) |
| contains / peek | O(1) expected | O(1) |
| clear | O(n) | O(1) |
| iteration | O(n) | O(1) |

Total space is O(capacity).

## 9. Correctness Properties

1. **Bounded size** — `len(cache) <= capacity` always.
2. **LRU order** — after any sequence of operations, the eviction victim is the
   entry whose most recent touching operation (per §3) is oldest.
3. **Value fidelity** — a `get` on a key that has not been evicted or deleted
   returns exactly the value most recently `put` for that key.
4. **Structural consistency** — the invariants of §2.2 hold after every operation.
5. **Callback exactness** — `on_evict` fires once per eviction, with the evicted
   key and value, and never for `delete` or `clear`.

## 10. Failure Modes and Edge Cases

- Capacity 1: every new key evicts the prior one.
- Re-putting an existing key at full capacity must NOT evict anything.
- Unhashable key (e.g. a list): raises `TypeError`, and the cache is left
  unmodified — no partial insertion.
- `None` as a legitimate value: `get` must distinguish "stored None" from "miss".
  This is why `get` takes an explicit `default` and why `contains` exists.
- A callback that itself raises: the exception propagates, but the cache must
  already be in a consistent state (entry fully removed) before the callback is
  invoked.
- Deleting a key that was already evicted returns False, not an error.

## 11. Public API

```python
class LRUCache:
    def __init__(
        self,
        capacity: int,
        *,
        on_evict: Callable[[Any, Any], None] | None = None,
    ) -> None: ...

    def get(self, key: Any, default: Any = None) -> Any: ...
    def peek(self, key: Any, default: Any = None) -> Any: ...
    def put(self, key: Any, value: Any) -> None: ...
    def delete(self, key: Any) -> bool: ...
    def clear(self) -> None: ...

    def keys(self) -> Iterator[Any]: ...
    def values(self) -> Iterator[Any]: ...
    def items(self) -> Iterator[tuple[Any, Any]]: ...

    def __len__(self) -> int: ...
    def __contains__(self, key: Any) -> bool: ...

    @property
    def capacity(self) -> int: ...
    @property
    def hit_rate(self) -> float: ...

    def check_invariants(self) -> None:
        """Raise AssertionError if any structural invariant of §2.2 is violated."""
```

## 12. Implementation Notes

- Do not use `collections.OrderedDict` or `functools.lru_cache`; the explicit
  hash-map-plus-linked-list construction is the subject of the specification.
- Splice helpers `_unlink(node)` and `_push_front(node)` should be the only code
  that mutates pointers, so the invariant surface stays small.
