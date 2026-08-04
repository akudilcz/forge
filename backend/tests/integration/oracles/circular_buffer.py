"""Oracle for whitepapers/14_circular_buffer.md.

Authored from the whitepaper only; never shown to any agent.

A circular buffer is easy to get *almost* right, and the near misses are exactly
the ones a self-written test suite does not probe: an implementation that never
wraps looks perfect, so the checks here force the head through every offset
(§8.4), drain a full buffer so that `head == tail` twice with opposite meanings
(§3.1), and diff several hundred mixed operations against a naive list model.

The other high-value checks are the ones that a *functionally* correct but
structurally lazy implementation fails: a sacrificed-slot design (§3.2) loses one
element of capacity, a `deque` wrapper implements none of §2, and an
`O(capacity)` push (`_storage.pop(0)`) satisfies every functional case in this
file while violating the complexity table of §7.
"""

from __future__ import annotations

import gc
import random
import time
import weakref
from typing import Any

from backend.tests.integration.oracles._base import Case, ErrorCase, Oracle, Prohibition


def _raises(thunk: Any, exc_name: str) -> bool:
    """True if calling ``thunk()`` raises an exception named ``exc_name``.

    Matched up the MRO by name, so the generated exception class need not be
    imported and a richer hierarchy than §10's still satisfies the check.
    """
    try:
        thunk()
    except Exception as exc:  # noqa: BLE001 — we are asserting on the raise
        return any(klass.__name__ == exc_name for klass in type(exc).__mro__)
    return False


# ── §8.4 / §8.5: worked behaviour ────────────────────────────────────────────


def _fifo_then_window(cls: Any) -> bool:
    """§8.3 + §8.5 — FIFO order, then the sliding window once full."""
    buf = cls(3)
    if [buf.push(v) for v in (1, 2, 3)] != [False, False, False]:
        return False
    if buf.to_list() != [1, 2, 3] or len(buf) != 3 or not buf.is_full:
        return False
    if buf.push(4) is not True:  # a bool, not the evicted element (§4.1)
        return False
    if buf.to_list() != [2, 3, 4] or len(buf) != 3:
        return False
    if [buf.pop(), buf.pop()] != [2, 3]:
        return False
    buf.check_invariants()
    return bool(buf.to_list() == [4] and buf.peek() == 4 and buf.peek_back() == 4)


def _capacity_is_exact(cls: Any) -> bool:
    """§3.3 + §8.2 — capacity N holds N elements, not N-1.

    This is the check a sacrificed-slot design (§3.2) fails: it evicts on the
    N-th push rather than the (N+1)-th, and for N = 1 it can hold nothing.
    """
    for n in (1, 2, 3, 8, 17):
        buf = cls(n)
        for i in range(n):
            if buf.push(i) is not False:  # nothing may be evicted yet
                return False
            if buf.is_full is not (len(buf) == n):
                return False
        if len(buf) != n or not buf.is_full or buf.is_empty:
            return False
        if buf.to_list() != list(range(n)):
            return False
        if buf.push(n) is not True or len(buf) != n:
            return False
        if buf.to_list() != list(range(1, n + 1)):
            return False
        buf.check_invariants()
    return True


def _full_and_empty_are_distinguishable(cls: Any) -> bool:
    """§3.1 — the central ambiguity: `head == tail` in both states.

    After six pushes into a capacity-4 buffer the head sits at 2 and the tail
    has wrapped onto it: full. Draining leaves the head at 2 with the tail
    again on top of it: empty. A head/tail-only representation reports the same
    answer for both.
    """
    buf = cls(4)
    for i in range(6):
        buf.push(i)
    if not buf.is_full or buf.is_empty or len(buf) != 4:
        return False
    if buf.to_list() != [2, 3, 4, 5]:
        return False
    if [buf.pop() for _ in range(4)] != [2, 3, 4, 5]:
        return False
    if not buf.is_empty or buf.is_full or len(buf) != 0 or buf.to_list() != []:
        return False
    if not _raises(buf.pop, "BufferEmptyError"):
        return False
    buf.push("x")  # still usable after a wrap
    buf.check_invariants()
    return bool(buf.to_list() == ["x"] and len(buf) == 1 and buf[0] == "x")


