"""Oracle for whitepapers/13_trie.md.

Authored from the whitepaper only; never shown to any agent.

The centre of gravity is deletion. Nearly every trie implementation gets
insert/get right on the first attempt, so the checks that discriminate are the
ones that count *nodes*: §2 pins the node set to exactly the prefix set of the
stored keys, which makes orphaning (a deleted key's chain left behind) and
over-pruning (a shared branch removed with its owner still stored) numerically
observable rather than a matter of inspection.

Wildcard matching is checked differentially against `fnmatch`, which §12 forbids
the generated code from importing — agreement therefore means the trie walk of §5
was actually implemented rather than a filter over enumerated keys.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Iterable
from fnmatch import fnmatchcase
from typing import Any

from backend.tests.integration.oracles._base import Case, ErrorCase, Oracle, Prohibition


def _safe(check: Callable[[Any], bool]) -> Callable[[Any], bool]:
    """Report a raising implementation as a failed case, not a crashed run.

    ``run_oracle`` guards the call it makes but not the check that follows, so a
    build that raises part-way through a multi-step scenario — a missing method,
    a `__len__` gone negative — would abort the whole oracle and hide every other
    result. Every check here is a conformance question, so any exception is a No.
    """

    def guarded(target: Any) -> bool:
        try:
            return check(target)
        except Exception:  # noqa: BLE001 — any raise is a conformance failure
            return False

    return guarded


def _expected_node_count(keys: Iterable[str]) -> int:
    """§2 invariant 4 — nodes are exactly the distinct prefixes, plus the root."""
    prefixes = {""}
    for key in keys:
        for i in range(len(key) + 1):
            prefixes.add(key[:i])
    return len(prefixes)


def _build(cls: Any, pairs: Iterable[tuple[str, Any]]) -> Any:
    trie = cls()
    for key, value in pairs:
        trie.insert(key, value)
    return trie


def _agrees(trie: Any, shadow: dict[str, int]) -> bool:
    """Full state comparison against an independent dict model.

    Checks content (§9.1), enumeration order (§9.6), node exactness (§9.2) and
    counter consistency (§9.7) in one pass, so a single churn case exercises all
    four after every batch of operations.
    """
    keys = sorted(shadow)
    if list(trie.keys()) != keys:
        return False
    if len(trie) != len(shadow):
        return False
    if trie.node_count() != _expected_node_count(keys):
        return False
    if list(trie.items()) != sorted(shadow.items()):
        return False
    for key, value in shadow.items():
        if trie.get(key, "MISS") != value:
            return False
    for prefix in ("", "a", "b", "ab", "ba"):
        want = sum(1 for key in shadow if key.startswith(prefix))
        if trie.count_prefix(prefix) != want:
            return False
    return True


def _insert_get_and_contains(cls: Any) -> bool:
    """§9.1 — a stored key round-trips; a bare prefix of one is not a key."""
    trie = _build(cls, [("apple", 1), ("app", 2)])
    return bool(
        trie.get("apple") == 1
        and trie.get("app") == 2
        and trie.contains("apple")
        and "app" in trie
        and not trie.contains("appl")
        and trie.get("appl", "MISS") == "MISS"
        and len(trie) == 2
    )


def _reinsert_replaces_without_growing(cls: Any) -> bool:
    """§3 / §9.1 — replacement returns False and touches no counter.

    Bumping `size` on a replacement is the classic error, and it shows up as
    count_prefix drifting above the true number of keys.
    """
    trie = cls()
    if not trie.insert("ab", 1):
        return False
    before = (len(trie), trie.node_count(), trie.count_prefix("a"))
    if trie.insert("ab", 2):
        return False
    after = (len(trie), trie.node_count(), trie.count_prefix("a"))
    return bool(after == before == (1, 3, 1) and trie.get("ab") == 2)


def _stored_none_is_not_a_miss(cls: Any) -> bool:
    """§10 — `terminal` is a flag, not `value is not None`."""
    trie = cls()
    trie.insert("k", None)
    return bool(
        trie.contains("k")
        and trie.get("k", "MISS") is None
        and list(trie.keys()) == ["k"]
        and len(trie) == 1
    )


def _node_count_is_the_prefix_set(cls: Any) -> bool:
    """§2 invariant 4 — node count is derivable from the key set alone."""
    words = ["a", "ab", "abc", "abd", "b", "", "xyz", "abc"]
    trie = _build(cls, [(w, len(w)) for w in words])
    return bool(trie.node_count() == _expected_node_count(words) == 9)


def _deleting_an_extension_prunes_the_dead_chain(cls: Any) -> bool:
    """§6 — {"car","cart"} minus "cart" must leave 4 nodes, not 5.

    Leaving 5 is the orphan: the `t` node is dead but still linked, and
    `starts_with("cart")` still answers True for a prefix nothing stores.
    """
    trie = _build(cls, [("car", 1), ("cart", 2)])
    if trie.node_count() != 5:
        return False
    if not trie.delete("cart"):
        return False
    return bool(
        trie.node_count() == 4
        and len(trie) == 1
        and trie.get("car") == 1
        and list(trie.keys()) == ["car"]
        and trie.starts_with("car")
        and not trie.starts_with("cart")
        and trie.count_prefix("c") == 1
    )


def _deleting_a_shared_prefix_key_prunes_nothing(cls: Any) -> bool:
    """§6 — deleting "car" from {"car","cart","carton"} removes no node.

    The `car` node stops being terminal but still carries a child, so pruning
    must not touch it. An over-pruning implementation destroys both survivors.
    """
    trie = _build(cls, [("car", 1), ("cart", 2), ("carton", 3)])
    if trie.node_count() != 7:
        return False
    if not trie.delete("car"):
        return False
    return bool(
        trie.node_count() == 7
        and len(trie) == 2
        and trie.get("cart") == 2
        and trie.get("carton") == 3
        and trie.get("car", "MISS") == "MISS"
        and list(trie.keys()) == ["cart", "carton"]
        and trie.count_prefix("car") == 2
    )


def _failed_delete_is_inert(cls: Any) -> bool:
    """§6 / §9.10 — a miss returns False and prunes nothing at all."""
    trie = _build(cls, [("cart", 1), ("cargo", 2)])
    before = (len(trie), trie.node_count(), list(trie.items()))
    for absent in ("ca", "c", "carg", "cartons", "dog", ""):
        if trie.delete(absent):
            return False
    after = (len(trie), trie.node_count(), list(trie.items()))
    return bool(after == before)


def _insert_delete_round_trip_restores_structure(cls: Any) -> bool:
    """§9.5 — structure is a function of content, not of history."""
    trie = _build(cls, [(w, len(w)) for w in ("alpha", "alp", "beta", "be", "b")])
    before = (len(trie), trie.node_count(), list(trie.items()))
    for word in ("alphabet", "alps", "bet", "zeta"):
        trie.insert(word, 0)
    for word in ("zeta", "bet", "alps", "alphabet"):
        if not trie.delete(word):
            return False
    after = (len(trie), trie.node_count(), list(trie.items()))
    return bool(after == before)


def _randomised_churn_preserves_node_exactness(cls: Any) -> bool:
    """§9.2 / §9.4 / §9.5 — 400 mixed operations against a dict model.

    Pruning bugs need overlapping keys and repeated delete/reinsert to surface,
    which no worked example in the whitepaper reaches. Node exactness is
    re-derived from the key set after every batch, so both orphaning and
    over-pruning are caught the moment they occur.
    """
    rng = random.Random(20260813)
    pool = sorted(
        {"".join(rng.choice("ab") for _ in range(rng.randint(1, 6))) for _ in range(60)}
    )
    trie = cls()
    shadow: dict[str, int] = {}
    for step in range(400):
        key = rng.choice(pool)
        if rng.random() < 0.6:
            trie.insert(key, step)
            shadow[key] = step
        else:
            if bool(trie.delete(key)) is not (key in shadow):
                return False
            shadow.pop(key, None)
        if step % 17 == 0 and not _agrees(trie, shadow):
            return False
    if not _agrees(trie, shadow):
        return False
    for key in sorted(shadow):
        if not trie.delete(key):
            return False
    return bool(len(trie) == 0 and trie.node_count() == 1 and list(trie.keys()) == [])


def _keys_are_sorted_independent_of_insert_order(cls: Any) -> bool:
    """§9.6 — children are visited in sorted order, not dict insertion order."""
    rng = random.Random(20260814)
    words = sorted(
        {"".join(rng.choice("abcd") for _ in range(rng.randint(1, 4))) for _ in range(40)}
    )
    shuffled = list(words)
    rng.shuffle(shuffled)
    forward = _build(cls, [(w, 0) for w in shuffled])
    backward = _build(cls, [(w, 0) for w in reversed(words)])
    return bool(
        list(forward.keys()) == words
        and list(backward.keys()) == words
        and forward.node_count() == backward.node_count() == _expected_node_count(words)
    )


def _enumeration_is_scoped_to_the_prefix(cls: Any) -> bool:
    """§4 — keys(prefix)/items(prefix) restrict to the subtree, still sorted."""
    trie = _build(cls, [(w, len(w)) for w in ("car", "cart", "carton", "cat", "dog", "")])
    return bool(
        list(trie.keys("car")) == ["car", "cart", "carton"]
        and list(trie.keys("ca")) == ["car", "cart", "carton", "cat"]
        and list(trie.keys("")) == ["", "car", "cart", "carton", "cat", "dog"]
        and list(trie.keys("z")) == []
        and list(trie.items("cart")) == [("cart", 4), ("carton", 6)]
    )


def _count_prefix_matches_enumeration(cls: Any) -> bool:
    """§9.7 — count_prefix(p) == len(keys(p)) for every prefix, before and after
    a deletion; subtree counters must be maintained by delete, not only insert."""
    words = ["a", "ab", "abc", "abd", "b", "bc", "", "cab"]
    trie = _build(cls, [(w, len(w)) for w in words])
    probes = sorted({w[:i] for w in words for i in range(len(w) + 1)} | {"z", "abcd", "ba"})

    def consistent(live: list[str]) -> bool:
        if not (trie.count_prefix("") == len(trie) == len(live)):
            return False
        return all(
            trie.count_prefix(p) == sum(1 for w in live if w.startswith(p)) == len(trie.keys(p))
            for p in probes
        )

    if not consistent(words):
        return False
    trie.delete("ab")
    trie.delete("")
    return consistent([w for w in words if w not in ("ab", "")])


def _starts_with_distinguishes_prefix_from_key(cls: Any) -> bool:
    """§4 — and, after deletion, the public detector for a leftover chain."""
    trie = _build(cls, [("cart", 1)])
    if not (trie.starts_with("ca") and trie.starts_with("cart") and trie.starts_with("")):
        return False
    if trie.contains("ca") or trie.starts_with("cartons") or trie.starts_with("z"):
        return False
    trie.delete("cart")
    return bool(not trie.starts_with("c") and not trie.starts_with("ca"))


def _longest_prefix_returns_the_longest_stored_key(cls: Any) -> bool:
    """§4 / §9.8 — the last terminal node on the walk, not the first."""
    trie = _build(cls, [("a", 1), ("ab", 2), ("abcd", 4), ("x", 9)])
    return bool(
        trie.longest_prefix("abcz") == ("ab", 2)
        and trie.longest_prefix("abcd") == ("abcd", 4)
        and trie.longest_prefix("abcde") == ("abcd", 4)
        and trie.longest_prefix("a") == ("a", 1)
        and trie.longest_prefix("zzz") is None
        and trie.longest_prefix("") is None
    )


def _longest_prefix_falls_back_to_the_empty_key(cls: Any) -> bool:
    """§4 — a stored "" is a prefix of every query, so None is wrong here."""
    trie = _build(cls, [("", 0), ("ab", 2)])
    if trie.longest_prefix("zzz") != ("", 0) or trie.longest_prefix("abc") != ("ab", 2):
        return False
    trie.delete("")
    return bool(trie.longest_prefix("zzz") is None)


def _match_agrees_with_glob_semantics(cls: Any) -> bool:
    """§9.9 — differential check against fnmatch over 120 generated keys.

    fnmatch is the independent oracle; §12 forbids the implementation from
    importing it, so this can only be satisfied by the walk of §5.
    """
    rng = random.Random(20260815)
    words = sorted(
        {"".join(rng.choice("abc") for _ in range(rng.randint(0, 5))) for _ in range(120)}
    )
    trie = _build(cls, [(w, len(w)) for w in words])
    patterns = (
        "a", "abc", "?", "??", "a?", "?c", "*", "a*", "*c", "a*c",
        "*a*", "?*", "*?", "a?c", "b*c", "*abc*", "zzz", "", "ab?c*",
    )
    for pattern in patterns:
        expected = sorted(w for w in words if fnmatchcase(w, pattern))
        if list(trie.match(pattern)) != expected:
            return False
    return True


def _match_deduplicates_adjacent_wildcards(cls: Any) -> bool:
    """§5 — the two `*` branches reach some keys twice; results must be a set.

    A recursion that appends to a list is otherwise correct and passes every
    single-star pattern, so this is the case that separates the two.
    """
    words = ["", "a", "aa", "aaa", "ab", "ba"]
    trie = _build(cls, [(w, len(w)) for w in words])
    for pattern in ("**", "*a*", "a**", "***", "*a*a*", "**?**"):
        got = list(trie.match(pattern))
        expected = sorted(w for w in words if fnmatchcase(w, pattern))
        if got != expected or len(got) != len(set(got)):
            return False
    return True


def _match_edge_patterns(cls: Any) -> bool:
    """§10 — "" matches only the empty key, "*" matches everything, and a
    wildcard-free pattern degenerates to an exact lookup."""
    trie = _build(cls, [("", 0), ("a", 1), ("ab", 2)])
    return bool(
        list(trie.match("")) == [""]
        and list(trie.match("*")) == ["", "a", "ab"]
        and list(trie.match("ab")) == ["ab"]
        and list(trie.match("abc")) == []
        and list(trie.match("?")) == ["a"]
    )


def _empty_key_is_first_class(cls: Any) -> bool:
    """§10 — it makes the root terminal, adds no node, and must not prune it."""
    trie = cls()
    if not trie.insert("", "root"):
        return False
    if not (len(trie) == 1 and trie.node_count() == 1 and trie.contains("")):
        return False
    if trie.get("") != "root" or list(trie.keys()) != [""]:
        return False
    if not trie.delete(""):
        return False
    return bool(
        len(trie) == 0
        and trie.node_count() == 1
        and list(trie.keys()) == []
        and not trie.delete("")
    )


def _very_long_key_does_not_recurse(cls: Any) -> bool:
    """§6 — path operations use a loop or path stack; 5000 > the 1000 limit.

    Deleting the key must also return the node count all the way to 1, which a
    prune loop that stops early would not.
    """
    trie = cls()
    key = "z" * 5000
    trie.insert(key, "deep")
    if trie.node_count() != 5001 or len(trie) != 1 or trie.get(key) != "deep":
        return False
    if trie.count_prefix("z" * 4000) != 1 or not trie.starts_with("z" * 4999):
        return False
    if not trie.delete(key):
        return False
    return bool(trie.node_count() == 1 and len(trie) == 0)


def _rejected_insert_leaves_state_untouched(cls: Any) -> bool:
    """§7 / §9.10 — validation precedes mutation.

    An implementation that walks first and validates later leaves a chain of
    nodes behind for a key it claimed to reject.
    """
    trie = _build(cls, [("safe", 1)])
    before = (len(trie), trie.node_count(), list(trie.items()))
    for bad_value in ("a*b", "a?b", "*", "?"):
        try:
            trie.insert(bad_value, 9)
        except ValueError:
            continue
        return False
    for bad_type in (5, None, b"bytes", ["a"]):
        try:
            trie.insert(bad_type, 9)
        except TypeError:
            continue
        return False
    after = (len(trie), trie.node_count(), list(trie.items()))
    return bool(after == before)


def _non_string_arguments_raise_type_error(cls: Any) -> bool:
    """§7 — every entry point type-checks its key, prefix, query or pattern."""
    trie = _build(cls, [("a", 1)])
    calls = (
        trie.get,
        trie.contains,
        trie.delete,
        trie.starts_with,
        trie.count_prefix,
        trie.keys,
        trie.items,
        trie.longest_prefix,
        trie.match,
    )
    for call in calls:
        for bad in (5, None):
            try:
                call(bad)
            except TypeError:
                continue
            return False
    return True


def _clear_resets_to_a_single_root(cls: Any) -> bool:
    """§10 — clear() returns the trie to one node, and it stays usable."""
    trie = _build(cls, [(w, 0) for w in ("a", "ab", "abc")])
    trie.clear()
    if not (len(trie) == 0 and trie.node_count() == 1 and list(trie.keys()) == []):
        return False
    trie.insert("z", 1)
    return bool(len(trie) == 1 and trie.node_count() == 2 and trie.get("z") == 1)


def _from_keys_builds_the_key_set(trie: Any) -> bool:
    """§11 — from_keys stores every key with value None."""
    return bool(
        list(trie.keys()) == ["a", "ab", "b"]
        and len(trie) == 3
        and trie.node_count() == _expected_node_count(["a", "ab", "b"])
        and trie.get("ab", "MISS") is None
        and trie.contains("b")
    )


def _wildcard_any_is_star(value: Any) -> bool:
    return bool(value == "*")


def _wildcard_one_is_question_mark(value: Any) -> bool:
    return bool(value == "?")


ORACLE = Oracle(
    whitepaper="13_trie.md",
    package_hint="trie",
    required_names=["Trie", "from_keys", "WILDCARD_ANY", "WILDCARD_ONE"],
    cases=[
        Case(
            target="Trie",
            call=False,
            check=_safe(_insert_get_and_contains),
            description="§9.1 stored keys round-trip; a bare prefix is not a key",
        ),
        Case(
            target="Trie",
            call=False,
            check=_safe(_reinsert_replaces_without_growing),
            description="§3 re-insert replaces the value and bumps no counter",
        ),
        Case(
            target="Trie",
            call=False,
            check=_safe(_stored_none_is_not_a_miss),
            description="§10 a stored None is not a miss",
        ),
        Case(
            target="Trie",
            call=False,
            check=_safe(_node_count_is_the_prefix_set),
            description="§2.4 node count equals the distinct-prefix count",
        ),
        Case(
            target="Trie",
            call=False,
            check=_safe(_deleting_an_extension_prunes_the_dead_chain),
            description="§6 delete('cart') from {car,cart} leaves 4 nodes (no orphan)",
        ),
        Case(
            target="Trie",
            call=False,
            check=_safe(_deleting_a_shared_prefix_key_prunes_nothing),
            description="§6 delete('car') from {car,cart,carton} prunes nothing",
        ),
        Case(
            target="Trie",
            call=False,
            check=_safe(_failed_delete_is_inert),
            description="§9.10 a failed delete returns False and prunes nothing",
        ),
        Case(
            target="Trie",
            call=False,
            check=_safe(_insert_delete_round_trip_restores_structure),
            description="§9.5 insert-then-delete restores len, nodes and items",
        ),
        Case(
            target="Trie",
            call=False,
            check=_safe(_randomised_churn_preserves_node_exactness),
            description="§9.2 node exactness survives 400 mixed operations",
        ),
        Case(
            target="Trie",
            call=False,
            check=_safe(_keys_are_sorted_independent_of_insert_order),
            description="§9.6 keys() is sorted regardless of insertion order",
        ),
        Case(
            target="Trie",
            call=False,
            check=_safe(_enumeration_is_scoped_to_the_prefix),
            description="§4 keys/items are restricted to the prefix subtree",
        ),
        Case(
            target="Trie",
            call=False,
            check=_safe(_count_prefix_matches_enumeration),
            description="§9.7 count_prefix agrees with enumeration after deletes",
        ),
        Case(
            target="Trie",
            call=False,
            check=_safe(_starts_with_distinguishes_prefix_from_key),
            description="§4 starts_with is True for prefixes and False after pruning",
        ),
        Case(
            target="Trie",
            call=False,
            check=_safe(_longest_prefix_returns_the_longest_stored_key),
            description="§9.8 longest_prefix takes the last terminal on the walk",
        ),
        Case(
            target="Trie",
            call=False,
            check=_safe(_longest_prefix_falls_back_to_the_empty_key),
            description="§4 a stored empty key is the fallback longest prefix",
        ),
        Case(
            target="Trie",
            call=False,
            check=_safe(_match_agrees_with_glob_semantics),
            description="§9.9 match() agrees with fnmatch on 19 patterns",
        ),
        Case(
            target="Trie",
            call=False,
            check=_safe(_match_deduplicates_adjacent_wildcards),
            description="§5 adjacent stars must not yield duplicate keys",
        ),
        Case(
            target="Trie",
            call=False,
            check=_safe(_match_edge_patterns),
            description="§10 '' matches only the empty key; '*' matches all",
        ),
        Case(
            target="Trie",
            call=False,
            check=_safe(_empty_key_is_first_class),
            description="§10 the empty key adds no node and never prunes the root",
        ),
        Case(
            target="Trie",
            call=False,
            check=_safe(_very_long_key_does_not_recurse),
            description="§6 a 5000-character key inserts, reads and deletes",
        ),
        Case(
            target="Trie",
            call=False,
            check=_safe(_rejected_insert_leaves_state_untouched),
            description="§7 a rejected insert leaves no partial path behind",
        ),
        Case(
            target="Trie",
            call=False,
            check=_safe(_non_string_arguments_raise_type_error),
            description="§7 non-str keys, prefixes and patterns raise TypeError",
        ),
        Case(
            target="Trie",
            call=False,
            check=_safe(_clear_resets_to_a_single_root),
            description="§10 clear() leaves exactly the root and stays usable",
        ),
        Case(
            target="from_keys",
            args=(["b", "a", "ab"],),
            check=_safe(_from_keys_builds_the_key_set),
            description="§11 from_keys stores every key with value None",
        ),
        Case(
            target="WILDCARD_ANY",
            call=False,
            check=_safe(_wildcard_any_is_star),
            description="§11 WILDCARD_ANY is '*'",
        ),
        Case(
            target="WILDCARD_ONE",
            call=False,
            check=_safe(_wildcard_one_is_question_mark),
            description="§11 WILDCARD_ONE is '?'",
        ),
    ],
    error_cases=[
        ErrorCase(
            target="from_keys",
            args=(["ok", 5],),
            exc_name="TypeError",
            description="§7 a non-str key raises TypeError",
        ),
        ErrorCase(
            target="from_keys",
            args=(["ok", None],),
            exc_name="TypeError",
            description="§7 None as a key raises TypeError",
        ),
        ErrorCase(
            target="from_keys",
            args=(["ok", "a*b"],),
            exc_name="ValueError",
            description="§7 a stored key containing '*' raises ValueError",
        ),
        ErrorCase(
            target="from_keys",
            args=(["ok", "a?"],),
            exc_name="ValueError",
            description="§7 a stored key containing '?' raises ValueError",
        ),
        ErrorCase(
            target="from_keys",
            args=(42,),
            exc_name="TypeError",
            description="§7 a non-iterable argument raises TypeError",
        ),
    ],
    prohibitions=[
        Prohibition(
            reason=(
                "§12 forbids delegating to a third-party trie package — the "
                "explicit node structure and the pruning deletion of §6 are the "
                "deliverable, and a wrapper would pass every functional check "
                "while implementing nothing"
            ),
            imports=(
                "pygtrie",
                "pytrie",
                "marisa_trie",
                "datrie",
                "hat_trie",
                "patricia_trie",
                "ahocorasick",
                "pyahocorasick",
                "trie_search",
            ),
        ),
        Prohibition(
            reason=(
                "§12 forbids re/fnmatch/glob — wildcard matching must descend the "
                "trie as in §5, not filter enumerated keys with a compiled "
                "pattern; the test suite uses fnmatch as an independent oracle"
            ),
            imports=("re", "regex", "fnmatch", "glob"),
        ),
        Prohibition(
            reason=(
                "§12 forbids str.startswith — prefix questions are answered by "
                "descending child pointers, and a startswith call is evidence "
                "that keys are being scanned linearly (violating §8)"
            ),
            attr_calls=("startswith",),
        ),
    ],
)
