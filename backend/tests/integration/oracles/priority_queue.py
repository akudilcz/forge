"""Oracle for whitepapers/08_priority_queue.md.

Authored from the whitepaper only; never shown to any agent.

An indexed heap has two structures that must agree — the array-encoded tree and
the key-to-index map — and almost every way of getting it wrong stays invisible
for a long time. Popping in the right order does *not* prove the array is a heap:
§6.1's missing sift-up leaves a small element stranded under a large parent, and
a short drain can still come out sorted because `pop` reshuffles the tail. So this
oracle does not settle for observing dequeue order. It reads `heap_array()` and
`index_of()` directly, checks the parent/child arithmetic of §2 slot by slot after
*every* operation of a long mixed workload, and replays 1200 operations against an
independent model of `(priority, admission)` pairs.

The other thing a self-written suite will not do is bound the work: §8 caps bulk
construction at 2n comparisons, which is Floyd's bound. Building the heap with n
successive pushes gives identical answers at Θ(n log n), so the comparison counter
below is the only check that separates the two.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from typing import Any

from backend.tests.integration.oracles._base import Case, ErrorCase, Oracle, Prohibition

# ── shared helpers ───────────────────────────────────────────────────────────


def _guard(check: Callable[[Any], bool]) -> Callable[[Any], bool]:
    """Turn an unexpected raise from a broken build into a clean case failure.

    ``run_oracle`` does not wrap its ``check`` calls, so an implementation that
    raises where the whitepaper says it must not — a stale index map makes
    ``decrease_key`` reject a legitimate priority, for instance — would abort the
    entire oracle run and hide every remaining check behind one traceback.
    """

    def guarded(subject: Any) -> bool:
        try:
            return bool(check(subject))
        except Exception:  # noqa: BLE001 — a raise here is a conformance failure
            return False

    return guarded


_COMPARISONS: dict[str, int] = {"count": 0}


class _CountedPriority:
    """A priority that tallies every rich comparison it takes part in (§8)."""

    __slots__ = ("value",)

    def __init__(self, value: int) -> None:
        self.value = value

    def _tally(self) -> None:
        _COMPARISONS["count"] += 1

    def __lt__(self, other: Any) -> bool:
        self._tally()
        return bool(self.value < other.value)

    def __le__(self, other: Any) -> bool:
        self._tally()
        return bool(self.value <= other.value)

    def __gt__(self, other: Any) -> bool:
        self._tally()
        return bool(self.value > other.value)

    def __ge__(self, other: Any) -> bool:
        self._tally()
        return bool(self.value >= other.value)

    def __eq__(self, other: Any) -> bool:
        self._tally()
        return bool(isinstance(other, _CountedPriority) and self.value == other.value)

    def __hash__(self) -> int:
        return hash(self.value)


def _raises(operation: Any, exc_name: str) -> bool:
    """True when ``operation()`` raises an exception named ``exc_name`` in its MRO."""
    try:
        operation()
    except Exception as exc:  # noqa: BLE001 — the raise is what is being asserted
        return any(klass.__name__ == exc_name for klass in type(exc).__mro__)
    return False


def _array_is_a_heap(pairs: Any) -> bool:
    """§9.1 + §2 — parent priority never exceeds child priority, by index arithmetic."""
    return all(pairs[(i - 1) // 2][1] <= pairs[i][1] for i in range(1, len(pairs)))


def _index_map_agrees_with_array(queue: Any) -> bool:
    """§9.2 — index_of(k) is exactly where k sits, for every slot, with no strays."""
    array = queue.heap_array()
    if len(array) != len(queue):
        return False
    if len({key for key, _ in array}) != len(array):
        return False
    for position, (key, priority) in enumerate(array):
        if queue.index_of(key) != position:
            return False
        if queue.priority_of(key) != priority:
            return False
        if key not in queue:
            return False
    return True


def _drain(queue: Any) -> list[Any]:
    return [queue.pop() for _ in range(len(queue))]


# ── heapsort (§7) ────────────────────────────────────────────────────────────


def _heapsort_matches_a_stable_stdlib_sort(fn: Any) -> bool:
    """§7 — ties in input order is precisely a stable sort by priority.

    The generated module may not call `sorted` (§12); this file may, which makes
    the stdlib a free differential oracle over heavily-tied inputs.
    """
    rng = random.Random(20260812)
    for _ in range(40):
        size = rng.randrange(0, 120)
        entries = [(f"k{i}", rng.randrange(6)) for i in range(size)]
        if fn(entries) != sorted(entries, key=lambda pair: pair[1]):
            return False
    return True


def _heapsort_accepts_any_iterable(fn: Any) -> bool:
    """§11 — the parameter is an Iterable, so a one-shot iterator must work."""
    entries = [("a", 3), ("b", 1), ("c", 2)]
    return bool(fn(iter(entries)) == [("b", 1), ("c", 2), ("a", 3)])


# ── structure: array encoding and the index map (§2, §4, §9.1-§9.2) ──────────


def _empty_queue_is_well_formed(cls: Any) -> bool:
    """§10 — the degenerate queue is valid, not an error."""
    queue = cls()
    queue.check_invariants()
    return bool(len(queue) == 0 and queue.heap_array() == [] and "anything" not in queue)


def _peek_is_the_root_and_does_not_mutate(cls: Any) -> bool:
    """§6 — peek reads slot 0 and changes nothing; index_of confirms the slot."""
    queue = cls([("a", 5), ("b", 1), ("c", 3)])
    peeked = queue.peek()
    if queue.index_of(peeked[0]) != 0 or len(queue) != 3:
        return False
    if queue.peek() != peeked:
        return False
    return bool(queue.pop() == peeked == ("b", 1))


def _heap_array_is_a_copy(cls: Any) -> bool:
    """§12 — mutating the returned list must not reach the queue."""
    queue = cls([("a", 1), ("b", 2), ("c", 3)])
    array = queue.heap_array()
    array.append(("zz", -100))
    array[0] = ("qq", -1)
    return bool(len(queue) == 3 and queue.heap_array()[0] == ("a", 1) and "zz" not in queue)


def _remove_restores_the_heap_in_both_directions(cls: Any) -> bool:
    """§6.1 case 2 — the element filling the hole may have to sift *up*.

    Pushing 0,10,1,11,12,2,3 puts the array in exactly one state, [0,10,1,11,12,2,3]
    (sift-up is deterministic). Removing the entry at index 4 moves the trailing 3
    into a slot whose parent is 10, so an implementation that only sifts down
    strands 3 beneath 10. That drain can still come out sorted — `pop` relocates
    the tail — which is why this reads heap_array() rather than trusting the order.
    """
    queue = cls()
    for key, priority in zip("abcdefg", [0, 10, 1, 11, 12, 2, 3], strict=True):
        queue.push(key, priority)
    if [priority for _, priority in queue.heap_array()] != [0, 10, 1, 11, 12, 2, 3]:
        return False
    if queue.remove("e") != 12:
        return False
    if not _array_is_a_heap(queue.heap_array()) or not _index_map_agrees_with_array(queue):
        return False
    return bool([key for key, _ in _drain(queue)] == list("acfgbd"))


def _degenerate_holes_are_handled(cls: Any) -> bool:
    """§6.1 case 1 / §10 — removing the last slot, and removing the only key."""
    queue = cls([("a", 1), ("b", 2), ("c", 3)])
    last_key = queue.heap_array()[-1][0]
    queue.remove(last_key)
    if len(queue) != 2 or last_key in queue or not _index_map_agrees_with_array(queue):
        return False
    solo = cls([("only", 7)])
    if solo.remove("only") != 7:
        return False
    solo.check_invariants()
    return bool(len(solo) == 0 and solo.heap_array() == [] and "only" not in solo)


def _structure_survives_churn(cls: Any) -> bool:
    """§9.1-§9.2 — array and index map still agree after 800 mixed operations.

    Checked after every single operation, because a map entry that goes stale on
    one swap is repaired by chance on the next one often enough that an end-state
    check misses it.
    """
    rng = random.Random(20260808)
    queue = cls()
    live: list[str] = []
    for step in range(800):
        roll = rng.random()
        if roll < 0.45 or not live:
            key = f"k{step}"
            queue.push(key, rng.randrange(50))
            live.append(key)
        elif roll < 0.62:
            key = rng.choice(live)
            queue.decrease_key(key, queue.priority_of(key) - rng.randrange(1, 6))
        elif roll < 0.76:
            key = rng.choice(live)
            queue.increase_key(key, queue.priority_of(key) + rng.randrange(1, 6))
        elif roll < 0.86:
            key = rng.choice(live)
            queue.remove(key)
            live.remove(key)
        else:
            live.remove(queue.pop()[0])
        if not _array_is_a_heap(queue.heap_array()):
            return False
        if not _index_map_agrees_with_array(queue):
            return False
    queue.check_invariants()
    return bool(len(queue) == len(live))


# ── ordering, stability and the sequence number (§3, §9.3-§9.5) ──────────────


def _equal_priorities_leave_in_admission_order(cls: Any) -> bool:
    """§9.4 — FIFO within a priority class, through the bulk-heapify path."""
    queue = cls([(f"k{i}", i % 3) for i in range(60)])
    order = [key for key, _ in _drain(queue)]
    expected = [f"k{i}" for band in range(3) for i in range(60) if i % 3 == band]
    return bool(order == expected)


def _reprioritising_keeps_the_admission_number(cls: Any) -> bool:
    """§3 — decrease_key/increase_key must not re-stamp the sequence number.

    'a' is admitted before 'b' in both halves, and is then moved onto 'b's
    priority. Keeping the original number leaves 'a' first; issuing a fresh one
    silently reverses the pair.
    """
    lowered = cls([("a", 9), ("b", 5)])
    lowered.decrease_key("a", 5)
    if [key for key, _ in _drain(lowered)] != ["a", "b"]:
        return False
    raised = cls([("a", 1), ("b", 5)])
    raised.increase_key("a", 5)
    return bool([key for key, _ in _drain(raised)] == ["a", "b"])


def _readmission_takes_a_fresh_sequence_number(cls: Any) -> bool:
    """§3 — remove-then-push puts the key behind equal-priority incumbents."""
    queue = cls([("a", 5), ("b", 5)])
    queue.remove("a")
    queue.push("a", 5)
    return bool([key for key, _ in _drain(queue)] == ["b", "a"])


def _dequeue_order_matches_an_independent_model(cls: Any) -> bool:
    """§9.3-§9.5 — 1200 operations replayed against a model of (priority, admission).

    Priorities are drawn from a 12-value range so ties are constant, which is
    where a stale index map or a lost sequence number shows up as the wrong key
    of the right priority.
    """
    rng = random.Random(20260809)
    queue = cls()
    model: dict[str, tuple[int, int]] = {}
    admitted = 0
    for step in range(1200):
        live = list(model)
        roll = rng.random()
        if roll < 0.45 or not live:
            key = f"k{step}"
            priority = rng.randrange(12)
            queue.push(key, priority)
            model[key] = (priority, admitted)
            admitted += 1
        elif roll < 0.60:
            key = rng.choice(live)
            priority, sequence = model[key]
            model[key] = (priority - rng.randrange(1, 4), sequence)
            queue.decrease_key(key, model[key][0])
        elif roll < 0.72:
            key = rng.choice(live)
            priority, sequence = model[key]
            model[key] = (priority + rng.randrange(1, 4), sequence)
            queue.increase_key(key, model[key][0])
        elif roll < 0.80:
            key = rng.choice(live)
            del model[key]
            queue.remove(key)
        else:
            expected = min(model, key=lambda candidate: model[candidate])
            if tuple(queue.pop()) != (expected, model[expected][0]):
                return False
            del model[expected]
        if len(queue) != len(model):
            return False
    remaining = [key for key, _ in _drain(queue)]
    return bool(remaining == sorted(model, key=lambda candidate: model[candidate]))


def _large_queue_dequeues_in_order(cls: Any) -> bool:
    """§10 — 20,000 entries still come out non-decreasing and fully accounted for."""
    rng = random.Random(20260811)
    queue = cls([(f"k{i}", rng.randrange(1_000_000)) for i in range(20_000)])
    previous = -1
    seen = 0
    while len(queue) > 0:
        _, priority = queue.pop()
        if priority < previous:
            return False
        previous = priority
        seen += 1
    return bool(seen == 20_000)


# ── construction, determinism and atomicity (§7, §9.6-§9.8) ──────────────────


def _bulk_construction_matches_successive_pushes(cls: Any) -> bool:
    """§9.8 — heapify and n pushes agree with each other and with a stable sort."""
    rng = random.Random(20260810)
    entries = [(f"k{i}", rng.randrange(15)) for i in range(200)]
    bulk = cls(entries)
    pushed = cls()
    for key, priority in entries:
        pushed.push(key, priority)
    expected = sorted(entries, key=lambda pair: pair[1])
    return bool(_drain(bulk) == _drain(pushed) == expected)


def _bulk_construction_stays_within_the_comparison_bound(cls: Any) -> bool:
    """§8 — the constructor is Floyd's O(n) heapify, capped at 2n comparisons.

    Priorities descend, which is the input on which repeated sift-up insertion is
    at its worst: every element becomes the new minimum and climbs to the root,
    costing sum(floor(log2 i)) ≈ 90,000 comparisons for n = 8192. Floyd's
    bottom-up pass costs at most 2n = 16,384. The budget below is 10n, which is
    roomy for an implementation comparing packed (priority, sequence) tuples —
    that invokes __eq__ and then __lt__ per comparison, so ~5n in total — while
    still excluding a constructor that merely pushes n times, measured here at
    ~23n.
    """
    size = 8192
    entries = [(f"k{i}", _CountedPriority(size - i)) for i in range(size)]
    _COMPARISONS["count"] = 0
    queue = cls(entries)
    used = _COMPARISONS["count"]
    if len(queue) != size:
        return False
    if queue.peek()[0] != f"k{size - 1}":
        return False
    return bool(used <= 10 * size)


def _identical_operation_sequences_agree(cls: Any) -> bool:
    """§9.7 — no dependence on set iteration or hash ordering."""

    def build() -> Any:
        queue = cls([(f"k{i}", (i * 37) % 23) for i in range(120)])
        for i in range(0, 120, 5):
            queue.decrease_key(f"k{i}", -1 - i)
        for i in range(1, 120, 7):
            queue.increase_key(f"k{i}", 500 + i)
        for i in range(2, 120, 11):
            queue.remove(f"k{i}")
        return queue

    first, second = build(), build()
    if first.heap_array() != second.heap_array():
        return False
    return bool(_drain(first) == _drain(second))


def _failed_operations_leave_the_queue_untouched(cls: Any) -> bool:
    """§9.6 — every documented rejection validates before it mutates."""
    queue = cls([("a", 1), ("b", 2), ("c", 3)])
    before = queue.heap_array()
    rejected: list[Any] = [
        lambda: queue.push("a", 0),
        lambda: queue.push("z", float("nan")),
        lambda: queue.decrease_key("a", 1),
        lambda: queue.decrease_key("a", 4),
        lambda: queue.increase_key("a", 1),
        lambda: queue.increase_key("a", 0),
        lambda: queue.decrease_key("absent", 0),
        lambda: queue.remove("absent"),
    ]
    for operation in rejected:
        try:
            operation()
        except Exception:  # noqa: BLE001 — every one of these must raise
            continue
        return False
    if queue.heap_array() != before or len(queue) != 3:
        return False
    return bool(_drain(queue) == [("a", 1), ("b", 2), ("c", 3)])


def _documented_errors_use_the_named_classes(cls: Any) -> bool:
    """§10/§11 — each documented failure raises its own named exception."""
    empty = cls()
    queue = cls([("a", 5), ("b", 9)])
    expectations: list[tuple[Any, str]] = [
        (empty.pop, "EmptyQueueError"),
        (empty.peek, "EmptyQueueError"),
        (lambda: queue.push("a", 1), "DuplicateKeyError"),
        (lambda: queue.push("z", float("nan")), "InvalidPriorityError"),
        (lambda: queue.decrease_key("a", 5), "InvalidPriorityError"),
        (lambda: queue.increase_key("a", 5), "InvalidPriorityError"),
        (lambda: queue.decrease_key("absent", 0), "MissingKeyError"),
        (lambda: queue.increase_key("absent", 99), "MissingKeyError"),
        (lambda: queue.remove("absent"), "MissingKeyError"),
        (lambda: queue.index_of("absent"), "MissingKeyError"),
        (lambda: queue.priority_of("absent"), "MissingKeyError"),
    ]
    return all(_raises(operation, name) for operation, name in expectations)


ORACLE = Oracle(
    whitepaper="08_priority_queue.md",
    package_hint="queue",
    required_names=[
        "PriorityQueue",
        "heapsort",
        "EmptyQueueError",
        "DuplicateKeyError",
        "MissingKeyError",
        "InvalidPriorityError",
        "InvariantError",
    ],
    cases=[
        # §7 — worked examples, stated verbatim in the whitepaper.
        Case(
            target="heapsort",
            args=([],),
            expected=[],
            description="§10 heapsort of no entries is empty",
        ),
        Case(
            target="heapsort",
            args=([("a", 5), ("b", 1), ("c", 5), ("d", 3), ("e", 1)],),
            expected=[("b", 1), ("e", 1), ("d", 3), ("a", 5), ("c", 5)],
            description="§7 worked heapsort example, ties in input order",
        ),
        Case(
            target="heapsort",
            check=_guard(_heapsort_matches_a_stable_stdlib_sort),
            call=False,
            description="§7 heapsort matches a stable stdlib sort over 40 tied inputs",
        ),
        Case(
            target="heapsort",
            check=_guard(_heapsort_accepts_any_iterable),
            call=False,
            description="§11 heapsort accepts a one-shot iterator",
        ),
        # §2/§4 — the array encoding and the index map.
        Case(
            target="PriorityQueue",
            call=False,
            check=_guard(_empty_queue_is_well_formed),
            description="§10 empty queue is valid, not an error",
        ),
        Case(
            target="PriorityQueue",
            call=False,
            check=_guard(_peek_is_the_root_and_does_not_mutate),
            description="§6 peek reads slot 0 and mutates nothing",
        ),
        Case(
            target="PriorityQueue",
            call=False,
            check=_guard(_heap_array_is_a_copy),
            description="§12 heap_array() returns a copy",
        ),
        Case(
            target="PriorityQueue",
            call=False,
            check=_guard(_remove_restores_the_heap_in_both_directions),
            description="§6.1 removal sifts the replacement up as well as down",
        ),
        Case(
            target="PriorityQueue",
            call=False,
            check=_guard(_degenerate_holes_are_handled),
            description="§6.1 removing the last slot and the only key",
        ),
        Case(
            target="PriorityQueue",
            call=False,
            check=_guard(_structure_survives_churn),
            description="§9.1-§9.2 heap property and index map hold after every one "
            "of 800 mixed operations",
        ),
        # §3/§9 — ordering, stability and the sequence number.
        Case(
            target="PriorityQueue",
            call=False,
            check=_guard(_equal_priorities_leave_in_admission_order),
            description="§9.4 equal priorities dequeue FIFO through bulk heapify",
        ),
        Case(
            target="PriorityQueue",
            call=False,
            check=_guard(_reprioritising_keeps_the_admission_number),
            description="§3 decrease_key/increase_key keep the admitting sequence number",
        ),
        Case(
            target="PriorityQueue",
            call=False,
            check=_guard(_readmission_takes_a_fresh_sequence_number),
            description="§3 remove-then-push re-admits behind equal-priority incumbents",
        ),
        Case(
            target="PriorityQueue",
            call=False,
            check=_guard(_dequeue_order_matches_an_independent_model),
            description="§9.3-§9.5 1200 churned operations match a (priority, admission) model",
        ),
        Case(
            target="PriorityQueue",
            call=False,
            check=_guard(_large_queue_dequeues_in_order),
            description="§10 20,000 entries dequeue non-decreasing",
        ),
        # §7-§9 — construction, determinism, atomicity.
        Case(
            target="PriorityQueue",
            call=False,
            check=_guard(_bulk_construction_matches_successive_pushes),
            description="§9.8 heapify and successive pushes give the same dequeue sequence",
        ),
        Case(
            target="PriorityQueue",
            call=False,
            check=_guard(_bulk_construction_stays_within_the_comparison_bound),
            description="§8 constructor stays inside Floyd's comparison budget "
            "(n pushes would blow it)",
        ),
        Case(
            target="PriorityQueue",
            call=False,
            check=_guard(_identical_operation_sequences_agree),
            description="§9.7 identical operation sequences give identical layouts",
        ),
        Case(
            target="PriorityQueue",
            call=False,
            check=_guard(_failed_operations_leave_the_queue_untouched),
            description="§9.6 a rejected operation does not mutate the queue",
        ),
        Case(
            target="PriorityQueue",
            call=False,
            check=_guard(_documented_errors_use_the_named_classes),
            description="§10 every documented failure raises its own named exception",
        ),
    ],
    error_cases=[
        ErrorCase(
            target="PriorityQueue",
            args=([("a", 1), ("a", 2)],),
            exc_name="DuplicateKeyError",
            description="§10 duplicate key in the constructor raises DuplicateKeyError",
        ),
        ErrorCase(
            target="heapsort",
            args=([("a", 1), ("b", 2), ("a", 3)],),
            exc_name="DuplicateKeyError",
            description="§10 duplicate key in heapsort raises DuplicateKeyError",
        ),
        ErrorCase(
            target="heapsort",
            args=([("a", float("nan")), ("b", 1.0)],),
            exc_name="InvalidPriorityError",
            description="§10 a NaN priority is rejected, not silently ordered",
        ),
        ErrorCase(
            target="PriorityQueue",
            args=([("a", 1), ("b", "x"), ("c", 2)],),
            exc_name="TypeError",
            description="§10 mutually incomparable priorities raise TypeError",
        ),
        ErrorCase(
            target="PriorityQueue",
            args=([([1, 2], 1)],),
            exc_name="TypeError",
            description="§10 an unhashable key raises TypeError",
        ),
    ],
    prohibitions=[
        Prohibition(
            reason=(
                "§12 forbids heapq — it satisfies the ordering contract while "
                "implementing none of the array encoding, the index map or the "
                "sift routines, and it cannot support decrease_key at all"
            ),
            imports=("heapq",),
            name_calls=("heappush", "heappop", "heapreplace", "heappushpop"),
            attr_calls=("heappush", "heappop", "heapreplace", "heappushpop"),
        ),
        Prohibition(
            reason=(
                "§12 forbids keeping a sorted list instead of a heap — right "
                "answers, wrong complexity, and no index arithmetic"
            ),
            imports=("bisect",),
            name_calls=("sorted",),
            attr_calls=("sort",),
        ),
    ],
)