def _rotation_invariance(cls: Any) -> bool:
    """§8.4 — behaviour is identical at every head offset.

    Drives the head to each of the `capacity` possible offsets and asserts that
    to_list, iteration, positive and negative indexing, and `in` all agree. An
    off-by-one in the modular arithmetic survives offset 0 and dies here.
    """
    cap = 5
    expect = list(range(cap))
    for offset in range(cap):
        buf = cls(cap)
        for i in range(offset):
            buf.push(-1 - i)
        for _ in range(offset):
            buf.pop()
        for value in expect:
            buf.push(value)
        if buf.to_list() != expect or list(buf) != expect:
            return False
        if [buf[i] for i in range(cap)] != expect:
            return False
        if [buf[-i] for i in range(1, cap + 1)] != expect[::-1]:
            return False
        if any(value not in buf for value in expect):
            return False
        if -1 in buf:  # §5 — a popped element must not linger in a vacant slot
            return False
        buf.push(99)
        if buf.to_list() != [*expect[1:], 99]:
            return False
        buf.check_invariants()
    return True


def _long_window(cls: Any) -> bool:
    """§8.5 — pushing 50 elements into a capacity-7 buffer keeps the last 7."""
    buf = cls(7)
    sequence = list(range(50))
    for value in sequence:
        buf.push(value)
    return bool(buf.to_list() == sequence[-7:] and buf[0] == 43 and buf[-1] == 49)


def _push_front_and_pop_back_wrap(cls: Any) -> bool:
    """§4 — the oldest-end insert wraps backwards and evicts the NEWEST.

    `(_head - 1) % cap` is the arithmetic most often written as a clamped
    decrement, which breaks the instant the head is 0.
    """
    buf = cls(4)
    for value in (1, 2, 3):
        buf.push(value)
    if buf.push_front(0) is not False or buf.to_list() != [0, 1, 2, 3]:
        return False
    if buf.push_front(-1) is not True:
        return False
    if buf.to_list() != [-1, 0, 1, 2]:  # 3, the newest, was displaced
        return False
    if [buf.pop_back(), buf.pop_back()] != [2, 1]:
        return False
    if buf.to_list() != [-1, 0] or buf.peek() != -1 or buf.peek_back() != 0:
        return False
    buf.check_invariants()
    return True


def _capacity_one(cls: Any) -> bool:
    """§9 — the degenerate capacity that a sacrificed-slot design cannot serve."""
    buf = cls(1)
    if buf.push("a") is not False or not buf.is_full or buf.to_list() != ["a"]:
        return False
    if buf.push("b") is not True or buf.to_list() != ["b"] or len(buf) != 1:
        return False
    if buf.push_front("c") is not True or buf.to_list() != ["c"]:
        return False
    if buf.pop() != "c" or not buf.is_empty or buf.is_full:
        return False
    strict = cls(1, overwrite=False)
    strict.push("only")
    if not _raises(lambda: strict.push("nope"), "BufferFullError"):
        return False
    buf.check_invariants()
    return bool(strict.to_list() == ["only"])


# ── §9: overwrite policy and refusal ─────────────────────────────────────────


def _overwrite_false_refuses_without_mutating(cls: Any) -> bool:
    """§9 — a refused push leaves the buffer *completely* unmodified.

    An implementation that advances the head and then raises is the failure this
    catches; it looks correct until the caller inspects the buffer afterwards.
    """
    buf = cls(3, overwrite=False)
    for value in (1, 2, 3):
        if buf.push(value) is not False:
            return False
    if not _raises(lambda: buf.push(4), "BufferFullError"):
        return False
    if buf.to_list() != [1, 2, 3] or len(buf) != 3:
        return False
    if not _raises(lambda: buf.push_front(0), "BufferFullError"):
        return False
    if buf.to_list() != [1, 2, 3] or len(buf) != 3:
        return False
    if buf.pop() != 1:
        return False
    buf.check_invariants()
    return bool(buf.push(4) is False and buf.to_list() == [2, 3, 4])


