"""Validate each oracle against a reference implementation of its whitepaper.

An oracle that has never been executed is a liability. If it assumes an API the
whitepaper does not actually specify, or its checks are subtly wrong, it fails a
*correct* build — and it does so hours into a paid run, where the failure looks
like a FORGE regression rather than a test-harness bug.

So every oracle is exercised here against a compact reference implementation
written directly from its whitepaper. These references are not the deliverable
and are never given to any agent; they exist only to prove the oracle accepts
code that genuinely satisfies the spec.

The complementary direction — that each oracle *rejects* wrong code — is covered
for the merge-sort oracle in ``test_oracle_framework.py``, which is where the
framework's own detection logic is pinned down.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.tests.integration.oracles import (
    binary_search,
    circular_buffer,
    csv_parser,
    edit_distance,
    expression_evaluator,
    interval_tree,
    lru_cache,
    online_statistics,
    priority_queue,
    rational_arithmetic,
    semver,
    topological_sort,
    trie,
    union_find,
)
from backend.tests.integration.oracles._base import Oracle, run_oracle

# ── Reference implementations, written from the whitepapers ──────────────────

_REF_LRU = '''
"""Reference LRU cache — hash map plus intrusive doubly linked list (§2)."""


class _Node:
    __slots__ = ("key", "value", "prev", "next")

    def __init__(self, key=None, value=None):
        self.key = key
        self.value = value
        self.prev = None
        self.next = None


class LRUCache:
    def __init__(self, capacity, *, on_evict=None):
        if isinstance(capacity, bool) or not isinstance(capacity, int):
            raise TypeError("capacity must be an int")
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._on_evict = on_evict
        self._map = {}
        self._head = _Node()
        self._tail = _Node()
        self._head.next = self._tail
        self._tail.prev = self._head
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def _unlink(self, node):
        node.prev.next = node.next
        node.next.prev = node.prev
        node.prev = node.next = None

    def _push_front(self, node):
        node.next = self._head.next
        node.prev = self._head
        self._head.next.prev = node
        self._head.next = node

    def get(self, key, default=None):
        node = self._map.get(key)
        if node is None:
            self.misses += 1
            return default
        self._unlink(node)
        self._push_front(node)
        self.hits += 1
        return node.value

    def peek(self, key, default=None):
        node = self._map.get(key)
        return default if node is None else node.value

    def put(self, key, value):
        node = self._map.get(key)
        if node is not None:
            node.value = value
            self._unlink(node)
            self._push_front(node)
            return
        node = _Node(key, value)
        self._map[key] = node
        self._push_front(node)
        if len(self._map) > self._capacity:
            victim = self._tail.prev
            self._unlink(victim)
            del self._map[victim.key]
            self.evictions += 1
            if self._on_evict is not None:
                self._on_evict(victim.key, victim.value)

    def delete(self, key):
        node = self._map.pop(key, None)
        if node is None:
            return False
        self._unlink(node)
        return True

    def clear(self):
        self._map.clear()
        self._head.next = self._tail
        self._tail.prev = self._head
        self.hits = self.misses = self.evictions = 0

    def keys(self):
        node = self._head.next
        while node is not self._tail:
            yield node.key
            node = node.next

    def values(self):
        node = self._head.next
        while node is not self._tail:
            yield node.value
            node = node.next

    def items(self):
        node = self._head.next
        while node is not self._tail:
            yield (node.key, node.value)
            node = node.next

    def __len__(self):
        return len(self._map)

    def __contains__(self, key):
        return key in self._map

    @property
    def capacity(self):
        return self._capacity

    @property
    def hit_rate(self):
        total = self.hits + self.misses
        return 0.0 if total == 0 else self.hits / total

    def check_invariants(self):
        count = 0
        node = self._head.next
        while node is not self._tail:
            assert node.next.prev is node
            assert node.prev.next is node
            count += 1
            assert count <= len(self._map) + 1
            node = node.next
        assert count == len(self._map)
        assert len(self._map) <= self._capacity
'''

_REF_SEARCH = '''
"""Reference binary search family — half-open [lo, hi), iterative (§1)."""


def lower_bound(seq, value, *, key=lambda x: x, lo=0, hi=None):
    hi = len(seq) if hi is None else hi
    while lo < hi:
        mid = lo + (hi - lo) // 2
        if key(seq[mid]) < value:
            lo = mid + 1
        else:
            hi = mid
    return lo


def upper_bound(seq, value, *, key=lambda x: x, lo=0, hi=None):
    hi = len(seq) if hi is None else hi
    while lo < hi:
        mid = lo + (hi - lo) // 2
        if key(seq[mid]) <= value:
            lo = mid + 1
        else:
            hi = mid
    return lo


def equal_range(seq, value, *, key=lambda x: x):
    return lower_bound(seq, value, key=key), upper_bound(seq, value, key=key)


def search(seq, value, *, key=lambda x: x):
    idx = lower_bound(seq, value, key=key)
    if idx < len(seq) and key(seq[idx]) == value:
        return idx
    return None


def contains(seq, value, *, key=lambda x: x):
    return search(seq, value, key=key) is not None


def find_rotation_point(seq):
    if not seq:
        return 0
    lo, hi = 0, len(seq) - 1
    while lo < hi:
        mid = lo + (hi - lo) // 2
        if seq[mid] > seq[hi]:
            lo = mid + 1
        else:
            hi = mid
    return lo


def search_rotated(seq, value):
    lo, hi = 0, len(seq) - 1
    while lo <= hi:
        mid = lo + (hi - lo) // 2
        if seq[mid] == value:
            return mid
        if seq[lo] == seq[mid] == seq[hi]:
            lo += 1
            hi -= 1
        elif seq[lo] <= seq[mid]:
            if seq[lo] <= value < seq[mid]:
                hi = mid - 1
            else:
                lo = mid + 1
        else:
            if seq[mid] < value <= seq[hi]:
                lo = mid + 1
            else:
                hi = mid - 1
    return None


def find_peak(seq):
    if not seq:
        raise ValueError("find_peak requires a non-empty sequence")
    lo, hi = 0, len(seq) - 1
    while lo < hi:
        mid = lo + (hi - lo) // 2
        if seq[mid] < seq[mid + 1]:
            lo = mid + 1
        else:
            hi = mid
    return lo


class ConvergenceError(RuntimeError):
    pass


def bisect_predicate(predicate, lo, hi, *, tolerance=1e-9, max_iterations=200):
    if tolerance <= 0:
        raise ValueError("tolerance must be positive")
    if predicate(lo):
        raise ValueError("predicate must be False at lo")
    if not predicate(hi):
        raise ValueError("predicate must be True at hi")
    for _ in range(max_iterations):
        if hi - lo < tolerance:
            return hi
        mid = lo + (hi - lo) / 2
        if predicate(mid):
            hi = mid
        else:
            lo = mid
    raise ConvergenceError("did not converge")
'''

_REF_STATS = '''
"""Reference online statistics — Welford plus higher moments (§3)."""

import math


class InsufficientDataError(ValueError):
    pass


class UndefinedStatisticError(ValueError):
    pass


class RunningStats:
    def __init__(self):
        self.n = 0
        self._mean = 0.0
        self.M2 = 0.0
        self.M3 = 0.0
        self.M4 = 0.0
        self._min = None
        self._max = None

    def update(self, x):
        if isinstance(x, bool) or not isinstance(x, (int, float)):
            raise TypeError("sample must be a real number")
        if math.isnan(x) or math.isinf(x):
            raise ValueError("sample must be finite")
        x = float(x)

        n1 = self.n
        self.n += 1
        delta = x - self._mean
        delta_n = delta / self.n
        delta_n2 = delta_n * delta_n
        term = delta * delta_n * n1

        self._mean += delta_n
        self.M4 += (
            term * delta_n2 * (self.n * self.n - 3 * self.n + 3)
            + 6 * delta_n2 * self.M2
            - 4 * delta_n * self.M3
        )
        self.M3 += term * delta_n * (self.n - 2) - 3 * delta_n * self.M2
        self.M2 += term

        self._min = x if self._min is None else min(self._min, x)
        self._max = x if self._max is None else max(self._max, x)

    def update_many(self, values):
        for v in values:
            self.update(v)

    def merge(self, other):
        out = RunningStats()
        if other.n == 0:
            out.__dict__.update(self.__dict__)
            return out
        if self.n == 0:
            out.__dict__.update(other.__dict__)
            return out
        a, b = self, other
        n = a.n + b.n
        delta = b._mean - a._mean
        out.n = n
        out._mean = a._mean + delta * b.n / n
        out.M2 = a.M2 + b.M2 + delta**2 * a.n * b.n / n
        out.M3 = (
            a.M3 + b.M3
            + delta**3 * a.n * b.n * (a.n - b.n) / n**2
            + 3 * delta * (a.n * b.M2 - b.n * a.M2) / n
        )
        out.M4 = (
            a.M4 + b.M4
            + delta**4 * a.n * b.n * (a.n**2 - a.n * b.n + b.n**2) / n**3
            + 6 * delta**2 * (a.n**2 * b.M2 + b.n**2 * a.M2) / n**2
            + 4 * delta * (a.n * b.M3 - b.n * a.M3) / n
        )
        out._min = min(x for x in (a._min, b._min) if x is not None)
        out._max = max(x for x in (a._max, b._max) if x is not None)
        return out

    def _require(self, k, name):
        if self.n < k:
            raise InsufficientDataError(f"{name} needs at least {k} samples")

    @property
    def mean(self):
        self._require(1, "mean")
        return self._mean

    @property
    def variance(self):
        self._require(2, "variance")
        return max(0.0, self.M2) / (self.n - 1)

    @property
    def variance_population(self):
        self._require(1, "variance_population")
        return max(0.0, self.M2) / self.n

    @property
    def stddev(self):
        return math.sqrt(self.variance)

    @property
    def skewness(self):
        self._require(3, "skewness")
        if self.M2 == 0.0:
            raise UndefinedStatisticError("skewness undefined for constant data")
        return math.sqrt(self.n) * self.M3 / self.M2**1.5

    @property
    def kurtosis(self):
        self._require(4, "kurtosis")
        if self.M2 == 0.0:
            raise UndefinedStatisticError("kurtosis undefined for constant data")
        return self.n * self.M4 / (self.M2 * self.M2) - 3

    @property
    def minimum(self):
        self._require(1, "minimum")
        return self._min

    @property
    def maximum(self):
        self._require(1, "maximum")
        return self._max
'''

_REF_INTERVALS = '''
"""Reference half-open interval library — algebra, index, sweep line (§1)."""

import math
from collections import namedtuple


class Interval(namedtuple("Interval", ["start", "end"])):
    __slots__ = ()

    def __new__(cls, start, end):
        for value in (start, end):
            if not isinstance(value, (int, float)):
                raise TypeError(f"endpoint must be a real number, got {value!r}")
            if math.isnan(value):
                raise ValueError("endpoint must not be NaN")
        if not start < end:
            raise ValueError(f"empty or inverted interval [{start}, {end})")
        return super().__new__(cls, start, end)

    @property
    def length(self):
        return self.end - self.start

    def contains_point(self, x):
        return self.start <= x < self.end

    def overlaps(self, other):
        other = as_interval(other)
        return self.start < other.end and other.start < self.end

    def touches(self, other):
        other = as_interval(other)
        return self.end == other.start or other.end == self.start

    def mergeable_with(self, other):
        other = as_interval(other)
        return self.start <= other.end and other.start <= self.end

    def intersection(self, other):
        other = as_interval(other)
        lo, hi = max(self.start, other.start), min(self.end, other.end)
        return Interval(lo, hi) if lo < hi else None

    def union(self, other):
        other = as_interval(other)
        if not self.mergeable_with(other):
            raise ValueError("union of separated intervals is not an interval")
        return Interval(min(self.start, other.start), max(self.end, other.end))


def as_interval(value):
    if isinstance(value, Interval):
        return value
    try:
        start, end = value
    except (TypeError, ValueError) as exc:
        raise ValueError(f"cannot coerce {value!r} to an Interval") from exc
    return Interval(start, end)


def merge_intervals(intervals):
    out = []
    for iv in sorted(as_interval(x) for x in intervals):
        if out and iv.start <= out[-1].end:          # <= : touching coalesces
            out[-1] = Interval(out[-1].start, max(out[-1].end, iv.end))
        else:
            out.append(iv)
    return out


def intersect_all(a, b):
    left, right = merge_intervals(a), merge_intervals(b)
    out, i, j = [], 0, 0
    while i < len(left) and j < len(right):
        lo = max(left[i].start, right[j].start)
        hi = min(left[i].end, right[j].end)
        if lo < hi:
            out.append(Interval(lo, hi))
        if left[i].end < right[j].end:
            i += 1
        else:
            j += 1
    return out


def subtract_all(a, b):
    left, right = merge_intervals(a), merge_intervals(b)
    out, j = [], 0
    for iv in left:
        while j < len(right) and right[j].end <= iv.start:
            j += 1
        cur, k = iv.start, j
        while k < len(right) and right[k].start < iv.end:
            if right[k].start > cur:
                out.append(Interval(cur, right[k].start))
            cur = max(cur, right[k].end)
            k += 1
        if cur < iv.end:
            out.append(Interval(cur, iv.end))
    return out


def max_concurrency(intervals):
    items = [as_interval(x) for x in intervals]
    if not items:
        raise ValueError("max_concurrency requires at least one interval")
    deltas = {}
    for iv in items:
        deltas[iv.start] = deltas.get(iv.start, 0) + 1
        deltas[iv.end] = deltas.get(iv.end, 0) - 1
    coords = sorted(deltas)
    best_count, best_iv, running = 0, None, 0
    for index, coord in enumerate(coords):
        running += deltas[coord]                    # all events at coord first
        if running > best_count and index + 1 < len(coords):
            best_count, best_iv = running, Interval(coord, coords[index + 1])
    return best_count, best_iv


def partition_into_lanes(intervals):
    lanes = []
    for iv in sorted(as_interval(x) for x in intervals):
        for lane in lanes:
            if lane[-1].end <= iv.start:            # <= : touching may share
                lane.append(iv)
                break
        else:
            lanes.append([iv])
    return lanes


class _Node:
    __slots__ = ("interval", "seq", "left", "right", "max_end")

    def __init__(self, interval, seq):
        self.interval = interval
        self.seq = seq
        self.left = self.right = None
        self.max_end = interval.end


def _key(node):
    return (node.interval.start, node.interval.end, node.seq)


def _refresh(node):
    node.max_end = node.interval.end
    for child in (node.left, node.right):
        if child is not None and child.max_end > node.max_end:
            node.max_end = child.max_end


class IntervalIndex:
    def __init__(self, intervals=()):
        self._root = None
        self._size = 0
        self._seq = 0
        self.nodes_visited = 0
        for iv in intervals:
            self.add(iv)

    def add(self, interval):
        iv = as_interval(interval)
        node = _Node(iv, self._seq)
        self._seq += 1
        self._size += 1
        key, path, cur = _key(node), [], self._root
        while cur is not None:
            path.append(cur)
            cur = cur.left if key < _key(cur) else cur.right
        if not path:
            self._root = node
        elif key < _key(path[-1]):
            path[-1].left = node
        else:
            path[-1].right = node
        for parent in reversed(path):
            _refresh(parent)
        return iv

    def remove(self, interval):
        iv = as_interval(interval)
        seq = self._earliest_seq(iv)
        if seq is None:
            raise KeyError(f"interval {iv!r} is not in the index")
        self._root = self._delete(self._root, (iv.start, iv.end, seq))
        self._size -= 1

    def _earliest_seq(self, iv):
        node, best, want = self._root, None, (iv.start, iv.end)
        while node is not None:
            here = (node.interval.start, node.interval.end)
            if here < want:
                node = node.right
            elif here > want:
                node = node.left
            else:
                best, node = node.seq, node.left
        return best

    def _delete(self, node, key):
        if node is None:
            return None
        if key < _key(node):
            node.left = self._delete(node.left, key)
        elif key > _key(node):
            node.right = self._delete(node.right, key)
        elif node.left is None:
            return node.right
        elif node.right is None:
            return node.left
        else:
            succ = node.right
            while succ.left is not None:
                succ = succ.left
            node.interval, node.seq = succ.interval, succ.seq
            node.right = self._delete(node.right, _key(succ))
        _refresh(node)
        return node

    def query_point(self, x):
        out = []
        self._visit_point(self._root, x, out)
        return out

    def _visit_point(self, node, x, out):
        if node is None or node.max_end <= x:       # <= : end == x is not a hit
            return
        self.nodes_visited += 1
        self._visit_point(node.left, x, out)
        if node.interval.start <= x < node.interval.end:
            out.append(node.interval)
        if node.interval.start <= x:
            self._visit_point(node.right, x, out)

    def query_range(self, start, end):
        out = []
        self._visit_range(self._root, Interval(start, end), out)
        return out

    def _visit_range(self, node, query, out):
        if node is None or node.max_end <= query.start:
            return
        self.nodes_visited += 1
        self._visit_range(node.left, query, out)
        if node.interval.start < query.end and query.start < node.interval.end:
            out.append(node.interval)
        if node.interval.start < query.end:
            self._visit_range(node.right, query, out)

    def check_invariants(self):
        def walk(node, lo, hi):
            if node is None:
                return float("-inf")
            key = _key(node)
            assert lo < key < hi, f"BST order violated at {node.interval!r}"
            best = max(node.interval.end, walk(node.left, lo, key), walk(node.right, key, hi))
            assert node.max_end == best, f"max_end wrong at {node.interval!r}"
            return best

        neg, pos = float("-inf"), float("inf")
        walk(self._root, (neg, neg, -1), (pos, pos, pos))
        assert len(list(iter(self))) == self._size

    def __len__(self):
        return self._size

    def __iter__(self):
        stack, node = [], self._root
        while stack or node is not None:
            while node is not None:
                stack.append(node)
                node = node.left
            node = stack.pop()
            yield node.interval
            node = node.right

    def __contains__(self, interval):
        try:
            iv = as_interval(interval)
        except (TypeError, ValueError):
            return False
        return self._earliest_seq(iv) is not None
'''

_REF_UNION_FIND = '''
"""Reference disjoint-set forest — path compression (§3) + union by rank (§4)."""


def _check_count(n):
    if isinstance(n, bool) or not isinstance(n, int):
        raise TypeError("element count must be an int")
    if n < 0:
        raise ValueError("element count must be non-negative")


class UnionFind:
    def __init__(self, n):
        _check_count(n)
        self._parent = list(range(n))
        self._rank = [0] * n
        self._size = [1] * n
        self._components = n
        self._hops = 0

    def __len__(self):
        return len(self._parent)

    def _validate(self, x):
        if isinstance(x, bool) or not isinstance(x, int):
            raise TypeError(f"element must be an int, got {type(x).__name__}")
        if not 0 <= x < len(self._parent):
            raise IndexError(f"element {x} out of range")

    def find(self, x):
        self._validate(x)
        root = x
        hops = 0
        while self._parent[root] != root:
            root = self._parent[root]
            hops += 1
        self._hops += hops
        while self._parent[x] != root:
            nxt = self._parent[x]
            self._parent[x] = root
            x = nxt
        return root

    def union(self, a, b):
        ra = self.find(a)
        rb = self.find(b)
        if ra == rb:
            return False
        if self._rank[ra] < self._rank[rb]:
            ra, rb = rb, ra
        elif self._rank[ra] == self._rank[rb]:
            self._rank[ra] += 1
        self._parent[rb] = ra
        self._size[ra] += self._size[rb]
        self._components -= 1
        return True

    def connected(self, a, b):
        return self.find(a) == self.find(b)

    def add(self):
        idx = len(self._parent)
        self._parent.append(idx)
        self._rank.append(0)
        self._size.append(1)
        self._components += 1
        return idx

    def component_size(self, x):
        return self._size[self.find(x)]

    def components(self):
        groups = {}
        for x in range(len(self._parent)):
            groups.setdefault(self.find(x), []).append(x)
        return sorted((sorted(g) for g in groups.values()), key=lambda g: g[0])

    def parent_of(self, x):
        self._validate(x)
        return self._parent[x]

    def rank_of(self, x):
        self._validate(x)
        return self._rank[x]

    def path_length(self, x):
        self._validate(x)
        links = 0
        while self._parent[x] != x:
            x = self._parent[x]
            links += 1
        return links

    @property
    def component_count(self):
        return self._components

    @property
    def pointer_hops(self):
        return self._hops


def from_edges(n, edges):
    uf = UnionFind(n)
    for edge in edges:
        try:
            pair = tuple(edge)
        except TypeError:
            raise ValueError("each edge must be a pair of elements") from None
        if len(pair) != 2:
            raise ValueError("each edge must be a pair of elements")
        uf.union(pair[0], pair[1])
    return uf


def connected_components(n, edges):
    return from_edges(n, edges).components()
'''

# Raw string: the state machine compares against "\r" and "\n" literals, which
# must reach the generated module as escape sequences rather than as real
# newlines inside its string literals.
_REF_CSV = r'''
"""Reference RFC 4180 reader/writer — character-level state machine (§3)."""

QUOTE_MINIMAL = "minimal"
QUOTE_ALL = "all"
QUOTE_NONE = "none"

_MODES = (QUOTE_MINIMAL, QUOTE_ALL, QUOTE_NONE)


class CsvError(ValueError):
    pass


def _check_dialect(delimiter, quotechar):
    for name, char in (("delimiter", delimiter), ("quotechar", quotechar)):
        if not isinstance(char, str) or len(char) != 1:
            raise CsvError(name + " must be a one-character string")
        if char == "\r" or char == "\n":
            raise CsvError(name + " must not be CR or LF")
    if delimiter == quotechar:
        raise CsvError("delimiter and quotechar must differ")


class CsvReader:
    """§3 state machine. States are START, UNQUOTED, QUOTED, AFTER_QUOTE."""

    _START, _UNQUOTED, _QUOTED, _AFTER_QUOTE = 0, 1, 2, 3

    def __init__(self, *, delimiter=",", quotechar='"', strict=False):
        _check_dialect(delimiter, quotechar)
        self._delimiter = delimiter
        self._quotechar = quotechar
        self._strict = strict
        self._reset()

    def _reset(self):
        self._state = self._START
        self._field = []
        self._row = []
        self._started = False
        self._after_cr = False

    def _end_field(self):
        self._row.append("".join(self._field))
        self._field = []

    def _end_record(self, out):
        if self._started:
            self._end_field()
            out.append(self._row)
        else:
            out.append([])          # §3.2 a blank line is the empty record
        self._state = self._START
        self._field = []
        self._row = []
        self._started = False

    def feed(self, chunk):
        if not isinstance(chunk, str):
            raise CsvError("input must be a str")
        out = []
        for char in chunk:
            if self._after_cr:      # §3.1 the LF of a CRLF pair
                self._after_cr = False
                if char == "\n":
                    continue
            if self._state == self._QUOTED:
                if char == self._quotechar:
                    self._state = self._AFTER_QUOTE
                else:
                    self._field.append(char)
                continue
            if char == "\r" or char == "\n":
                self._end_record(out)
                self._after_cr = char == "\r"
                continue
            self._started = True
            if self._state == self._START:
                if char == self._quotechar:
                    self._state = self._QUOTED
                elif char == self._delimiter:
                    self._end_field()
                else:
                    self._field.append(char)
                    self._state = self._UNQUOTED
            elif self._state == self._UNQUOTED:
                if char == self._delimiter:
                    self._end_field()
                    self._state = self._START
                elif char == self._quotechar and self._strict:
                    raise CsvError("quotechar inside an unquoted field")
                else:
                    self._field.append(char)
            elif char == self._quotechar:
                self._field.append(char)        # §3 the "" escape
                self._state = self._QUOTED
            elif char == self._delimiter:
                self._end_field()
                self._state = self._START
            elif self._strict:
                raise CsvError("unexpected character after closing quotechar")
            else:
                self._field.append(char)        # §3.3 continue *unquoted*
                self._state = self._UNQUOTED
        return out

    def close(self):
        if self._state == self._QUOTED:
            raise CsvError("unterminated quoted field at end of input")
        out = []
        if self._started:
            self._end_field()
            out.append(self._row)
        self._reset()
        return out


def parse_csv(text, *, delimiter=",", quotechar='"', strict=False):
    reader = CsvReader(delimiter=delimiter, quotechar=quotechar, strict=strict)
    rows = reader.feed(text)
    rows.extend(reader.close())
    return rows


def needs_quoting(field, *, delimiter=",", quotechar='"'):
    _check_dialect(delimiter, quotechar)
    if not isinstance(field, str):
        raise CsvError("field must be a str")
    for char in field:
        if char in (delimiter, quotechar, "\r", "\n"):
            return True
    return False


def _render(value):
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def format_row(row, *, delimiter=",", quotechar='"', quoting=QUOTE_MINIMAL):
    _check_dialect(delimiter, quotechar)
    if quoting not in _MODES:
        raise CsvError("unknown quoting mode: " + repr(quoting))
    if isinstance(row, (str, bytes)) or not isinstance(row, (list, tuple)):
        raise CsvError("row must be a list or tuple of fields")
    fields = [_render(value) for value in row]
    # §5 a sole empty field must be quoted or it reads back as the empty record
    lone_empty = len(fields) == 1 and fields[0] == ""
    out = []
    for field in fields:
        needed = needs_quoting(field, delimiter=delimiter, quotechar=quotechar)
        if quoting == QUOTE_NONE:
            if needed:
                raise CsvError("field requires quoting but quoting is disabled")
            out.append(field)
        elif quoting == QUOTE_ALL or needed or lone_empty:
            doubled = field.replace(quotechar, quotechar * 2)
            out.append(quotechar + doubled + quotechar)
        else:
            out.append(field)
    return delimiter.join(out)


def format_csv(
    rows, *, delimiter=",", quotechar='"', quoting=QUOTE_MINIMAL, lineterminator="\r\n"
):
    if lineterminator not in ("\r\n", "\n", "\r"):
        raise CsvError("lineterminator must be CRLF, LF or CR")
    parts = []
    for row in rows:
        parts.append(
            format_row(row, delimiter=delimiter, quotechar=quotechar, quoting=quoting)
        )
        parts.append(lineterminator)
    return "".join(parts)
'''

_REF_RATIONAL = '''
"""Reference exact rational arithmetic — canonical form fixed at construction (§2)."""

_ASCII_DIGITS = "0123456789"


def _require_int(value, what):
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{what} must be an int, not {type(value).__name__}")


def gcd(a, b):
    _require_int(a, "a")
    _require_int(b, "b")
    a = -a if a < 0 else a
    b = -b if b < 0 else b
    while b:
        a, b = b, a % b
    return a


def _normalise(n, d):
    if d == 0:
        raise ZeroDivisionError("denominator must not be zero")
    if d < 0:
        n, d = -n, -d
    g = gcd(n, d)
    return n // g, d // g


class Rational:
    __slots__ = ("_d", "_n")

    def __init__(self, numerator, denominator=1):
        _require_int(numerator, "numerator")
        _require_int(denominator, "denominator")
        self._n, self._d = _normalise(numerator, denominator)

    @property
    def numerator(self):
        return self._n

    @property
    def denominator(self):
        return self._d

    def _coerce(self, other):
        if isinstance(other, Rational):
            return other
        if isinstance(other, bool) or not isinstance(other, int):
            return None
        return Rational(other, 1)

    def __add__(self, other):
        o = self._coerce(other)
        if o is None:
            return NotImplemented
        return Rational(self._n * o._d + o._n * self._d, self._d * o._d)

    __radd__ = __add__

    def __sub__(self, other):
        o = self._coerce(other)
        if o is None:
            return NotImplemented
        return Rational(self._n * o._d - o._n * self._d, self._d * o._d)

    def __rsub__(self, other):
        o = self._coerce(other)
        if o is None:
            return NotImplemented
        return Rational(o._n * self._d - self._n * o._d, self._d * o._d)

    def __mul__(self, other):
        o = self._coerce(other)
        if o is None:
            return NotImplemented
        g1 = gcd(self._n, o._d)
        g2 = gcd(o._n, self._d)
        return Rational((self._n // g1) * (o._n // g2), (self._d // g2) * (o._d // g1))

    __rmul__ = __mul__

    def __truediv__(self, other):
        o = self._coerce(other)
        if o is None:
            return NotImplemented
        if o._n == 0:
            raise ZeroDivisionError("division by zero")
        return Rational(self._n * o._d, self._d * o._n)

    def __rtruediv__(self, other):
        o = self._coerce(other)
        if o is None:
            return NotImplemented
        if self._n == 0:
            raise ZeroDivisionError("division by zero")
        return Rational(o._n * self._d, o._d * self._n)

    def __pow__(self, exponent):
        if isinstance(exponent, bool) or not isinstance(exponent, int):
            return NotImplemented
        if exponent >= 0:
            return Rational(self._n ** exponent, self._d ** exponent)
        if self._n == 0:
            raise ZeroDivisionError("zero cannot be raised to a negative power")
        k = -exponent
        return Rational(self._d ** k, self._n ** k)

    def __neg__(self):
        return Rational(-self._n, self._d)

    def __pos__(self):
        return Rational(self._n, self._d)

    def __abs__(self):
        return Rational(-self._n if self._n < 0 else self._n, self._d)

    def __bool__(self):
        return self._n != 0

    def __eq__(self, other):
        o = self._coerce(other)
        if o is None:
            return NotImplemented
        return self._n == o._n and self._d == o._d

    def __lt__(self, other):
        o = self._coerce(other)
        if o is None:
            return NotImplemented
        return self._n * o._d < o._n * self._d

    def __le__(self, other):
        o = self._coerce(other)
        if o is None:
            return NotImplemented
        return self._n * o._d <= o._n * self._d

    def __gt__(self, other):
        o = self._coerce(other)
        if o is None:
            return NotImplemented
        return self._n * o._d > o._n * self._d

    def __ge__(self, other):
        o = self._coerce(other)
        if o is None:
            return NotImplemented
        return self._n * o._d >= o._n * self._d

    def __hash__(self):
        return hash(self._n) if self._d == 1 else hash((self._n, self._d))

    def __str__(self):
        return str(self._n) if self._d == 1 else f"{self._n}/{self._d}"

    def __repr__(self):
        return f"Rational({self._n}, {self._d})"

    def reciprocal(self):
        if self._n == 0:
            raise ZeroDivisionError("zero has no reciprocal")
        return Rational(self._d, self._n)

    def to_float(self):
        return self._n / self._d

    def limit_denominator(self, max_denominator):
        _require_int(max_denominator, "max_denominator")
        if max_denominator < 1:
            raise ValueError("max_denominator must be at least 1")
        if self._d <= max_denominator:
            return self
        p0, q0, p1, q1 = 0, 1, 1, 0
        n, d = self._n, self._d
        while True:
            a = n // d
            q2 = q0 + a * q1
            if q2 > max_denominator:
                break
            p0, q0, p1, q1 = p1, q1, p0 + a * p1, q2
            n, d = d, n - a * d
        k = (max_denominator - q0) // q1
        low = Rational(p0 + k * p1, q0 + k * q1)
        high = Rational(p1, q1)
        gap_low, gap_high = abs(low - self), abs(high - self)
        if gap_high < gap_low:
            return high
        if gap_low < gap_high:
            return low
        return high if high._d <= low._d else low


def _parse_signed_int(text):
    body, negative = text, False
    if body[:1] in ("+", "-"):
        negative = body[0] == "-"
        body = body[1:]
    if not body:
        return None
    value = 0
    for char in body:
        digit = _ASCII_DIGITS.find(char)
        if digit < 0:
            return None
        value = value * 10 + digit
    return -value if negative else value


def parse_rational(text):
    if not isinstance(text, str):
        raise TypeError(f"parse_rational requires a str, not {type(text).__name__}")
    stripped = text.strip()
    if "/" in stripped:
        parts = stripped.split("/")
        if len(parts) != 2:
            raise ValueError(f"malformed rational literal: {text!r}")
        num, den = _parse_signed_int(parts[0]), _parse_signed_int(parts[1])
    else:
        num, den = _parse_signed_int(stripped), 1
    if num is None or den is None:
        raise ValueError(f"malformed rational literal: {text!r}")
    if den == 0:
        raise ZeroDivisionError("denominator must not be zero")
    return Rational(num, den)


def approximate(value, *, max_denominator):
    _require_int(max_denominator, "max_denominator")
    if max_denominator < 1:
        raise ValueError("max_denominator must be at least 1")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"value must be an int or float, not {type(value).__name__}")
    if isinstance(value, int):
        return Rational(value, 1).limit_denominator(max_denominator)
    if value != value or value == float("inf") or value == float("-inf"):
        raise ValueError("value must be finite")
    numerator, denominator = value, 1
    while numerator != int(numerator):
        numerator *= 2.0
        denominator *= 2
    return Rational(int(numerator), denominator).limit_denominator(max_denominator)


def rational_sum(values):
    total = Rational(0, 1)
    for value in values:
        if isinstance(value, Rational):
            total = total + value
        elif isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"rational_sum accepts Rational or int, not {type(value).__name__}")
        else:
            total = total + Rational(value, 1)
    return total
'''


_REF_SEMVER = '''
"""Reference SemVer 2.0.0 — strict scanner, precedence, comparator ranges (§1-§3)."""

import functools

_DIGITS = frozenset("0123456789")
_ALNUM = frozenset("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-")

_CARET = "^"
_TILDE = "~"
_OPERATORS = (">=", "<=", ">", "<", "=", _CARET, _TILDE)


class InvalidVersionError(ValueError):
    pass


class InvalidRangeError(ValueError):
    pass


def _is_numeric(ident):
    return bool(ident) and all(c in _DIGITS for c in ident)


def _has_leading_zero(ident):
    return len(ident) > 1 and ident[0] == "0"


def _core_int(text, what):
    if not _is_numeric(text) or _has_leading_zero(text):
        raise InvalidVersionError(f"{what} must be a number without leading zeroes: {text!r}")
    return int(text)


def _check_prerelease(ident):
    if not ident:
        raise InvalidVersionError("empty pre-release identifier")
    if not all(c in _ALNUM for c in ident):
        raise InvalidVersionError(f"bad character in pre-release identifier {ident!r}")
    if _is_numeric(ident) and _has_leading_zero(ident):
        raise InvalidVersionError(f"numeric pre-release identifier has a leading zero: {ident!r}")


def _check_build(ident):
    if not ident:
        raise InvalidVersionError("empty build identifier")
    if not all(c in _ALNUM for c in ident):
        raise InvalidVersionError(f"bad character in build identifier {ident!r}")


class Version:
    """Immutable, hashable; equality and hash follow precedence (§2.4)."""

    __slots__ = ("major", "minor", "patch", "prerelease", "build")

    def __init__(self, major, minor, patch, prerelease=(), build=()):
        for value, name in ((major, "major"), (minor, "minor"), (patch, "patch")):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an int, got {type(value).__name__}")
            if value < 0:
                raise InvalidVersionError(f"{name} must not be negative: {value}")
        pre = tuple(prerelease)
        bld = tuple(build)
        for ident in pre:
            _check_prerelease(ident)
        for ident in bld:
            _check_build(ident)
        self.major = major
        self.minor = minor
        self.patch = patch
        self.prerelease = pre
        self.build = bld

    def __str__(self):
        text = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            text += "-" + ".".join(self.prerelease)
        if self.build:
            text += "+" + ".".join(self.build)
        return text

    def __repr__(self):
        return f"Version({str(self)!r})"

    @property
    def is_prerelease(self):
        return bool(self.prerelease)

    def __eq__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return compare(self, other) == 0

    def __hash__(self):
        return hash((self.major, self.minor, self.patch, self.prerelease))

    def __lt__(self, other):
        return compare(self, other) < 0

    def __le__(self, other):
        return compare(self, other) <= 0

    def __gt__(self, other):
        return compare(self, other) > 0

    def __ge__(self, other):
        return compare(self, other) >= 0


def parse(text):
    if not isinstance(text, str):
        raise TypeError(f"version must be a str, got {type(text).__name__}")
    body, plus, build_text = text.partition("+")
    build = tuple(build_text.split(".")) if plus else ()
    core_text, dash, pre_text = body.partition("-")
    prerelease = tuple(pre_text.split(".")) if dash else ()
    parts = core_text.split(".")
    if len(parts) != 3:
        raise InvalidVersionError(f"expected major.minor.patch, got {text!r}")
    return Version(
        _core_int(parts[0], "major"),
        _core_int(parts[1], "minor"),
        _core_int(parts[2], "patch"),
        prerelease,
        build,
    )


def _coerce(value):
    return value if isinstance(value, Version) else parse(value)


def _sign(value):
    return (value > 0) - (value < 0)


def _identifier_key(ident):
    """Numeric identifiers sort as integers and below every alphanumeric one."""
    if _is_numeric(ident):
        return (0, int(ident), "")
    return (1, 0, ident)


def _compare_prerelease(left, right):
    for x, y in zip(left, right):
        kx, ky = _identifier_key(x), _identifier_key(y)
        if kx != ky:
            return -1 if kx < ky else 1
    return _sign(len(left) - len(right))


def compare(a, b):
    left, right = _coerce(a), _coerce(b)
    core_left = (left.major, left.minor, left.patch)
    core_right = (right.major, right.minor, right.patch)
    if core_left != core_right:
        return -1 if core_left < core_right else 1
    if not left.prerelease and not right.prerelease:
        return 0
    if not left.prerelease:
        return 1
    if not right.prerelease:
        return -1
    return _compare_prerelease(left.prerelease, right.prerelease)


def sort_versions(versions):
    return sorted((_coerce(v) for v in versions), key=functools.cmp_to_key(compare))


def _caret_bound(version):
    if version.major:
        return Version(version.major + 1, 0, 0)
    if version.minor:
        return Version(0, version.minor + 1, 0)
    return Version(0, 0, version.patch + 1)


def _parse_comparator(token):
    if token == "*":
        return [(">=", Version(0, 0, 0))]
    operator, rest = "=", token
    for candidate in _OPERATORS:
        if token.startswith(candidate):
            operator, rest = candidate, token[len(candidate) :]
            break
    if not rest:
        raise InvalidRangeError(f"comparator {token!r} names no version")
    try:
        version = parse(rest)
    except InvalidVersionError as exc:
        raise InvalidRangeError(f"comparator {token!r}: {exc}") from exc
    if operator == _CARET:
        return [(">=", version), ("<", _caret_bound(version))]
    if operator == _TILDE:
        return [(">=", version), ("<", Version(version.major, version.minor + 1, 0))]
    return [(operator, version)]


def _parse_range(text):
    if not isinstance(text, str):
        raise TypeError(f"range must be a str, got {type(text).__name__}")
    sets = []
    for chunk in text.split("||"):
        tokens = chunk.split()
        if not tokens:
            raise InvalidRangeError(f"empty comparator set in {text!r}")
        comparators = []
        for token in tokens:
            comparators.extend(_parse_comparator(token))
        sets.append(comparators)
    return sets


_TESTS = {
    "=": lambda c: c == 0,
    ">": lambda c: c > 0,
    ">=": lambda c: c >= 0,
    "<": lambda c: c < 0,
    "<=": lambda c: c <= 0,
}


def _matches_set(version, comparators):
    for operator, bound in comparators:
        if not _TESTS[operator](compare(version, bound)):
            return False
    if not version.prerelease:
        return True
    core = (version.major, version.minor, version.patch)
    return any(
        bound.prerelease and (bound.major, bound.minor, bound.patch) == core
        for _, bound in comparators
    )


def satisfies(version, range_expr):
    candidate = _coerce(version)
    return any(_matches_set(candidate, s) for s in _parse_range(range_expr))


def max_satisfying(versions, range_expr):
    sets = _parse_range(range_expr)
    best = None
    for item in versions:
        candidate = _coerce(item)
        if not any(_matches_set(candidate, s) for s in sets):
            continue
        if best is None or compare(candidate, best) > 0:
            best = candidate
    return best
'''

_REF_TRIE = '''
"""Reference prefix trie — explicit nodes, pruning deletion (§2, §6)."""

WILDCARD_ANY = "*"
WILDCARD_ONE = "?"


class _Node:
    __slots__ = ("children", "terminal", "value", "size")

    def __init__(self):
        self.children = {}
        self.terminal = False
        self.value = None
        self.size = 0


def _require_str(text, what):
    if not isinstance(text, str):
        raise TypeError(f"{what} must be a str, not {type(text).__name__}")


def _require_storable(key):
    _require_str(key, "key")
    if WILDCARD_ANY in key or WILDCARD_ONE in key:
        raise ValueError("a stored key must not contain '*' or '?'")


class Trie:
    def __init__(self):
        self._root = _Node()
        self._nodes = 1

    def insert(self, key, value=None):
        _require_storable(key)
        node = self._root
        path = [node]
        for ch in key:
            child = node.children.get(ch)
            if child is None:
                child = _Node()
                node.children[ch] = child
                self._nodes += 1
            node = child
            path.append(node)
        if node.terminal:
            node.value = value
            return False
        node.terminal = True
        node.value = value
        for step in path:
            step.size += 1
        return True

    def _descend(self, text):
        node = self._root
        for ch in text:
            node = node.children.get(ch)
            if node is None:
                return None
        return node

    def get(self, key, default=None):
        _require_str(key, "key")
        node = self._descend(key)
        return node.value if node is not None and node.terminal else default

    def contains(self, key):
        _require_str(key, "key")
        node = self._descend(key)
        return node is not None and node.terminal

    def starts_with(self, prefix):
        _require_str(prefix, "prefix")
        return self._descend(prefix) is not None

    def count_prefix(self, prefix):
        _require_str(prefix, "prefix")
        node = self._descend(prefix)
        return 0 if node is None else node.size

    def items(self, prefix=""):
        _require_str(prefix, "prefix")
        node = self._descend(prefix)
        out = []
        if node is None:
            return out
        stack = [(node, prefix)]
        while stack:
            current, text = stack.pop()
            if current.terminal:
                out.append((text, current.value))
            for ch in sorted(current.children, reverse=True):
                stack.append((current.children[ch], text + ch))
        return out

    def keys(self, prefix=""):
        return [key for key, _ in self.items(prefix)]

    def longest_prefix(self, query):
        _require_str(query, "query")
        node = self._root
        best = ("", node.value) if node.terminal else None
        for index, ch in enumerate(query):
            node = node.children.get(ch)
            if node is None:
                break
            if node.terminal:
                best = (query[: index + 1], node.value)
        return best

    def match(self, pattern):
        _require_str(pattern, "pattern")
        found = set()
        self._walk(self._root, pattern, 0, "", found)
        return sorted(found)

    def _walk(self, node, pattern, index, prefix, out):
        if index == len(pattern):
            if node.terminal:
                out.add(prefix)
            return
        token = pattern[index]
        if token == WILDCARD_ANY:
            self._walk(node, pattern, index + 1, prefix, out)
            for ch, child in node.children.items():
                self._walk(child, pattern, index, prefix + ch, out)
        elif token == WILDCARD_ONE:
            for ch, child in node.children.items():
                self._walk(child, pattern, index + 1, prefix + ch, out)
        else:
            child = node.children.get(token)
            if child is not None:
                self._walk(child, pattern, index + 1, prefix + token, out)

    def delete(self, key):
        _require_str(key, "key")
        node = self._root
        path = []
        for ch in key:
            child = node.children.get(ch)
            if child is None:
                return False
            path.append((node, ch))
            node = child
        if not node.terminal:
            return False
        node.terminal = False
        node.value = None
        node.size -= 1
        for parent, ch in reversed(path):
            child = parent.children[ch]
            if not child.terminal and not child.children:
                del parent.children[ch]
                self._nodes -= 1
            parent.size -= 1
        return True

    def clear(self):
        self._root = _Node()
        self._nodes = 1

    def node_count(self):
        return self._nodes

    def __len__(self):
        return self._root.size

    def __contains__(self, key):
        return self.contains(key)


def from_keys(keys):
    built = Trie()
    for key in keys:
        built.insert(key, None)
    return built
'''


_REF_PQ = '''
"""Reference indexed binary min-heap priority queue (§2-§7).

