"""Oracle for whitepapers/09_interval_tree.md.

Authored from the whitepaper only; never shown to any agent.

Everything here turns on one distinction: intervals that *touch* (``[1, 3)`` and
``[3, 5)``) versus intervals that *overlap*. They differ by a single ``<`` versus
``<=``, and the two relations pull in opposite directions — touching intervals
merge but do not overlap. An implementation that picks one comparison and uses it
everywhere is self-consistent, passes any test suite written from the same
misreading, and is wrong. So the centre of gravity of this oracle is the boundary
matrix of §2.1 and the differential checks that compare against brute-force point
coverage the generated code cannot shortcut.

Section references point at the whitepaper clause each check enforces.
"""

from __future__ import annotations

import functools
import random
from collections.abc import Callable
from typing import Any

from backend.tests.integration.oracles._base import Case, ErrorCase, Oracle, Prohibition

_WORKED: list[tuple[int, int]] = [(0, 3), (1, 4), (3, 5), (6, 8), (7, 9), (8, 10)]
"""§3 worked example. Touching pairs at 3 and at 8, one nested overlap at 1."""


def _safe(check: Callable[[Any], bool]) -> Callable[[Any], bool]:
    """Turn a raise inside a check into a rejection.

    ``run_oracle`` invokes ``check`` outside the try/except that guards the call
    itself, so an exception escaping a ``call=False`` check aborts the entire
    oracle run rather than failing one case. Several checks here provoke raises
    on purpose — a wrong implementation constructs an empty interval and trips
    its own validation — and every one of those must be reported, not fatal.
    """

    @functools.wraps(check)
    def wrapper(value: Any) -> bool:
        try:
            return bool(check(value))
        except Exception:  # noqa: BLE001 — any raise means the check did not hold
            return False

    return wrapper


def _pairs(value: Any) -> list[tuple[Any, Any]]:
    """Normalise a returned interval sequence to plain ``(start, end)`` tuples."""
    return [(iv[0], iv[1]) for iv in value]


