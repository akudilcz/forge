# Fixed-Capacity Circular Buffer with Overwrite Policy and Iteration

Python Library Specification

## Abstract

This document specifies a Python library implementing a fixed-capacity circular
(ring) buffer. Storage is a single list allocated once at construction and never
resized; elements are addressed by modular arithmetic against a moving head
index. Push, pop, peek and indexed access run in O(1) time with no allocation.
When the buffer is full, a configurable overwrite policy either discards the
element at the opposite end or refuses the write. Iteration yields elements in
logical order, oldest first.

## 1. Overview and Design Rationale

A circular buffer is the standard structure for bounded streaming work:
telemetry windows, audio frames, retry queues. Its appeal is that the memory
footprint is decided once and never changes, so no operation may append to,
insert into, slice or otherwise resize the backing store. The whole difficulty
therefore sits in two places — the modular arithmetic mapping logical positions
onto physical slots (§2), and the representation of occupancy (§3) — and both
are specified exhaustively below because both are where implementations go
wrong.

## 2. Storage Model and Index Arithmetic

| Name | Meaning |
|---|---|
| `_storage` | list of exactly `capacity` slots, allocated once |
| `_head` | physical index of the OLDEST live element |
| `_count` | number of live elements, `0 <= _count <= capacity` |

Logical order is oldest first, and logical index `i` maps to the physical slot
`physical(i) = (_head + i) % capacity` for `0 <= i < _count`. The next free slot
at the back — the "tail" — is `physical(_count)`; it is derived, never stored.

All index motion is modular. Moving the head backwards uses
`(_head - 1) % capacity`, which in Python yields `capacity - 1` when `_head` is
0. An implementation that writes `_head - 1` and clamps at zero, or that
special-cases the wrap in one place and uses `%` in another, passes every test
that never wraps and fails the moment the buffer does.

## 3. The Full-versus-Empty Ambiguity

### 3.1 The problem

The classical implementation stores `_head` and `_tail` and derives occupancy
from their relationship. That representation is defective: after `capacity`
pushes the tail has wrapped exactly once and `_tail == _head`, which is also the
state after zero pushes. Full and empty become indistinguishable, and every
predicate built on the comparison — `is_empty`, `is_full`, `__len__`, the
eviction test in `push` — is wrong in one of the two states.

### 3.2 Rejected resolutions

- **Sacrifice a slot** so the buffer never physically fills. This makes
  `capacity` a lie: a buffer built with capacity 4 holds 3 elements, and one of
  capacity 1 holds nothing. Rejected.
- **Mirror bit**: widen the indices by one bit and compare the extra bit.
  Correct, but only when `capacity` is a power of two. Rejected.

### 3.3 Mandated resolution

The buffer stores an explicit `_count`, making occupancy exact and independent of
the index relationship: `is_empty` is `_count == 0`, `is_full` is
`_count == capacity`, `len(buffer)` is `_count`. A buffer of capacity N accepts
exactly N elements before any eviction, for every N >= 1 including N = 1. The
physical state `_head == physical(_count)` arises in both the empty and the full
case and is never consulted.

## 4. Operations

Let `cap` abbreviate `capacity`. Each mutating operation increments a private
modification counter `_version` (§6.2). `EMPTY` is the vacancy sentinel of §5.

```
push(item):                        # append at the newest end
    if _count == cap:
        if not overwrite: raise BufferFullError
        _storage[_head] = item     # when full, the oldest slot IS the tail slot
        _head = (_head + 1) % cap
        return True                # an element was evicted
    _storage[(_head + _count) % cap] = item
    _count += 1
    return False

push_front(item):                  # insert at the oldest end
    full = (_count == cap)
    if full and not overwrite: raise BufferFullError
    _head = (_head - 1) % cap      # when full this slot holds the NEWEST
    _storage[_head] = item         # element, so push_front evicts the newest
    if not full: _count += 1
    return full                    # True iff an element was evicted

pop():                             # remove and return the oldest
    if _count == 0: raise BufferEmptyError
    item, _storage[_head] = _storage[_head], EMPTY
    _head = (_head + 1) % cap
    _count -= 1
    return item

pop_back():                        # remove and return the newest
    if _count == 0: raise BufferEmptyError
    idx = (_head + _count - 1) % cap
    item, _storage[idx] = _storage[idx], EMPTY
    _count -= 1
    return item
```

`peek()` and `peek_back()` return what `pop()` and `pop_back()` would return
without removing it, and raise `BufferEmptyError` when empty. `extend(items)`
pushes each element of an iterable in order and returns the evicted elements in
eviction order; it is not atomic, so under `overwrite=False` the elements
consumed before the buffer filled remain in it when `BufferFullError` is raised.
`clear()` writes `EMPTY` into every slot and sets `_count` and `_head` to 0,
without reallocating `_storage` or changing `capacity`.

### 4.1 Why `push` returns a bool rather than the evicted element

`None` is a legitimate element, so a caller could not distinguish "evicted
`None`" from "evicted nothing". `push` and `push_front` therefore return `True`
if and only if an element was evicted; callers needing the evicted values use
`extend`, whose list return type has no such ambiguity.

## 5. Vacant Slots and Reference Release

Vacant slots hold a module-private sentinel that is **not** `None`, because
`None` is storable. Every removal writes that sentinel back into the vacated
slot. This is not cosmetic: a slot still holding a popped element keeps that
object alive for the lifetime of the buffer, and makes `__contains__` report
elements the buffer no longer holds.