Entries are packed as ``(priority, sequence, key)`` so the tuple order *is* the
§3 order, and every array permutation goes through ``_swap``, which rewrites both
index-map entries (§4).
"""


class EmptyQueueError(IndexError):
    pass


class DuplicateKeyError(KeyError):
    pass


class MissingKeyError(KeyError):
    pass


class InvalidPriorityError(ValueError):
    pass


class InvariantError(RuntimeError):
    pass


class PriorityQueue:
    def __init__(self, entries=()):
        self._heap = []
        self._index = {}
        self._counter = 0
        for key, priority in entries:
            self._reject_nan(priority)
            if key in self._index:
                raise DuplicateKeyError(f"duplicate key {key!r}")
            self._index[key] = len(self._heap)
            self._heap.append((priority, self._counter, key))
            self._counter += 1
        for i in range(len(self._heap) // 2 - 1, -1, -1):  # Floyd, O(n)
            self._sift_down(i)

    @staticmethod
    def _reject_nan(priority):
        if priority != priority:
            raise InvalidPriorityError("priority must not be NaN")

    def _swap(self, i, j):
        self._heap[i], self._heap[j] = self._heap[j], self._heap[i]
        self._index[self._heap[i][2]] = i
        self._index[self._heap[j][2]] = j

    def _sift_up(self, i):
        while i > 0 and self._heap[i] < self._heap[(i - 1) // 2]:
            self._swap(i, (i - 1) // 2)
            i = (i - 1) // 2

    def _sift_down(self, i):
        size = len(self._heap)
        while True:
            left, right, smallest = 2 * i + 1, 2 * i + 2, i
            if left < size and self._heap[left] < self._heap[smallest]:
                smallest = left
            if right < size and self._heap[right] < self._heap[smallest]:
                smallest = right
            if smallest == i:
                return
            self._swap(i, smallest)
            i = smallest

    def _locate(self, key):
        if key not in self._index:
            raise MissingKeyError(f"no such key {key!r}")
        return self._index[key]

    def _delete_at(self, i):
        last = len(self._heap) - 1
        del self._index[self._heap[i][2]]
        if i == last:
            self._heap.pop()
            return
        self._heap[i] = self._heap.pop()
        self._index[self._heap[i][2]] = i
        self._sift_down(i)
        self._sift_up(i)

    def push(self, key, priority):
        self._reject_nan(priority)
        if key in self._index:
            raise DuplicateKeyError(f"duplicate key {key!r}")
        self._heap.append((priority, self._counter, key))
        self._index[key] = len(self._heap) - 1
        self._counter += 1
        self._sift_up(len(self._heap) - 1)

    def pop(self):
        if not self._heap:
            raise EmptyQueueError("pop from an empty priority queue")
        priority, _sequence, key = self._heap[0]
        self._delete_at(0)
        return (key, priority)

    def peek(self):
        if not self._heap:
            raise EmptyQueueError("peek at an empty priority queue")
        priority, _sequence, key = self._heap[0]
        return (key, priority)

    def _reprioritise(self, key, priority, accepts):
        self._reject_nan(priority)
        i = self._locate(key)
        if not accepts(priority, self._heap[i][0]):
            raise InvalidPriorityError(f"priority {priority!r} is on the wrong side")
        self._heap[i] = (priority, self._heap[i][1], key)
        return i

    def decrease_key(self, key, priority):
        self._sift_up(self._reprioritise(key, priority, lambda new, old: new < old))

    def increase_key(self, key, priority):
        self._sift_down(self._reprioritise(key, priority, lambda new, old: new > old))

    def remove(self, key):
        i = self._locate(key)
        priority = self._heap[i][0]
        self._delete_at(i)
        return priority

    def priority_of(self, key):
        return self._heap[self._locate(key)][0]

    def index_of(self, key):
        return self._locate(key)

    def heap_array(self):
        return [(key, priority) for priority, _sequence, key in self._heap]

    def check_invariants(self):
        if len(self._index) != len(self._heap):
            raise InvariantError("index map and heap disagree on size")
        for i, entry in enumerate(self._heap):
            key = entry[2]
            if key not in self._index or self._index[key] != i:
                raise InvariantError(f"stale index for {key!r}")
            if i > 0 and self._heap[(i - 1) // 2] > entry:
                raise InvariantError(f"heap property violated at index {i}")

    def __len__(self):
        return len(self._heap)

    def __contains__(self, key):
        return key in self._index


def heapsort(entries):
    queue = PriorityQueue(entries)
    return [queue.pop() for _ in range(len(queue))]
'''

_REF_CIRCULAR_BUFFER = '''
"""Reference circular buffer — fixed list, head plus explicit count (§2, §3.3)."""

_EMPTY = object()
"""Vacancy sentinel. Deliberately not None, which is a storable element (§5)."""


class CircularBufferError(Exception):
    pass


class BufferEmptyError(CircularBufferError, IndexError):
    pass


class BufferFullError(CircularBufferError):
    pass


class CircularBuffer:
    def __init__(self, capacity, *, overwrite=True):
        if isinstance(capacity, bool) or not isinstance(capacity, int):
            raise TypeError("capacity must be an int")
        if capacity < 1:
            raise ValueError("capacity must be >= 1")
        self._capacity = capacity
        self._overwrite = bool(overwrite)
        self._storage = [_EMPTY] * capacity
        self._head = 0
        self._count = 0
        self._version = 0

    def _physical(self, i):
        return (self._head + i) % self._capacity

    @property
    def capacity(self):
        return self._capacity

    @property
    def overwrite(self):
        return self._overwrite

    @property
    def is_full(self):
        return self._count == self._capacity

    @property
    def is_empty(self):
        return self._count == 0

    def __len__(self):
        return self._count

    def push(self, item):
        if self._count == self._capacity:
            if not self._overwrite:
                raise BufferFullError("buffer is full and overwrite is disabled")
            self._storage[self._head] = item
            self._head = (self._head + 1) % self._capacity
            self._version += 1
            return True
        self._storage[self._physical(self._count)] = item
        self._count += 1
        self._version += 1
        return False

    def push_front(self, item):
        full = self._count == self._capacity
        if full and not self._overwrite:
            raise BufferFullError("buffer is full and overwrite is disabled")
        self._head = (self._head - 1) % self._capacity
        self._storage[self._head] = item
        if not full:
            self._count += 1
        self._version += 1
        return full

    def pop(self):
        if self._count == 0:
            raise BufferEmptyError("pop from an empty buffer")
        item = self._storage[self._head]
        self._storage[self._head] = _EMPTY
        self._head = (self._head + 1) % self._capacity
        self._count -= 1
        self._version += 1
        return item

    def pop_back(self):
        if self._count == 0:
            raise BufferEmptyError("pop_back from an empty buffer")
        idx = self._physical(self._count - 1)
        item = self._storage[idx]
        self._storage[idx] = _EMPTY
        self._count -= 1
        self._version += 1
        return item

    def peek(self):
        if self._count == 0:
            raise BufferEmptyError("peek on an empty buffer")
        return self._storage[self._head]

    def peek_back(self):
        if self._count == 0:
            raise BufferEmptyError("peek_back on an empty buffer")
        return self._storage[self._physical(self._count - 1)]

    def extend(self, items):
        evicted = []
        for item in items:
            oldest = self._storage[self._head] if self._count == self._capacity else _EMPTY
            if self.push(item):
                evicted.append(oldest)
        return evicted

    def clear(self):
        for i in range(self._capacity):
            self._storage[i] = _EMPTY
        self._head = 0
        self._count = 0
        self._version += 1

    def to_list(self):
        return [self._storage[self._physical(i)] for i in range(self._count)]

    def __iter__(self):
        version = self._version
        for i in range(self._count):
            if self._version != version:
                raise RuntimeError("circular buffer mutated during iteration")
            yield self._storage[self._physical(i)]

    def __getitem__(self, index):
        if not isinstance(index, int):
            raise TypeError("buffer index must be an int")
        i = index + self._count if index < 0 else index
        if not 0 <= i < self._count:
            raise IndexError("buffer index out of range")
        return self._storage[self._physical(i)]

    def __contains__(self, item):
        for element in self:
            if element is item or element == item:
                return True
        return False

    def __repr__(self):
        return f"CircularBuffer(capacity={self._capacity}, items={self.to_list()!r})"

    def check_invariants(self):
        assert len(self._storage) == self._capacity
        assert 0 <= self._head < self._capacity
        assert 0 <= self._count <= self._capacity
        live = {self._physical(i) for i in range(self._count)}
        assert len(live) == self._count
        for idx in range(self._capacity):
            if idx not in live:
                assert self._storage[idx] is _EMPTY


def from_iterable(items, capacity, *, overwrite=True):
    buffer = CircularBuffer(capacity, overwrite=overwrite)
    buffer.extend(items)
    return buffer
'''

_REF_EDIT_DISTANCE = '''
"""Reference Levenshtein — rolling rows, full matrix backtrace, band (§3-§6)."""


class EditOp:
    __slots__ = ("kind", "index_a", "index_b", "symbol_a", "symbol_b")

    def __init__(self, kind, index_a, index_b, symbol_a, symbol_b):
        self.kind = kind
        self.index_a = index_a
        self.index_b = index_b
        self.symbol_a = symbol_a
        self.symbol_b = symbol_b

    def __repr__(self):
        return "EditOp({!r}, {!r}, {!r})".format(self.kind, self.index_a, self.index_b)


def _check_costs(*costs):
    for cost in costs:
        if cost < 0:
            raise ValueError("edit costs must be non-negative")


def distance(a, b, *, cost_insert=1, cost_delete=1, cost_substitute=1, transpositions=False):
    """§3 recurrence over three rolling rows — O(min(m, n)) space (§8)."""
    _check_costs(cost_insert, cost_delete, cost_substitute)
    if len(a) < len(b):     # shorter sequence inside; the costs swap roles with it
        a, b, cost_insert, cost_delete = b, a, cost_delete, cost_insert
    n = len(b)
    prev2 = [0] * (n + 1)
    prev = [j * cost_insert for j in range(n + 1)]
    cur = [0] * (n + 1)
    for i in range(1, len(a) + 1):
        cur[0] = i * cost_delete
        for j in range(1, n + 1):
            same = a[i - 1] == b[j - 1]
            best = min(
                prev[j] + cost_delete,
                cur[j - 1] + cost_insert,
                prev[j - 1] + (0 if same else cost_substitute),
            )
            if (
                transpositions
                and i > 1
                and j > 1
                and a[i - 1] == b[j - 2]
                and a[i - 2] == b[j - 1]
            ):
                best = min(best, prev2[j - 2] + 1)          # §3.1 restricted form
            cur[j] = best
        prev2, prev, cur = prev, cur, prev2                 # §8 swap, never reallocate
    return prev[n]


def _full_matrix(a, b, ci, cd, cs):
    """§3 — the whole table, which §4 needs and the two-row variant cannot give."""
    grid = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        grid[i][0] = i * cd
    for j in range(1, len(b) + 1):
        grid[0][j] = j * ci
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            same = a[i - 1] == b[j - 1]
            grid[i][j] = min(
                grid[i - 1][j] + cd,
                grid[i][j - 1] + ci,
                grid[i - 1][j - 1] + (0 if same else cs),
            )
    return grid


def edit_script(a, b, **costs):
    """§4 backtrace, ties broken match > substitute > delete > insert."""
    ci = costs.pop("cost_insert", 1)
    cd = costs.pop("cost_delete", 1)
    cs = costs.pop("cost_substitute", 1)
    if costs:
        raise TypeError("unexpected cost keywords: " + ", ".join(sorted(costs)))
    _check_costs(ci, cd, cs)
    grid = _full_matrix(a, b, ci, cd, cs)
    ops = []
    i, j = len(a), len(b)
    while i > 0 or j > 0:
        diagonal = grid[i - 1][j - 1] if i > 0 and j > 0 else None
        same = diagonal is not None and a[i - 1] == b[j - 1]
        if same and grid[i][j] == diagonal:
            ops.append(EditOp("match", i - 1, j - 1, a[i - 1], b[j - 1]))
            i, j = i - 1, j - 1
        elif diagonal is not None and not same and grid[i][j] == diagonal + cs:
            ops.append(EditOp("substitute", i - 1, j - 1, a[i - 1], b[j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and grid[i][j] == grid[i - 1][j] + cd:
            ops.append(EditOp("delete", i - 1, j, a[i - 1], None))
            i -= 1
        else:
            ops.append(EditOp("insert", i, j - 1, None, b[j - 1]))
            j -= 1
    ops.reverse()                                           # §4 forward order
    return ops


def apply_script(a, script):
    """§4.1 — replaying the script over a must reproduce b exactly."""
    out = []
    pos = 0
    for op in script:
        if op.kind in ("match", "substitute", "delete"):
            if pos >= len(a):
                raise ValueError("script consumes past the end of a")
            if op.kind == "match":
                out.append(a[pos])
            elif op.kind == "substitute":
                out.append(op.symbol_b)
            pos += 1
        elif op.kind == "insert":
            out.append(op.symbol_b)
        else:
            raise ValueError("unknown edit operation: " + repr(op.kind))
    if pos != len(a):
        raise ValueError("script does not consume the whole of a")
    return out


def distance_banded(a, b, max_distance):
    """§5 — only the k-diagonal band, with both early exits."""
    if max_distance < 0:
        raise ValueError("max_distance must be non-negative")
    if len(a) < len(b):
        a, b = b, a
    m, n = len(a), len(b)
    if m - n > max_distance:                    # the length gap alone exceeds k
        return max_distance + 1
    k = int(max_distance)
    big = float("inf")
    prev = [float(j) if j <= max_distance else big for j in range(n + 1)]
    cur = [big] * (n + 1)
    for i in range(1, m + 1):
        lo, hi = max(1, i - k), min(n, i + k)
        cur[0] = float(i) if i <= max_distance else big
        if lo > 1:
            cur[lo - 1] = big
        row_min = cur[0]
        for j in range(lo, hi + 1):
            same = a[i - 1] == b[j - 1]
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (0 if same else 1))
            row_min = min(row_min, cur[j])
        if hi < n:
            cur[hi + 1] = big
        if row_min > max_distance:              # no surviving cell in this row
            return max_distance + 1
        prev, cur = cur, prev
    return prev[n] if prev[n] <= max_distance else max_distance + 1


def normalized_distance(a, b):
    """§6 — [0, 1], and 0.0 rather than ZeroDivisionError for two empties."""
    longest = max(len(a), len(b))
    if longest == 0:
        return 0.0
    return distance(a, b) / longest


def similarity(a, b):
    return 1.0 - normalized_distance(a, b)
'''


_REF_TOPO = '''
"""Reference topological ordering — Kahn (§3) plus iterative Tarjan (§5)."""

import heapq


class CyclicGraphError(ValueError):
    def __init__(self, cycle):
        self.cycle = list(cycle)
        walk = " -> ".join(str(n) for n in self.cycle)
        if self.cycle:
            walk = walk + " -> " + str(self.cycle[0])
        super().__init__("graph contains a cycle: " + walk)


def _normalise(graph):
    """(order, index, succ) with successors deduplicated per source node (§2)."""
    order = []
    index = {}
    succ = {}

    def register(node):
        if node not in index:
            index[node] = len(order)
            order.append(node)
            succ[node] = []

    for u, raw in graph.items():
        register(u)
        outs = succ[u]
        seen = set(outs)
        for v in raw:
            register(v)
            if v not in seen:
                seen.add(v)
                outs.append(v)
    return order, index, succ


def _key_function(order, tie_breaker):
    """§3.1 — sort_key defaults to the node, or to insertion order if unordered."""
    if tie_breaker is not None:
        return tie_breaker
    try:
        sorted(order)
    except TypeError:
        position = {n: i for i, n in enumerate(order)}
        return lambda n: position[n]
    return lambda n: n


def _indegrees(order, succ):
    indegree = {n: 0 for n in order}
    for u in order:
        for v in succ[u]:
            indegree[v] += 1
    return indegree


def topological_sort(graph, *, tie_breaker=None):
    """§3 — the frontier is a priority queue, so the key is read once per node."""
    order, index, succ = _normalise(graph)
    key_of = _key_function(order, tie_breaker)
    indegree = _indegrees(order, succ)

    ready = []
    for n in order:
        if indegree[n] == 0:
            heapq.heappush(ready, (key_of(n), index[n]))

    out = []
    while ready:
        _, i = heapq.heappop(ready)
        u = order[i]
        out.append(u)
        for v in succ[u]:
            indegree[v] -= 1
            if indegree[v] == 0:
                heapq.heappush(ready, (key_of(v), index[v]))

    if len(out) != len(order):
        raise CyclicGraphError(find_cycle(graph))
    return out


def _walk_for_cycle(residual, succ):
    """Iterative three-colour DFS; the first back edge closes a minimal cycle (§4).

    The residual graph has dead ends — a node hanging off a cycle survives Kahn
    without lying on one — so the walk backtracks rather than trusting §4's
    "walk successors" to keep moving.
    """
    colour = {n: 0 for n in residual}
    adjacent = {n: [v for v in succ[n] if v in colour] for n in residual}
    for start in residual:
        if colour[start] != 0:
            continue
        colour[start] = 1
        path = [start]
        position = {start: 0}
        stack = [[start, 0]]
        while stack:
            frame = stack[-1]
            u, i = frame[0], frame[1]
            outs = adjacent[u]
            if i >= len(outs):
                colour[u] = 2
                stack.pop()
                path.pop()
                del position[u]
                continue
            frame[1] = i + 1
            v = outs[i]
            if colour[v] == 1:
                return path[position[v]:]
            if colour[v] == 0:
                colour[v] = 1
                position[v] = len(path)
                path.append(v)
                stack.append([v, 0])
    return None


def find_cycle(graph):
    """§4 — peel every in-degree-zero node, then walk what is left."""
    order, _index, succ = _normalise(graph)
    indegree = _indegrees(order, succ)
    queue = [n for n in order if indegree[n] == 0]
    removed = set(queue)
    head = 0
    while head < len(queue):
        u = queue[head]
        head += 1
        for v in succ[u]:
            indegree[v] -= 1
            if indegree[v] == 0:
                removed.add(v)
                queue.append(v)
    residual = [n for n in order if n not in removed]
    if not residual:
        return None
    return _walk_for_cycle(residual, succ)


def is_acyclic(graph):
    return find_cycle(graph) is None


def strongly_connected_components(graph):
    """Iterative Tarjan (§5); its emission order is already reverse topological."""
    order, _index, succ = _normalise(graph)
    key_of = _key_function(order, None)

    idx = {}
    low = {}
    on_stack = {}
    stack = []
    components = []
    counter = 0

    for root in order:
        if root in idx:
            continue
        work = [(root, 0)]
        while work:
            u, i = work.pop()
            if i == 0:
                idx[u] = counter
                low[u] = counter
                counter += 1
                stack.append(u)
                on_stack[u] = True
            outs = succ[u]
            descended = False
            while i < len(outs):
                v = outs[i]
                i += 1
                if v not in idx:
                    work.append((u, i))
                    work.append((v, 0))
                    descended = True
                    break
                if on_stack.get(v) and idx[v] < low[u]:
                    low[u] = idx[v]
            if descended:
                continue
            if low[u] == idx[u]:
                component = []
                while True:
                    w = stack.pop()
                    on_stack[w] = False
                    component.append(w)
                    if w == u:
                        break
                components.append(sorted(component, key=key_of))
            if work and low[u] < low[work[-1][0]]:
                low[work[-1][0]] = low[u]
    return components


def longest_path_length(graph):
    """§6 — relax along the topological order, which raises if the graph cycles."""
    order, _index, succ = _normalise(graph)
    distance = {n: 0 for n in order}
    best = 0
    for u in topological_sort(graph):
        for v in succ[u]:
            if distance[u] + 1 > distance[v]:
                distance[v] = distance[u] + 1
                if distance[v] > best:
                    best = distance[v]
    return best


def descendants(graph, node):
    _order, _index, succ = _normalise(graph)
    if node not in succ:
        raise KeyError(f"node {node!r} is not in the graph")
    seen = set()
    stack = [node]
    while stack:
        u = stack.pop()
        for v in succ[u]:
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return seen


def ancestors(graph, node):
    order, _index, succ = _normalise(graph)
    if node not in succ:
        raise KeyError(f"node {node!r} is not in the graph")
    predecessors = {n: [] for n in order}
    for u in order:
        for v in succ[u]:
            predecessors[v].append(u)
    seen = set()
    stack = [node]
    while stack:
        u = stack.pop()
        for v in predecessors[u]:
            if v not in seen:
                seen.add(v)
                stack.append(v)
    return seen
'''


_REF_EXPR = r'''
"""Reference expression evaluator — tokenizer + shunting-yard (§1)."""

import math

_OPERATORS = {
    "+": (1, "left", 2),
    "-": (1, "left", 2),
    "*": (2, "left", 2),
    "/": (2, "left", 2),
    "%": (2, "left", 2),
    "u-": (3, "right", 1),
    "u+": (3, "right", 1),
    "^": (4, "right", 2),
}

_CONSTANTS = {"pi": math.pi, "e": math.e}
_ARITY = {
    "abs": (1, 1), "sqrt": (1, 1), "floor": (1, 1), "ceil": (1, 1),
    "round": (1, 2), "min": (2, None), "max": (2, None),
}


class Token:
    __slots__ = ("kind", "text", "offset", "argc")

    def __init__(self, kind, text, offset):
        self.kind = kind
        self.text = text
        self.offset = offset
        self.argc = 1

    def __repr__(self):
        return f"Token({self.kind!r}, {self.text!r}, {self.offset})"

    def __eq__(self, other):
        return (
            isinstance(other, Token)
            and (self.kind, self.text, self.offset) == (other.kind, other.text, other.offset)
        )


class ExpressionError(Exception):
    def __init__(self, message, offset, expression):
        super().__init__(message)
        self.message = message
        self.offset = offset
        self.expression = expression

    def render(self):
        caret = " " * max(0, self.offset) + "^ " + self.message
        return f"{self.expression}\n{caret}"


class LexicalError(ExpressionError):
    pass


class SyntaxError_(ExpressionError):
    pass


class NameError_(ExpressionError):
    pass


class MathError(ExpressionError):
    pass


def _is_digit(ch):
    return "0" <= ch <= "9"


def _is_ident_start(ch):
    return ch.isalpha() or ch == "_"


def tokenize(expression):
    tokens = []
    i, n = 0, len(expression)
    while i < n:
        ch = expression[i]
        if ch in " \t\r\n":
            i += 1
            continue
        start = i
        if _is_digit(ch) or (ch == "." and i + 1 < n and _is_digit(expression[i + 1])):
            saw_digit = False
            while i < n and _is_digit(expression[i]):
                i += 1
                saw_digit = True
            if i < n and expression[i] == ".":
                i += 1
                while i < n and _is_digit(expression[i]):
                    i += 1
                    saw_digit = True
            if not saw_digit:
                raise LexicalError("invalid number", start, expression)
            if i < n and expression[i] in "eE":
                j = i + 1
                if j < n and expression[j] in "+-":
                    j += 1
                if j < n and _is_digit(expression[j]):
                    i = j
                    while i < n and _is_digit(expression[i]):
                        i += 1
            tokens.append(Token("NUMBER", expression[start:i], start))
            continue
        if ch == ".":
            raise LexicalError("invalid number", start, expression)
        if _is_ident_start(ch):
            while i < n and (expression[i].isalnum() or expression[i] == "_"):
                i += 1
            tokens.append(Token("IDENT", expression[start:i], start))
            continue
        if ch in "+-*/%^":
            tokens.append(Token("OPERATOR", ch, start))
            i += 1
            continue
        if ch == "(":
            tokens.append(Token("LPAREN", ch, start))
            i += 1
            continue
        if ch == ")":
            tokens.append(Token("RPAREN", ch, start))
            i += 1
            continue
        if ch == ",":
            tokens.append(Token("COMMA", ch, start))
            i += 1
            continue
        raise LexicalError(f"illegal character {ch!r}", start, expression)
    return tokens


def _number(token):
    text = token.text
    if "." in text or "e" in text or "E" in text:
        return float(text)
    return int(text)


def to_rpn(tokens, expression=""):
    tokens = list(tokens)
    if not expression and tokens:
        expression = ""
    output = []
    stack = []
    arg_counts = []
    prev = None
    for index, tok in enumerate(tokens):
        nxt = tokens[index + 1] if index + 1 < len(tokens) else None
        if tok.kind == "NUMBER":
            output.append(tok)
        elif tok.kind == "IDENT":
            if nxt is not None and nxt.kind == "LPAREN":
                stack.append(tok)
            else:
                output.append(tok)
        elif tok.kind == "COMMA":
            while stack and stack[-1].kind != "LPAREN":
                output.append(stack.pop())
            if not stack:
                raise SyntaxError_("comma outside an argument list", tok.offset, expression)
            if arg_counts:
                arg_counts[-1] += 1
        elif tok.kind == "OPERATOR":
            unary = prev is None or prev.kind in ("OPERATOR", "LPAREN", "COMMA")
            if unary and tok.text in "+-":
                op = Token("OPERATOR", "u" + tok.text, tok.offset)
            elif unary:
                raise SyntaxError_(
                    f"unexpected operator {tok.text!r}", tok.offset, expression
                )
            else:
                op = tok
            prec, assoc, _ = _OPERATORS[op.text]
            while stack and stack[-1].kind == "OPERATOR":
                top_prec, _, _ = _OPERATORS[stack[-1].text]
                if top_prec > prec or (top_prec == prec and assoc == "left"):
                    output.append(stack.pop())
                else:
                    break
            stack.append(op)
        elif tok.kind == "LPAREN":
            if prev is not None and prev.kind == "IDENT":
                arg_counts.append(1)
            stack.append(tok)
        elif tok.kind == "RPAREN":
            while stack and stack[-1].kind != "LPAREN":
                output.append(stack.pop())
            if not stack:
                raise SyntaxError_("unmatched ')'", tok.offset, expression)
            stack.pop()
            if stack and stack[-1].kind == "IDENT":
                fn = stack.pop()
                count = arg_counts.pop() if arg_counts else 1
                fn_tok = Token("CALL", fn.text, fn.offset)
                fn_tok.argc = count
                output.append(fn_tok)
        prev = tok
    while stack:
        top = stack.pop()
        if top.kind == "LPAREN":
            raise SyntaxError_("unclosed '('", top.offset, expression)
        output.append(top)
    return output


def _apply_function(name, args, offset, expression):
    if name not in _ARITY:
        raise NameError_(f"unknown function {name!r}", offset, expression)
    lo, hi = _ARITY[name]
    if len(args) < lo or (hi is not None and len(args) > hi):
        expected = f"{lo}" if hi == lo else (f"{lo} or more" if hi is None else f"{lo} to {hi}")
        raise SyntaxError_(
            f"{name}() expects {expected} arguments, got {len(args)}", offset, expression
        )
    if name == "abs":
        return abs(args[0])
    if name == "sqrt":
        if args[0] < 0:
            raise MathError("sqrt of a negative number", offset, expression)
        return math.sqrt(args[0])
    if name == "floor":
        return math.floor(args[0])
    if name == "ceil":
        return math.ceil(args[0])
    if name == "round":
        return round(*args)
    if name == "min":
        return min(args)
    return max(args)


def _binary(op, a, b, offset, expression):
    if op == "+":
        return a + b
    if op == "-":
        return a - b
    if op == "*":
        return a * b
    if op == "/":
        if b == 0:
            raise MathError("division by zero", offset, expression)
        return a / b
    if op == "%":
        if b == 0:
            raise MathError("modulo by zero", offset, expression)
        return a % b
    if op == "^":
        if a < 0 and isinstance(b, float) and b != int(b):
            raise MathError(
                "negative base with a fractional exponent", offset, expression
            )
        try:
            result = a ** b
        except ZeroDivisionError:
            raise MathError("division by zero", offset, expression) from None
        if isinstance(result, complex):
            raise MathError("result is not a real number", offset, expression)
        return result
    raise SyntaxError_(f"unknown operator {op!r}", offset, expression)


def evaluate(expression, variables=None):
    env = dict(variables) if variables else {}
    tokens = tokenize(expression)
    if not tokens:
        raise SyntaxError_("empty expression", 0, expression)
    rpn = to_rpn(tokens, expression)
    stack = []
    for tok in rpn:
        if tok.kind == "NUMBER":
            stack.append(_number(tok))
        elif tok.kind == "IDENT":
            if tok.text in env:
                stack.append(env[tok.text])
            elif tok.text in _CONSTANTS:
                stack.append(_CONSTANTS[tok.text])
            else:
                raise NameError_(f"unknown identifier {tok.text!r}", tok.offset, expression)
        elif tok.kind == "CALL":
            argc = getattr(tok, "argc", 1)
            if len(stack) < argc:
                raise SyntaxError_("malformed expression", tok.offset, expression)
            args = [stack.pop() for _ in range(argc)][::-1]
            stack.append(_apply_function(tok.text, args, tok.offset, expression))
        elif tok.kind == "OPERATOR":
            _, _, arity = _OPERATORS[tok.text]
            if len(stack) < arity:
                raise SyntaxError_("malformed expression", tok.offset, expression)
            if arity == 1:
                value = stack.pop()
                stack.append(-value if tok.text == "u-" else +value)
            else:
                b = stack.pop()
                a = stack.pop()
                stack.append(_binary(tok.text, a, b, tok.offset, expression))
    if len(stack) != 1:
        offset = rpn[-1].offset if rpn else 0
        raise SyntaxError_("malformed expression", offset, expression)
    return stack[0]
'''


def _workspace(tmp_path: Path, source: str, filename: str) -> Path:
    ws = tmp_path / "workspace"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / filename).write_text(source, encoding="utf-8")
    return ws


@pytest.mark.parametrize(
    ("oracle", "source", "filename"),
    [
        pytest.param(lru_cache.ORACLE, _REF_LRU, "cache.py", id="lru_cache"),
        pytest.param(binary_search.ORACLE, _REF_SEARCH, "search.py", id="binary_search"),
        pytest.param(online_statistics.ORACLE, _REF_STATS, "stats.py", id="online_statistics"),
        pytest.param(interval_tree.ORACLE, _REF_INTERVALS, "intervals.py", id="interval_tree"),
        pytest.param(union_find.ORACLE, _REF_UNION_FIND, "union_find.py", id="union_find"),
        pytest.param(csv_parser.ORACLE, _REF_CSV, "csv_parser.py", id="csv_parser"),
        pytest.param(
            rational_arithmetic.ORACLE, _REF_RATIONAL, "rational.py", id="rational_arithmetic"
        ),
        pytest.param(semver.ORACLE, _REF_SEMVER, "versions.py", id="semver"),
        pytest.param(priority_queue.ORACLE, _REF_PQ, "priority_queue.py", id="priority_queue"),
        pytest.param(trie.ORACLE, _REF_TRIE, "trie.py", id="trie"),
        pytest.param(topological_sort.ORACLE, _REF_TOPO, "topology.py", id="topological_sort"),
        pytest.param(
            edit_distance.ORACLE, _REF_EDIT_DISTANCE, "edit_distance.py", id="edit_distance"
        ),
        pytest.param(
            circular_buffer.ORACLE, _REF_CIRCULAR_BUFFER, "buffer.py", id="circular_buffer"
        ),
        pytest.param(
            expression_evaluator.ORACLE, _REF_EXPR, "expr.py", id="expression_evaluator"
        ),
    ],
)
def test_oracle_accepts_a_reference_implementation(
    tmp_path: Path, oracle: Oracle, source: str, filename: str
) -> None:
    """A spec-conformant implementation must pass its oracle cleanly.

    A failure here means the oracle is wrong, not the implementation — and it
    would otherwise surface as a phantom FORGE regression during a paid run.
    """
    ws = _workspace(tmp_path, source, filename)
    result = run_oracle(oracle, ws)
    assert result.ok, f"oracle rejected a conformant implementation:\n{result.summary()}"
    assert len(result.passed) >= len(oracle.cases)
