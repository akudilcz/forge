"""Oracle for whitepapers/02_lru_cache.md.

Authored from the whitepaper only; never shown to any agent.

The interesting checks here are the ones a self-written test suite reliably
misses: that `contains` does *not* promote (§3), that overwriting at full
capacity evicts nothing (§4.2), and that a stored `None` is distinguishable from
a miss (§10).
"""

from __future__ import annotations

from typing import Any

from backend.tests.integration.oracles._base import Case, ErrorCase, Oracle, Prohibition


def _eviction_order_is_lru(cls: Any) -> bool:
    """§9.2 — the victim is the entry whose last *touch* is oldest."""
    cache = cls(2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.get("a")  # 'a' becomes most recent, so 'b' is now the victim
    cache.put("c", 3)
    return cache.get("b") is None and cache.get("a") == 1 and cache.get("c") == 3


def _contains_does_not_promote(cls: Any) -> bool:
    """§3 — membership testing is explicitly a non-touching read.

    If `__contains__` promoted, 'a' would survive and 'b' would be evicted.
    """
    cache = cls(2)
    cache.put("a", 1)
    cache.put("b", 2)
    assert "a" in cache
    cache.put("c", 3)
    return cache.get("a") is None and cache.get("b") == 2


def _peek_does_not_promote(cls: Any) -> bool:
    """§3 — peek returns the value without changing recency."""
    cache = cls(2)
    cache.put("a", 1)
    cache.put("b", 2)
    assert cache.peek("a") == 1
    cache.put("c", 3)
    return cache.get("a") is None


def _overwrite_at_capacity_evicts_nothing(cls: Any) -> bool:
    """§4.2 step 1 — updating an existing key must not consult capacity."""
    evicted: list[Any] = []
    cache = cls(2, on_evict=lambda k, v: evicted.append(k))
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("a", 99)
    return evicted == [] and cache.get("a") == 99 and len(cache) == 2


def _none_value_is_distinguishable_from_miss(cls: Any) -> bool:
    """§10 — a stored None must not read as absent."""
    cache = cls(2)
    cache.put("k", None)
    return ("k" in cache) and cache.get("k", "MISSING") is None


def _capacity_one_evicts_every_time(cls: Any) -> bool:
    """§10 — capacity 1 is the degenerate case that breaks off-by-one splices."""
    cache = cls(1)
    cache.put("a", 1)
    cache.put("b", 2)
    return len(cache) == 1 and cache.get("a") is None and cache.get("b") == 2


def _delete_does_not_fire_callback(cls: Any) -> bool:
    """§4.3 — the callback signals capacity pressure, not explicit removal."""
    evicted: list[Any] = []
    cache = cls(4, on_evict=lambda k, v: evicted.append(k))
    cache.put("a", 1)
    assert cache.delete("a") is True
    assert cache.delete("a") is False
    cache.clear()
    return evicted == []


def _callback_receives_key_and_value(cls: Any) -> bool:
    """§9.5 — exactly one callback per eviction, carrying key and value."""
    seen: list[tuple[Any, Any]] = []
    cache = cls(1, on_evict=lambda k, v: seen.append((k, v)))
    cache.put("a", 1)
    cache.put("b", 2)
    return seen == [("a", 1)]


def _iterates_most_recent_first(cls: Any) -> bool:
    """§7 — recency order, most recent first, without altering recency."""
    cache = cls(3)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("c", 3)
    cache.get("a")  # a is now most recent
    return list(cache.keys()) == ["a", "c", "b"]


def _invariants_hold_under_churn(cls: Any) -> bool:
    """§2.2 — the structural invariants survive a long mixed workload.

    Splice bugs in the doubly linked list usually need many operations to
    surface, so this drives several hundred mixed calls and then asks the cache
    to check itself.
    """
    cache = cls(16)
    for i in range(400):
        cache.put(i % 40, i)
        if i % 3 == 0:
            cache.get(i % 25)
        if i % 7 == 0:
            cache.delete(i % 30)
    cache.check_invariants()
    return len(cache) <= 16


def _hit_rate_starts_at_zero(cls: Any) -> bool:
    """§6 — no lookups yet must not raise ZeroDivisionError."""
    return bool(cls(4).hit_rate == 0.0)


def _statistics_count_correctly(cls: Any) -> bool:
    cache = cls(2)
    cache.put("a", 1)
    cache.get("a")
    cache.get("zzz")
    cache.put("b", 2)
    cache.put("c", 3)  # evicts one
    return bool(cache.hits == 1 and cache.misses == 1 and cache.evictions == 1)


ORACLE = Oracle(
    whitepaper="02_lru_cache.md",
    package_hint="cache",
    required_names=["LRUCache"],
    cases=[
        Case(
            target="LRUCache",
            call=False,
            check=_eviction_order_is_lru,
            description="§9.2 evicts the least recently used entry",
        ),
        Case(
            target="LRUCache",
            call=False,
            check=_contains_does_not_promote,
            description="§3 `in` does not change recency",
        ),
        Case(
            target="LRUCache",
            call=False,
            check=_peek_does_not_promote,
            description="§3 peek() does not change recency",
        ),
        Case(
            target="LRUCache",
            call=False,
            check=_overwrite_at_capacity_evicts_nothing,
            description="§4.2 overwriting an existing key evicts nothing",
        ),
        Case(
            target="LRUCache",
            call=False,
            check=_none_value_is_distinguishable_from_miss,
            description="§10 a stored None is not a miss",
        ),
        Case(
            target="LRUCache",
            call=False,
            check=_capacity_one_evicts_every_time,
            description="§10 capacity 1 behaves correctly",
        ),
        Case(
            target="LRUCache",
            call=False,
            check=_delete_does_not_fire_callback,
            description="§4.3 delete/clear do not fire on_evict",
        ),
        Case(
            target="LRUCache",
            call=False,
            check=_callback_receives_key_and_value,
            description="§9.5 on_evict receives the evicted key and value",
        ),
        Case(
            target="LRUCache",
            call=False,
            check=_iterates_most_recent_first,
            description="§7 iteration is in recency order",
        ),
        Case(
            target="LRUCache",
            call=False,
            check=_invariants_hold_under_churn,
            description="§2.2 structural invariants survive 400 mixed operations",
        ),
        Case(
            target="LRUCache",
            call=False,
            check=_hit_rate_starts_at_zero,
            description="§6 hit_rate is 0.0 before any lookup",
        ),
        Case(
            target="LRUCache",
            call=False,
            check=_statistics_count_correctly,
            description="§6 hits/misses/evictions are counted",
        ),
    ],
    error_cases=[
        ErrorCase(
            target="LRUCache",
            args=(0,),
            exc_name="ValueError",
            description="§5 capacity 0 raises ValueError",
        ),
        ErrorCase(
            target="LRUCache",
            args=(-1,),
            exc_name="ValueError",
            description="§5 negative capacity raises ValueError",
        ),
    ],
    prohibitions=[
        Prohibition(
            reason=(
                "§12 forbids OrderedDict and functools.lru_cache — the explicit "
                "hash-map-plus-linked-list construction is the deliverable"
            ),
            imports=(),
            name_calls=("OrderedDict", "lru_cache"),
        ),
    ],
)