def _extend_reports_evictions_in_order(cls: Any) -> bool:
    """§4 — extend returns evicted elements in eviction order and is not atomic."""
    buf = cls(3)
    evicted = buf.extend(iter(range(10)))  # an iterator, not a list
    if list(evicted) != [0, 1, 2, 3, 4, 5, 6] or buf.to_list() != [7, 8, 9]:
        return False
    strict = cls(3, overwrite=False)
    if not _raises(lambda: strict.extend([1, 2, 3, 4, 5]), "BufferFullError"):
        return False
    if strict.to_list() != [1, 2, 3]:  # §4: elements consumed before the raise stay
        return False
    return bool(list(cls(4).extend([])) == [])


# ── §5 / §6: vacancy, iteration, indexing ────────────────────────────────────


def _stale_slots_are_invisible(cls: Any) -> bool:
    """§5 + §8.6 — popped elements must not be reachable through the buffer.

    An implementation that answers `in` by scanning `_storage`, or that forgets
    to blank the vacated slot, reports ghosts here.
    """
    buf = cls(4)
    for value in ("a", "b", "c", "d"):
        buf.push(value)
    for _ in range(3):
        buf.pop()
    if buf.to_list() != ["d"] or len(buf) != 1 or list(buf) != ["d"]:
        return False
    if any(ghost in buf for ghost in ("a", "b", "c")):
        return False
    if not _raises(lambda: buf[1], "IndexError"):
        return False
    buf.check_invariants()
    return True


def _none_is_a_storable_element(cls: Any) -> bool:
    """§5 + §9 — `None` is data, so the vacancy sentinel cannot be `None`."""
    buf = cls(3)
    buf.push(None)
    buf.push(1)
    if len(buf) != 2 or buf.to_list() != [None, 1] or list(buf) != [None, 1]:
        return False
    if None not in buf or buf.peek() is not None or buf[0] is not None:
        return False
    if buf.pop() is not None or buf.to_list() != [1]:
        return False
    if None in buf:  # the slot just vacated must not read as a stored None
        return False
    buf.check_invariants()
    return True


def _pop_releases_the_reference(cls: Any) -> bool:
    """§5 — a vacated slot must not keep the element alive.

    The failure is invisible functionally and fatal in the streaming workloads
    §1 names, so it is checked directly with a weak reference.
    """

    class _Tracked:
        pass

    buf = cls(4)
    obj = _Tracked()
    ref = weakref.ref(obj)
    buf.push(obj)
    popped = buf.pop()
    if popped is not obj:
        return False
    del obj, popped
    gc.collect()
    if ref() is not None:
        return False

    displaced = _Tracked()
    ref_displaced = weakref.ref(displaced)
    small = cls(1)
    small.push(displaced)
    del displaced
    small.push("replacement")  # overwrite policy displaces the tracked object
    gc.collect()
    return bool(ref_displaced() is None and small.to_list() == ["replacement"])


def _iteration_is_lazy_and_fails_fast(cls: Any) -> bool:
    """§6.2 + §9 — mutation during iteration raises RuntimeError.

    An implementation whose `__iter__` snapshots into a list yields stale
    elements silently instead, which is the behaviour §6.2 forbids.
    """
    buf = cls(4)
    for value in (1, 2, 3):
        buf.push(value)
    iterator = iter(buf)
    if next(iterator) != 1:
        return False
    buf.push(4)
    try:
        next(iterator)
    except RuntimeError:
        pass
    else:
        return False
    return bool(buf.to_list() == [1, 2, 3, 4] and list(buf) == [1, 2, 3, 4])