## 6. Indexing and Iteration

`buffer[i]` returns the element at logical index `i`, 0 being the oldest.
Negative indices count back from the newest: `buffer[-1]` is the newest,
`buffer[-len(buffer)]` the oldest. An index outside `[-_count, _count)` raises
`IndexError`; a non-integer index, including a slice, raises `TypeError`.

`__iter__` yields live elements oldest first without copying the buffer. It
captures `_version` on entry and, before yielding each element, raises
`RuntimeError` if `_version` has changed — mirroring `dict` when mutated during
iteration. `to_list()` returns the same sequence as a new list and never raises
`RuntimeError`. `item in buffer` is equivalent to
`any(e is item or e == item for e in buffer)` over live elements only.

## 7. Complexity

| Operation | Time | Space |
|---|---|---|
| push / push_front | O(1) | O(1) |
| pop / pop_back | O(1) | O(1) |
| peek / peek_back / `buffer[i]` | O(1) | O(1) |
| `in` / iteration | O(n) | O(1) |
| to_list | O(n) | O(n) |
| clear | O(capacity) | O(1) |

Total space is O(capacity), fixed at construction. The O(1) bounds are required,
not aspirational: `_storage.pop(0)` and `_storage = _storage[1:] + [item]` yield
a functionally correct buffer with O(capacity) pushes, and this table excludes
them.

## 8. Correctness Properties

1. **Bounded size** — `0 <= len(buffer) <= capacity` after every operation.
2. **Exact capacity** — a buffer of capacity N accepts N pushes with no eviction;
   the (N+1)-th evicts exactly one element.
3. **FIFO order** — with `push` and `pop` alone, elements leave in arrival order.
4. **Rotation invariance** — observable behaviour does not depend on `_head`. Two
   buffers holding equal elements agree on `to_list`, indexing, iteration and
   `in` whatever their head offsets.
5. **Window property** — after pushing M >= capacity elements into an empty
   buffer with `overwrite=True`, `to_list()` equals the last `capacity` elements
   of that sequence, in order.
6. **Slot exclusivity** — every physical slot not equal to `physical(i)` for some
   `0 <= i < _count` holds the vacancy sentinel.
7. **Iteration agreement** — `list(buffer) == to_list() == [buffer[i] for i in
   range(len(buffer))]`.
8. **Determinism** — an identical sequence of operations yields identical results
   and an identical final `to_list()`.

## 9. Failure Modes and Edge Cases

- Capacity 1: legal, and the case a sacrificed-slot design fails. Every push
  after the first evicts the previous element.
- Empty buffer: `pop`, `pop_back`, `peek`, `peek_back` raise `BufferEmptyError`.
- Full buffer with `overwrite=False`: `push` and `push_front` raise
  `BufferFullError` and leave the buffer completely unmodified.
- `capacity < 1` raises `ValueError`; a non-`int` capacity raises `TypeError`,
  and `bool` is rejected as a capacity despite being an `int` subclass, because
  `CircularBuffer(True)` is always a mistake.
- Storing `None` is legal: `len`, `in`, indexing and iteration treat it exactly
  like any other element.
- Emptying a full buffer returns it to the empty state even though `_head` did
  not return to 0; the buffer stays usable across any number of wraps.
- Mutating during iteration raises `RuntimeError`, rather than silently skipping
  or duplicating elements.

## 10. Public API

```python
class CircularBufferError(Exception): ...
class BufferEmptyError(CircularBufferError, IndexError): ...
class BufferFullError(CircularBufferError): ...


class CircularBuffer:
    def __init__(self, capacity: int, *, overwrite: bool = True) -> None: ...

    def push(self, item: Any) -> bool: ...
    def push_front(self, item: Any) -> bool: ...
    def pop(self) -> Any: ...
    def pop_back(self) -> Any: ...
    def peek(self) -> Any: ...
    def peek_back(self) -> Any: ...
    def extend(self, items: Iterable[Any]) -> list[Any]: ...
    def clear(self) -> None: ...
    def to_list(self) -> list[Any]: ...
    def check_invariants(self) -> None: ...   # AssertionError if §2/§3.3/§8.6 fails

    def __len__(self) -> int: ...
    def __iter__(self) -> Iterator[Any]: ...
    def __getitem__(self, index: int) -> Any: ...
    def __contains__(self, item: Any) -> bool: ...


def from_iterable(
    items: Iterable[Any], capacity: int, *, overwrite: bool = True
) -> CircularBuffer: ...
```

`CircularBuffer` also exposes four read-only properties: `capacity: int`,
`overwrite: bool`, `is_full: bool` and `is_empty: bool`.

## 11. Implementation Notes

- Do not delegate to `collections.deque` or to `queue.Queue`. A
  `deque(maxlen=capacity)` wrapper satisfies every functional requirement here
  while implementing none of §2 or §3, which are the subject of the
  specification. Importing `collections.abc` for the `Iterable` and `Iterator`
  annotations is expected and permitted; importing `deque` under any name, or
  importing `queue`, is not.
- The backing list is allocated exactly once, in `__init__`. No operation may
  call `append`, `insert`, `pop`, `remove` or slice-assignment on it; assignment
  to an existing index is the only permitted mutation.
- Route every physical index through a single helper implementing `physical(i)`,
  so the modular arithmetic exists in exactly one place.
- A `__repr__` showing capacity and contents is recommended for debuggability but
  is not part of the tested contract.