def _random_intervals(rng: random.Random, count: int, span: int, width: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for _ in range(count):
        start = rng.randrange(0, span)
        out.append((start, start + rng.randrange(1, width)))
    return out


def _samples(span: int) -> list[float]:
    """Half-integer probes; interval endpoints are integers in these tests, so a
    half-step grid distinguishes ``[1, 3) ∪ [4, 6)`` from ``[1, 6)``."""
    return [i / 2 for i in range(-2, span * 2 + 4)]


def _covers(data: list[tuple[Any, Any]], x: float) -> bool:
    return any(start <= x < end for start, end in data)


def _is_canonical(got: list[tuple[Any, Any]]) -> bool:
    """§8.1 / §8.5 — ascending, non-empty members, positive gaps between them."""
    if any(start >= end for start, end in got):
        return False
    return all(prev_end < start for (_, prev_end), (start, _) in zip(got, got[1:], strict=False))


def _true_max_concurrency(data: list[tuple[int, int]]) -> tuple[int, tuple[int, int] | None]:
    """Trusted §5 sweep, computed independently of the generated code.

    Concurrency is constant on ``[c_i, c_i+1)`` for consecutive event coordinates,
    so evaluating at each coordinate finds the maximum, and the first coordinate
    attaining it is the leftmost witness.
    """
    coords = sorted({c for pair in data for c in pair})
    best, at = 0, -1
    for index, coord in enumerate(coords):
        count = sum(1 for start, end in data if start <= coord < end)
        if count > best:
            best, at = count, index
    if at < 0:
        return 0, None
    return best, (coords[at], coords[at + 1])


# ── §2 the Interval type ─────────────────────────────────────────────────────


@_safe
def _predicate_matrix(interval_cls: Any) -> bool:
    """§2.1 — the whole specification in one table, for A = [1, 3).

    ``overlaps`` strict, ``mergeable_with`` loose, ``touches`` an equality on the
    shared endpoint. A single implementation reused for all three fails a row.
    """
    a = interval_cls(1, 3)
    table: list[tuple[tuple[int, int], bool, bool, bool, tuple[int, int] | None]] = [
        ((3, 5), False, True, True, None),
        ((-1, 1), False, True, True, None),
        ((2, 5), True, False, True, (2, 3)),
        ((4, 6), False, False, False, None),
        ((1, 3), True, False, True, (1, 3)),
        ((0, 9), True, False, True, (1, 3)),
    ]
    for other, overlaps, touches, mergeable, intersection in table:
        b = interval_cls(*other)
        for arg in (b, other):  # §2 every entry point coerces plain pairs
            if bool(a.overlaps(arg)) != overlaps:
                return False
            if bool(a.touches(arg)) != touches:
                return False
            if bool(a.mergeable_with(arg)) != mergeable:
                return False
        if bool(b.overlaps(a)) != overlaps or bool(b.touches(a)) != touches:
            return False
        if bool(b.mergeable_with(a)) != mergeable:
            return False
        got = a.intersection(b)
        if intersection is None:
            if got is not None:
                return False
        elif got != intersection or b.intersection(a) != intersection:
            return False
    return True


@_safe
def _contains_point_is_half_open(interval_cls: Any) -> bool:
    """§1.1 — start is a member, end is not."""
    iv = interval_cls(1, 3)
    expectations = [(0.999, False), (1, True), (1.5, True), (2.999, True), (3, False), (4, False)]
    return all(bool(iv.contains_point(x)) is want for x, want in expectations)


@_safe
def _behaves_like_a_tuple(interval_cls: Any) -> bool:
    """§2 — a namedtuple: tuple-equal, hashable, lexicographically ordered."""
    iv = interval_cls(1, 3)
    if iv != (1, 3) or iv.start != 1 or iv.end != 3 or len(iv) != 2:
        return False
    if {iv} != {(1, 3)}:
        return False
    order = sorted([interval_cls(2, 3), interval_cls(1, 9), interval_cls(1, 2)])
    return bool(order == [(1, 2), (1, 9), (2, 3)] and iv.length == 2)


@_safe
def _union_merges_touching_and_rejects_gaps(interval_cls: Any) -> bool:
    """§2.1 — union is defined exactly where mergeable_with holds."""
    a = interval_cls(1, 3)
    if a.union((3, 5)) != (1, 5) or a.union((2, 9)) != (1, 9) or a.union((0, 2)) != (0, 3):
        return False
    try:
        a.union((4, 6))
    except ValueError:
        return True
    return False


# ── §3/§3 merging and set algebra ────────────────────────────────────────────


@_safe
def _merge_matches_brute_force_coverage(merge: Any) -> bool:
    """§8.1 + §8.2 — canonical form *and* an identical point set.

    Two independent failures are caught here and neither alone suffices: failing
    to coalesce ``[1, 3)`` with ``[3, 5)`` preserves coverage but breaks the
    canonical form, while bridging the gap between ``[1, 3)`` and ``[4, 6)``
    preserves the canonical form but changes coverage at 3.5.
    """
    rng = random.Random(20260901)
    for _ in range(60):
        data = _random_intervals(rng, rng.randrange(0, 12), 20, 6)
        got = _pairs(merge(list(data)))
        if not _is_canonical(got):
            return False
        if any(_covers(data, x) != _covers(got, x) for x in _samples(26)):
            return False
    return True


@_safe
def _merge_is_idempotent_and_order_independent(merge: Any) -> bool:
    """§8.3 + §8.4."""
    rng = random.Random(20260902)
    for _ in range(40):
        data = _random_intervals(rng, rng.randrange(1, 14), 24, 7)
        once = _pairs(merge(list(data)))
        shuffled = list(data)
        rng.shuffle(shuffled)
        if once != _pairs(merge(once)) or once != _pairs(merge(shuffled)):
            return False
    return True


@_safe
def _intersect_matches_brute_force_coverage(intersect: Any) -> bool:
    """§3 + §8.2 — the intersection of two unions, sampled point by point."""
    rng = random.Random(20260903)
    for _ in range(60):
        a = _random_intervals(rng, rng.randrange(0, 8), 20, 6)
        b = _random_intervals(rng, rng.randrange(0, 8), 20, 6)
        got = _pairs(intersect(list(a), list(b)))
        if not _is_canonical(got):
            return False
        if any(_covers(got, x) != (_covers(a, x) and _covers(b, x)) for x in _samples(26)):
            return False
    return True


@_safe
def _subtract_matches_brute_force_coverage(subtract: Any) -> bool:
    """§3 + §8.5 — difference by coverage, with no zero-width slivers left behind."""
    rng = random.Random(20260904)
    for _ in range(60):
        a = _random_intervals(rng, rng.randrange(0, 8), 20, 8)
        b = _random_intervals(rng, rng.randrange(0, 8), 20, 5)
        got = _pairs(subtract(list(a), list(b)))
        if not _is_canonical(got):
            return False
        if any(_covers(got, x) != (_covers(a, x) and not _covers(b, x)) for x in _samples(28)):
            return False
    return True


# ── §5/§6 sweep line and scheduling ──────────────────────────────────────────


@_safe
def _max_concurrency_matches_brute_force(fn: Any) -> bool:
    """§5 + §8.4 — count and leftmost witness both agree with a trusted sweep."""
    rng = random.Random(20260905)
    for _ in range(80):
        data = _random_intervals(rng, rng.randrange(1, 10), 15, 6)
        want_count, want_witness = _true_max_concurrency(data)
        count, witness = fn(list(data))
        if count != want_count or (witness[0], witness[1]) != want_witness:
            return False
        shuffled = list(data)
        rng.shuffle(shuffled)
        if fn(shuffled) != (count, witness):
            return False
    return True


@_safe
def _lanes_are_optimal(fn: Any) -> bool:
    """§8.9 — a property no single call reveals.

    Lane count must equal the maximum concurrency computed independently here;
    lanes must be internally conflict-free (touching allowed, overlapping not);
    and the lanes together must contain every input exactly once.
    """
    rng = random.Random(20260906)
    for _ in range(60):
        data = _random_intervals(rng, rng.randrange(1, 14), 18, 7)
        lanes = fn(list(data))
        want_count, _ = _true_max_concurrency(data)
        if len(lanes) != want_count:
            return False
        flat: list[tuple[Any, Any]] = []
        for lane in lanes:
            pairs = _pairs(lane)
            if pairs != sorted(pairs):
                return False
            if any(prev_end > start for (_, prev_end), (start, _) in zip(pairs, pairs[1:], strict=False)):
                return False
            flat.extend(pairs)
        if sorted(flat) != sorted(data):
            return False
    return True


# ── §4 the index ─────────────────────────────────────────────────────────────


@_safe
def _point_query_boundaries(index_cls: Any) -> bool:
    """§1.1 + §4 — the single most likely place for a `<=` to be wrong."""
    idx = index_cls([(1, 3), (3, 5)])
    return bool(
        _pairs(idx.query_point(1)) == [(1, 3)]
        and _pairs(idx.query_point(2.999)) == [(1, 3)]
        and _pairs(idx.query_point(3)) == [(3, 5)]
        and _pairs(idx.query_point(5)) == []
        and _pairs(idx.query_point(0)) == []
        and len(idx) == 2
    )


@_safe
def _range_query_excludes_touching(index_cls: Any) -> bool:
    """§4 — a range that abuts a stored interval is not a hit; §9 empty range raises."""
    idx = index_cls([(1, 3), (5, 7)])
    if _pairs(idx.query_range(3, 5)) != []:
        return False
    if _pairs(idx.query_range(2, 6)) != [(1, 3), (5, 7)]:
        return False
    if _pairs(idx.query_range(3, 6)) != [(5, 7)]:
        return False
    if _pairs(idx.query_range(0, 1)) != [] or _pairs(idx.query_range(0, 2)) != [(1, 3)]:
        return False
    try:
        idx.query_range(5, 5)
    except ValueError:
        return True
    return False


@_safe
def _nested_intervals_are_all_found(index_cls: Any) -> bool:
    """§9 — a max_end augmentation that stores only the subtree's rightmost end
    loses the enclosing interval here."""
    idx = index_cls([(0, 100), (39, 40), (40, 41)])
    return bool(
        _pairs(idx.query_point(40)) == [(0, 100), (40, 41)]
        and _pairs(idx.query_point(39.5)) == [(0, 100), (39, 40)]
        and _pairs(idx.query_range(40, 41)) == [(0, 100), (40, 41)]
    )


@_safe
def _duplicates_form_a_multiset(index_cls: Any) -> bool:
    """§4 + §9 — equal intervals are stored separately; remove takes one."""
    idx = index_cls()
    idx.add((1, 5))
    idx.add((1, 5))
    idx.add((2, 6))
    if len(idx) != 3 or _pairs(idx.query_point(3)) != [(1, 5), (1, 5), (2, 6)]:
        return False
    idx.remove((1, 5))
    if len(idx) != 2 or _pairs(idx.query_point(3)) != [(1, 5), (2, 6)]:
        return False
    if (1, 5) not in idx:
        return False
    idx.remove((1, 5))
    if (1, 5) in idx or len(idx) != 1:
        return False
    try:
        idx.remove((1, 5))
    except KeyError:
        return True
    return False


@_safe
def _survives_churn(index_cls: Any) -> bool:
    """§8.6 + §8.7 — after interleaved insertion and deletion the index still
    answers exactly what a linear scan of the surviving multiset answers, and its
    structural invariants still hold."""
    rng = random.Random(20260907)
    idx = index_cls()
    pool = _random_intervals(rng, 400, 200, 25)
    for iv in pool:
        idx.add(iv)
    for _ in range(150):
        idx.remove(pool.pop(rng.randrange(len(pool))))
    idx.check_invariants()
    if len(idx) != len(pool) or _pairs(list(idx)) != sorted(pool):
        return False
    for _ in range(120):
        point = rng.randrange(-3, 230)
        if _pairs(idx.query_point(point)) != sorted(s for s in pool if s[0] <= point < s[1]):
            return False
    for _ in range(120):
        start = rng.randrange(-3, 230)
        end = start + rng.randrange(1, 40)
        want = sorted(s for s in pool if s[0] < end and start < s[1])
        if _pairs(idx.query_range(start, end)) != want:
            return False
    return True


@_safe
def _queries_are_insertion_order_independent(index_cls: Any) -> bool:
    """§8.4 — sorted, reverse-sorted and shuffled builds must agree with each
    other and with a linear scan, so a degenerate tree shape cannot change what
    is returned or in what order."""
    rng = random.Random(20260908)
    data = _random_intervals(rng, 120, 80, 20)
    probes = list(range(-2, 100))
    results: list[list[list[tuple[Any, Any]]]] = []
    for order in (sorted(data), sorted(data, reverse=True), data):
        idx = index_cls(order)
        idx.check_invariants()
        results.append([_pairs(idx.query_point(p)) for p in probes])
    brute = [sorted(s for s in data if s[0] <= p < s[1]) for p in probes]
    return bool(results[0] == results[1] and results[1] == results[2] and results[0] == brute)


@_safe
def _point_queries_prune(index_cls: Any) -> bool:
    """§8.10 — the max_end augmentation must actually eliminate subtrees.

    1023 disjoint intervals, 64 point queries: a scan of every node costs 65472
    node visits. The bound below is eight times smaller than that and five times
    larger than a straightforward augmented descent, so it separates the two
    algorithms without pinning down an implementation.
    """
    rng = random.Random(20260909)
    data = [(i * 4, i * 4 + 3) for i in range(1023)]
    shuffled = list(data)
    rng.shuffle(shuffled)
    idx = index_cls(shuffled)
    baseline = idx.nodes_visited
    for _ in range(64):
        idx.query_point(rng.randrange(0, 4092))
    visited = idx.nodes_visited - baseline
    return bool(0 < visited <= 64 * 120)


ORACLE = Oracle(
    whitepaper="09_interval_tree.md",
    package_hint="interval",
    required_names=[
        "Interval",
        "as_interval",
        "merge_intervals",
        "intersect_all",
        "subtract_all",
        "max_concurrency",
        "partition_into_lanes",
        "IntervalIndex",
    ],
    cases=[
        # §3 — merging, where touching must coalesce
        Case(
            target="merge_intervals",
            args=(list(_WORKED),),
            expected=[(0, 5), (6, 10)],
            description="§3 worked example merges to two ranges",
        ),
        Case(
            target="merge_intervals",
            args=([(1, 3), (3, 5)],),
            expected=[(1, 5)],
            description="§1.3 touching intervals coalesce into one",
        ),
        Case(
            target="merge_intervals",
            args=([(1, 3), (4, 6)],),
            expected=[(1, 3), (4, 6)],
            description="§1.4 a positive gap is never bridged",
        ),
        Case(
            target="merge_intervals",
            args=([(1, 10), (2, 3)],),
            expected=[(1, 10)],
            description="§3 a nested interval must not truncate its container",
        ),
        Case(
            target="merge_intervals",
            args=([(5, 7), (2, 4), (1, 3), (5, 7)],),
            expected=[(1, 4), (5, 7)],
            description="§3 unsorted input with a duplicate is normalised",
        ),
        Case(
            target="merge_intervals",
            args=([],),
            expected=[],
            description="§9 empty input merges to the empty list",
        ),
        # §3 — set algebra, where touching must contribute nothing
        Case(
            target="intersect_all",
            args=([(0, 5), (6, 10)], [(3, 6), (7, 8), (9, 12)]),
            expected=[(3, 5), (7, 8), (9, 10)],
            description="§3 intersection worked example; [6,10) x [3,6) touch and yield nothing",
        ),
        Case(
            target="intersect_all",
            args=([(1, 3)], [(3, 5)]),
            expected=[],
            description="§1.2 touching intervals have empty intersection",
        ),
        Case(
            target="subtract_all",
            args=([(0, 10)], [(3, 5), (5, 7)]),
            expected=[(0, 3), (7, 10)],
            description="§3 adjacent subtrahends leave no zero-width sliver at 5",
        ),
        Case(
            target="subtract_all",
            args=([(0, 3)], [(3, 5)]),
            expected=[(0, 3)],
            description="§3 subtracting a merely touching interval removes nothing",
        ),
        Case(
            target="subtract_all",
            args=([(0, 3)], [(0, 3)]),
            expected=[],
            description="§8.5 subtracting an identical interval leaves nothing",
        ),
        # §5 — the sweep line, where an end and a start at the same coordinate
        # must not be simultaneous
        Case(
            target="max_concurrency",
            args=([(1, 3), (3, 5)],),
            expected=(1, (1, 3)),
            description="§5 touching intervals are never concurrent",
        ),
        Case(
            target="max_concurrency",
            args=(list(_WORKED),),
            expected=(2, (1, 3)),
            description="§5 worked example: 2, first attained on [1,3)",
        ),
        Case(
            target="max_concurrency",
            args=([(0, 1), (0, 1)],),
            expected=(2, (0, 1)),
            description="§5 duplicate intervals count separately",
        ),
        Case(
            target="max_concurrency",
            args=([(4, 9)],),
            expected=(1, (4, 9)),
            description="§5 a single interval is its own witness",
        ),
        # §6 — lane partitioning
        Case(
            target="partition_into_lanes",
            args=(list(_WORKED),),
            expected=[[(0, 3), (3, 5), (6, 8), (8, 10)], [(1, 4), (7, 9)]],
            description="§6 worked example: touching intervals share a lane",
        ),
        Case(
            target="partition_into_lanes",
            args=([(1, 3), (3, 5)],),
            expected=[[(1, 3), (3, 5)]],
            description="§6 abutting intervals need only one lane",
        ),
        Case(
            target="partition_into_lanes",
            args=([(1, 3), (2, 5)],),
            expected=[[(1, 3)], [(2, 5)]],
            description="§6 overlapping intervals need two lanes",
        ),
        Case(
            target="partition_into_lanes",
            args=([],),
            expected=[],
            description="§9 empty input yields no lanes",
        ),
        # §2 — the type itself
        Case(
            target="Interval",
            call=False,
            check=_predicate_matrix,
            description="§2.1 boundary predicate matrix for [1,3)",
        ),
        Case(
            target="Interval",
            call=False,
            check=_contains_point_is_half_open,
            description="§1.1 contains_point includes start and excludes end",
        ),
        Case(
            target="Interval",
            call=False,
            check=_behaves_like_a_tuple,
            description="§2 Interval is a namedtuple: tuple-equal, hashable, ordered",
        ),
        Case(
            target="Interval",
            call=False,
            check=_union_merges_touching_and_rejects_gaps,
            description="§2.1 union spans touching intervals and rejects separated ones",
        ),
        # §4 — the index
        Case(
            target="IntervalIndex",
            call=False,
            check=_point_query_boundaries,
            description="§4 point query at a shared endpoint returns only the later interval",
        ),
        Case(
            target="IntervalIndex",
            call=False,
            check=_range_query_excludes_touching,
            description="§4 range query excludes merely touching intervals",
        ),
        Case(
            target="IntervalIndex",
            call=False,
            check=_nested_intervals_are_all_found,
            description="§9 nested intervals are all returned by a point query",
        ),
        Case(
            target="IntervalIndex",
            call=False,
            check=_duplicates_form_a_multiset,
            description="§4 duplicate intervals are a multiset; remove deletes one",
        ),
        Case(
            target="IntervalIndex",
            call=False,
            check=_survives_churn,
            description="§8.6/§8.7 400 adds and 150 removes leave queries and invariants exact",
        ),
        Case(
            target="IntervalIndex",
            call=False,
            check=_queries_are_insertion_order_independent,
            description="§8.4 query results do not depend on insertion order",
        ),
        Case(
            target="IntervalIndex",
            call=False,
            check=_point_queries_prune,
            description="§8.10 point queries prune subtrees instead of scanning",
        ),
        # Differential checks against brute force
        Case(
            target="merge_intervals",
            call=False,
            check=_merge_matches_brute_force_coverage,
            description="§8.1/§8.2 merge is canonical and preserves coverage (60 random cases)",
        ),
        Case(
            target="merge_intervals",
            call=False,
            check=_merge_is_idempotent_and_order_independent,
            description="§8.3/§8.4 merge is idempotent and order-independent",
        ),
        Case(
            target="intersect_all",
            call=False,
            check=_intersect_matches_brute_force_coverage,
            description="§3 intersection matches brute-force coverage (60 random cases)",
        ),
        Case(
            target="subtract_all",
            call=False,
            check=_subtract_matches_brute_force_coverage,
            description="§3 difference matches brute-force coverage (60 random cases)",
        ),
        Case(
            target="max_concurrency",
            call=False,
            check=_max_concurrency_matches_brute_force,
            description="§5 count and witness match an independent sweep (80 random cases)",
        ),
        Case(
            target="partition_into_lanes",
            call=False,
            check=_lanes_are_optimal,
            description="§8.9 lane count equals maximum concurrency and lanes are conflict-free",
        ),
    ],
    error_cases=[
        ErrorCase(
            target="Interval",
            args=(3, 3),
            exc_name="ValueError",
            description="§9 a zero-width interval is not representable",
        ),
        ErrorCase(
            target="Interval",
            args=(5, 1),
            exc_name="ValueError",
            description="§9 an inverted interval raises",
        ),
        ErrorCase(
            target="Interval",
            args=(float("nan"), 1),
            exc_name="ValueError",
            description="§9 a NaN endpoint raises ValueError",
        ),
        ErrorCase(
            target="Interval",
            args=("a", 1),
            exc_name="TypeError",
            description="§9 a non-numeric endpoint raises TypeError",
        ),
        ErrorCase(
            target="as_interval",
            args=((1, 2, 3),),
            exc_name="ValueError",
            description="§9 a 3-item sequence cannot be coerced",
        ),
        ErrorCase(
            target="as_interval",
            args=(5,),
            exc_name="ValueError",
            description="§9 a scalar cannot be coerced",
        ),
        ErrorCase(
            target="merge_intervals",
            args=([(1, 3), (4, 4)],),
            exc_name="ValueError",
            description="§9 validation is applied to coerced inputs, not bypassed",
        ),
        ErrorCase(
            target="max_concurrency",
            args=([],),
            exc_name="ValueError",
            description="§9 maximum concurrency of nothing is not defined",
        ),
    ],
    prohibitions=[
        Prohibition(
            reason=(
                "§11 forbids delegating to an interval library — intervaltree, "
                "portion, pyinterval, interlap, ncls, pyranges and quicksect all "
                "implement this specification already, so importing one reduces "
                "the deliverable to a wrapper that implements none of the "
                "half-open boundary logic that is the point of the library"
            ),
            imports=(
                "intervaltree",
                "portion",
                "pyinterval",
                "interlap",
                "ncls",
                "pyranges",
                "quicksect",
            ),
        ),
        Prohibition(
            reason=(
                "§11 forbids pandas, numpy and sortedcontainers — "
                "pandas.IntervalIndex(closed='left') is this whitepaper, and a "
                "SortedList replaces the augmented tree of §4 wholesale"
            ),
            imports=("pandas", "numpy", "sortedcontainers"),
        ),
    ],
)