def _index_bounds_and_types(cls: Any) -> bool:
    """§6.1 — negative indexing, both out-of-range ends, and non-int indices."""
    buf = cls(4)
    for value in (10, 20, 30):
        buf.push(value)
    if [buf[0], buf[1], buf[2]] != [10, 20, 30]:
        return False
    if [buf[-1], buf[-2], buf[-3]] != [30, 20, 10]:
        return False
    if not _raises(lambda: buf[3], "IndexError"):
        return False
    if not _raises(lambda: buf[-4], "IndexError"):
        return False
    if not _raises(lambda: buf[0:2], "TypeError"):
        return False
    if not _raises(lambda: buf["0"], "TypeError"):
        return False
    return _raises(lambda: cls(2)[0], "IndexError")


def _peek_does_not_remove(cls: Any) -> bool:
    """§4 — peek is a read; on an empty buffer it raises rather than returning None."""
    buf = cls(3)
    buf.push(1)
    buf.push(2)
    if buf.peek() != 1 or buf.peek_back() != 2 or len(buf) != 2:
        return False
    if buf.to_list() != [1, 2]:
        return False
    empty = cls(2)
    return bool(
        _raises(empty.peek, "BufferEmptyError")
        and _raises(empty.peek_back, "BufferEmptyError")
        and _raises(empty.pop_back, "BufferEmptyError")
    )


def _clear_resets_and_buffer_stays_usable(cls: Any) -> bool:
    """§4 + §9 — clear from a wrapped state, then keep using the buffer."""
    buf = cls(3)
    for value in range(5):
        buf.push(value)  # wrapped: the head is no longer 0
    buf.clear()
    if len(buf) != 0 or not buf.is_empty or buf.is_full or buf.capacity != 3:
        return False
    if buf.to_list() != [] or list(buf) != []:
        return False
    if not _raises(buf.pop, "BufferEmptyError"):
        return False
    for label in ("x", "y", "z", "w"):
        buf.push(label)
    buf.check_invariants()
    return bool(buf.to_list() == ["y", "z", "w"])


# ── §7 / §8.8: properties no single call exhibits ────────────────────────────


class _ModelEmptyError(Exception):
    pass


class _ModelFullError(Exception):
    pass


class _Model:
    """A deliberately naive list-backed model of §2-§4 semantics.

    Wrong complexity, right behaviour — which is exactly what an independent
    oracle wants: the generated buffer must agree with it on every observable.
    """

    def __init__(self, capacity: int, overwrite: bool) -> None:
        self.capacity = capacity
        self.overwrite = overwrite
        self.items: list[Any] = []

    def push(self, item: Any) -> bool:
        if len(self.items) == self.capacity:
            if not self.overwrite:
                raise _ModelFullError
            self.items.pop(0)
            self.items.append(item)
            return True
        self.items.append(item)
        return False

    def push_front(self, item: Any) -> bool:
        if len(self.items) == self.capacity:
            if not self.overwrite:
                raise _ModelFullError
            self.items.pop()
            self.items.insert(0, item)
            return True
        self.items.insert(0, item)
        return False

    def pop(self) -> Any:
        if not self.items:
            raise _ModelEmptyError
        return self.items.pop(0)

    def pop_back(self) -> Any:
        if not self.items:
            raise _ModelEmptyError
        return self.items.pop()

    def peek(self) -> Any:
        if not self.items:
            raise _ModelEmptyError
        return self.items[0]

    def peek_back(self) -> Any:
        if not self.items:
            raise _ModelEmptyError
        return self.items[-1]


def _outcome(thunk: Any, *args: Any) -> tuple[str, Any]:
    """Normalise a call into a comparable outcome, collapsing error identities."""
    try:
        return ("ok", thunk(*args))
    except Exception as exc:  # noqa: BLE001 — the raise is part of the outcome
        names = {klass.__name__ for klass in type(exc).__mro__}
        if names & {"BufferEmptyError", "_ModelEmptyError"}:
            return ("raise", "empty")
        if names & {"BufferFullError", "_ModelFullError"}:
            return ("raise", "full")
        return ("raise", f"unexpected:{type(exc).__name__}")


def _differential_against_naive_model(cls: Any) -> bool:
    """§8 — several hundred mixed operations must agree with the model exactly.

    This is the check that no single call reveals: it compares return values,
    raised errors, contents, length, both occupancy predicates, iteration and
    indexing after *every* operation, across five capacity/policy combinations.
    """
    rng = random.Random(20260803)
    operations = ("push", "push", "push", "push_front", "pop", "pop_back", "peek", "peek_back")
    for capacity, overwrite in ((1, True), (2, True), (3, False), (5, True), (8, False)):
        buf = cls(capacity, overwrite=overwrite)
        model = _Model(capacity, overwrite)
        for _ in range(400):
            op = rng.choice(operations)
            args = (rng.randrange(1000),) if op.startswith("push") else ()
            if _outcome(getattr(buf, op), *args) != _outcome(getattr(model, op), *args):
                return False
            if buf.to_list() != model.items or list(buf) != model.items:
                return False
            if len(buf) != len(model.items):
                return False
            if buf.is_full is not (len(model.items) == capacity):
                return False
            if buf.is_empty is not (not model.items):
                return False
            if [buf[i] for i in range(len(model.items))] != model.items:
                return False
        buf.check_invariants()
    return True


def _script(cls: Any, seed: int) -> list[Any]:
    rng = random.Random(seed)
    buf = cls(6)
    trace: list[Any] = []
    for _ in range(300):
        op = rng.choice(("push", "push_front", "pop", "pop_back"))
        args = (rng.randrange(100),) if op.startswith("push") else ()
        trace.append(_outcome(getattr(buf, op), *args))
        trace.append(tuple(buf.to_list()))
    return trace


def _is_deterministic(cls: Any) -> bool:
    """§8.8 — the same operation sequence yields the same results every run."""
    first = _script(cls, 11)
    return bool(first and first == _script(cls, 11) and first != _script(cls, 12))


def _invariants_hold_under_churn(cls: Any) -> bool:
    """§8.1 + §8.6 — 2000 mixed operations leave the structure consistent."""
    rng = random.Random(20260814)
    buf = cls(9)
    for i in range(2000):
        roll = rng.random()
        if roll < 0.40:
            buf.push(i)
        elif roll < 0.60:
            buf.push_front(i)
        elif roll < 0.80 and not buf.is_empty:
            buf.pop()
        elif not buf.is_empty:
            buf.pop_back()
        if len(buf) > 9 or len(buf) != len(buf.to_list()):
            return False
    buf.check_invariants()
    return True


def _timed_pushes(cls: Any, capacity: int, pushes: int) -> tuple[float, Any]:
    buf = cls(capacity)
    started = time.perf_counter()
    for i in range(pushes):
        buf.push(i)
    return time.perf_counter() - started, buf


def _push_is_constant_time(cls: Any) -> bool:
    """§7 — O(1) push, which `_storage.pop(0)` and slice-shifting are not.

    An absolute time budget would be both machine-dependent and too generous:
    `list.pop(0)` is a memmove, and on a 100k-slot buffer it costs only ~10us.
    So this compares the *same* push count at two capacities three orders of
    magnitude apart. A genuinely O(1) push is insensitive to capacity; an
    O(capacity) one is 30-100x slower on the large buffer. The floor keeps a
    momentarily busy machine from failing a correct implementation.
    """
    pushes = 500_000
    small_elapsed, _ = _timed_pushes(cls, 64, pushes)
    large_elapsed, buf = _timed_pushes(cls, 100_000, pushes)
    if large_elapsed > max(0.6, 8.0 * small_elapsed):
        return False
    return bool(
        len(buf) == 100_000 and buf[0] == pushes - 100_000 and buf[-1] == pushes - 1
    )


# ── §10: the documented exception hierarchy ──────────────────────────────────


def _empty_error_hierarchy(exc: Any) -> bool:
    """§10 — BufferEmptyError is both a CircularBufferError and an IndexError."""
    if not isinstance(exc, type):
        return False
    names = {klass.__name__ for klass in exc.__mro__}
    return bool(issubclass(exc, IndexError) and "CircularBufferError" in names)


def _full_error_hierarchy(exc: Any) -> bool:
    """§10 — BufferFullError shares the base but is NOT an IndexError."""
    if not isinstance(exc, type):
        return False
    names = {klass.__name__ for klass in exc.__mro__}
    return bool(
        issubclass(exc, Exception)
        and not issubclass(exc, IndexError)
        and "CircularBufferError" in names
    )


def _from_iterable_builds_a_window(factory: Any) -> bool:
    """§10 — the module-level constructor honours capacity, policy and order."""
    windowed = factory(range(10), 4)
    if windowed.to_list() != [6, 7, 8, 9] or windowed.capacity != 4:
        return False
    if len(windowed) != 4 or not windowed.is_full or windowed.overwrite is not True:
        return False
    exact = factory([1, 2], 5)
    if exact.to_list() != [1, 2] or exact.is_full:
        return False
    strict = factory([1, 2, 3], 3, overwrite=False)
    if strict.to_list() != [1, 2, 3] or strict.overwrite is not False:
        return False
    empty = factory([], 2)
    return bool(empty.is_empty and empty.capacity == 2 and list(empty) == [])


ORACLE = Oracle(
    whitepaper="14_circular_buffer.md",
    package_hint="buffer",
    required_names=[
        "CircularBuffer",
        "from_iterable",
        "CircularBufferError",
        "BufferEmptyError",
        "BufferFullError",
    ],
    cases=[
        Case(
            target="CircularBuffer",
            call=False,
            check=_fifo_then_window,
            description="§8.3 FIFO order, then the sliding window once full",
        ),
        Case(
            target="CircularBuffer",
            call=False,
            check=_capacity_is_exact,
            description="§3.3 capacity N holds N elements (defeats the sacrificed slot)",
        ),
        Case(
            target="CircularBuffer",
            call=False,
            check=_full_and_empty_are_distinguishable,
            description="§3.1 full and empty are distinguished when head == tail",
        ),
        Case(
            target="CircularBuffer",
            call=False,
            check=_rotation_invariance,
            description="§8.4 identical behaviour at every head offset",
        ),
        Case(
            target="CircularBuffer",
            call=False,
            check=_long_window,
            description="§8.5 50 pushes into capacity 7 keep the last 7",
        ),
        Case(
            target="CircularBuffer",
            call=False,
            check=_push_front_and_pop_back_wrap,
            description="§4 push_front wraps backwards and evicts the newest",
        ),
        Case(
            target="CircularBuffer",
            call=False,
            check=_capacity_one,
            description="§9 capacity 1 is legal and evicts on every push",
        ),
        Case(
            target="CircularBuffer",
            call=False,
            check=_overwrite_false_refuses_without_mutating,
            description="§9 a refused push leaves the buffer unmodified",
        ),
        Case(
            target="CircularBuffer",
            call=False,
            check=_extend_reports_evictions_in_order,
            description="§4 extend returns evictions in order and is non-atomic",
        ),
        Case(
            target="CircularBuffer",
            call=False,
            check=_stale_slots_are_invisible,
            description="§5 popped elements are not reachable through the buffer",
        ),
        Case(
            target="CircularBuffer",
            call=False,
            check=_none_is_a_storable_element,
            description="§5 None is data, so the vacancy sentinel is not None",
        ),
        Case(
            target="CircularBuffer",
            call=False,
            check=_pop_releases_the_reference,
            description="§5 a vacated slot releases its reference",
        ),
        Case(
            target="CircularBuffer",
            call=False,
            check=_iteration_is_lazy_and_fails_fast,
            description="§6.2 mutation during iteration raises RuntimeError",
        ),
        Case(
            target="CircularBuffer",
            call=False,
            check=_index_bounds_and_types,
            description="§6.1 negative indexing, out-of-range and non-int indices",
        ),
        Case(
            target="CircularBuffer",
            call=False,
            check=_peek_does_not_remove,
            description="§4 peek reads without removing and raises when empty",
        ),
        Case(
            target="CircularBuffer",
            call=False,
            check=_clear_resets_and_buffer_stays_usable,
            description="§4 clear from a wrapped state leaves a usable buffer",
        ),
        Case(
            target="CircularBuffer",
            call=False,
            check=_differential_against_naive_model,
            description="§8 400 mixed operations agree with a naive list model",
        ),
        Case(
            target="CircularBuffer",
            call=False,
            check=_is_deterministic,
            description="§8.8 identical operation sequences give identical results",
        ),
        Case(
            target="CircularBuffer",
            call=False,
            check=_invariants_hold_under_churn,
            description="§8.6 invariants survive 2000 mixed operations",
        ),
        Case(
            target="CircularBuffer",
            call=False,
            check=_push_is_constant_time,
            description="§7 push is O(1), not O(capacity)",
        ),
        Case(
            target="BufferEmptyError",
            call=False,
            check=_empty_error_hierarchy,
            description="§10 BufferEmptyError is a CircularBufferError and an IndexError",
        ),
        Case(
            target="BufferFullError",
            call=False,
            check=_full_error_hierarchy,
            description="§10 BufferFullError shares the base but is not an IndexError",
        ),
        Case(
            target="from_iterable",
            call=False,
            check=_from_iterable_builds_a_window,
            description="§10 from_iterable honours capacity, order and policy",
        ),
    ],
    error_cases=[
        ErrorCase(
            target="CircularBuffer",
            args=(0,),
            exc_name="ValueError",
            description="§9 capacity 0 raises ValueError",
        ),
        ErrorCase(
            target="CircularBuffer",
            args=(-4,),
            exc_name="ValueError",
            description="§9 negative capacity raises ValueError",
        ),
        ErrorCase(
            target="CircularBuffer",
            args=(2.5,),
            exc_name="TypeError",
            description="§9 non-integer capacity raises TypeError",
        ),
        ErrorCase(
            target="CircularBuffer",
            args=("4",),
            exc_name="TypeError",
            description="§9 string capacity raises TypeError",
        ),
        ErrorCase(
            target="CircularBuffer",
            args=(True,),
            exc_name="TypeError",
            description="§9 bool capacity is rejected despite subclassing int",
        ),
        ErrorCase(
            target="from_iterable",
            args=([], 0),
            exc_name="ValueError",
            description="§10 from_iterable validates capacity",
        ),
        ErrorCase(
            target="from_iterable",
            args=([1, 2, 3], 2),
            kwargs={"overwrite": False},
            exc_name="BufferFullError",
            description="§10 from_iterable propagates BufferFullError",
        ),
    ],
    prohibitions=[
        Prohibition(
            reason=(
                "§11 forbids collections.deque and queue.Queue — a "
                "deque(maxlen=capacity) wrapper satisfies every functional "
                "requirement while implementing none of the index arithmetic of "
                "§2 or the occupancy representation of §3, which are the "
                "deliverable. `popleft`/`appendleft` are listed because they "
                "exist only on deque, so they betray a delegation that was "
                "imported under an alias; `collections` itself is not listed "
                "because §11 expects `collections.abc` for the annotations"
            ),
            imports=("queue",),
            name_calls=("deque", "Queue"),
            attr_calls=("deque", "popleft", "appendleft"),
        ),
    ],
)
