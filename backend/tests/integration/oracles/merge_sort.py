"""Oracle for whitepapers/01_stable_merge_sort.md.

Authored from the whitepaper only. No agent sees this file, and it is never
written into the workspace, so it is an independent check on whether FORGE built
what was specified rather than something self-consistently wrong.

Section references below point at the whitepaper clause each check enforces.
"""

from __future__ import annotations

from typing import Any

from backend.tests.integration.oracles._base import Case, ErrorCase, Oracle, Prohibition


def _is_stable_by_key(result: Any) -> bool:
    """§3.1 — equal keys retain their original relative order.

    Tuples are tagged with their input position, so a stable sort by first
    element leaves the tags ascending within each key group. An unstable sort
    (or one that reverses by flipping the comparator) scrambles them.
    """
    if not isinstance(result, list):
        return False
    seen: dict[Any, int] = {}
    for item in result:
        if not isinstance(item, tuple) or len(item) != 2:
            return False
        k, tag = item
        if k in seen and tag < seen[k]:
            return False
        seen[k] = tag
    return True


def _is_permutation_sorted(result: Any) -> bool:
    """§8.1 + §8.2 — output is sorted AND a permutation of the input."""
    original = [5, 3, 9, 1, 7, 3, 8, 2, 9, 4, 0, 6]
    if not isinstance(result, list):
        return False
    return result == sorted(original) and sorted(result) == sorted(original)


ORACLE = Oracle(
    whitepaper="01_stable_merge_sort.md",
    package_hint="sort",
    required_names=["sort", "sorted_copy", "is_sorted"],
    cases=[
        # §9 — boundary inputs
        Case(
            target="sorted_copy",
            args=([],),
            expected=[],
            description="§9 empty input returns empty",
        ),
        Case(
            target="sorted_copy",
            args=([42],),
            expected=[42],
            description="§9 single element",
        ),
        Case(
            target="sorted_copy",
            args=([3, 1, 2],),
            expected=[1, 2, 3],
            description="§8.1 basic ascending sort",
        ),
        Case(
            target="sorted_copy",
            args=([1, 2, 3, 4, 5],),
            expected=[1, 2, 3, 4, 5],
            description="§8.5 already-sorted input is unchanged",
        ),
        Case(
            target="sorted_copy",
            args=([5, 4, 3, 2, 1],),
            expected=[1, 2, 3, 4, 5],
            description="reverse-sorted input",
        ),
        Case(
            target="sorted_copy",
            args=([7, 7, 7, 7],),
            expected=[7, 7, 7, 7],
            description="§9 all elements equal",
        ),
        # §6 — must exercise the MIN_RUN=32 insertion-sort cutoff and the merge
        # path either side of it.
        Case(
            target="sorted_copy",
            args=(list(range(31, -1, -1)),),
            expected=list(range(32)),
            description="§2 exactly MIN_RUN elements (cutoff boundary)",
        ),
        Case(
            target="sorted_copy",
            args=(list(range(200, 0, -1)),),
            expected=list(range(1, 201)),
            description="§6 well above MIN_RUN, exercises merge",
        ),
        Case(
            target="sorted_copy",
            args=([5, 3, 9, 1, 7, 3, 8, 2, 9, 4, 0, 6],),
            check=_is_permutation_sorted,
            description="§8.2 output is a sorted permutation of the input",
        ),
        # §3 — key and reverse
        Case(
            target="sorted_copy",
            args=(["bbb", "a", "cc"],),
            kwargs={"key": len},
            expected=["a", "cc", "bbb"],
            description="§3 key function orders by length",
        ),
        Case(
            target="sorted_copy",
            args=([1, 3, 2],),
            kwargs={"reverse": True},
            expected=[3, 2, 1],
            description="§3 reverse ordering",
        ),
        # §3.1 — stability, the property the whole design exists to preserve
        Case(
            target="sorted_copy",
            args=([(2, 0), (1, 1), (2, 2), (1, 3), (2, 4)],),
            kwargs={"key": lambda t: t[0]},
            check=_is_stable_by_key,
            description="§3.1 equal keys keep input order (stability, small input)",
        ),
        # The case above stays under MIN_RUN=32 and so only exercises binary
        # insertion sort. Stability has to hold through the *merge* too, which
        # needs an input large enough to recurse — this is where a `<=` written
        # as `<` in the merge comparison shows up.
        Case(
            target="sorted_copy",
            args=([(i % 7, i) for i in range(200)],),
            kwargs={"key": lambda t: t[0]},
            check=_is_stable_by_key,
            description="§3.1 stability through the merge path (200 elements)",
        ),
        Case(
            target="sorted_copy",
            args=([(1, 0), (1, 1), (1, 2)],),
            kwargs={"key": lambda t: t[0], "reverse": True},
            check=_is_stable_by_key,
            description="§3.1 stability survives reverse=True",
        ),
        # §10 — sort() mutates in place and returns None
        Case(
            target="sort",
            args=([3, 1, 2],),
            expected=[1, 2, 3],
            mutates_arg=0,
            description="§10 sort() sorts the list in place",
        ),
        Case(
            target="sort",
            args=([3, 1, 2],),
            expected=None,
            description="§10 sort() returns None",
        ),
        # §10 — is_sorted predicate
        Case(
            target="is_sorted",
            args=([1, 2, 3],),
            expected=True,
            description="§10 is_sorted on ordered input",
        ),
        Case(
            target="is_sorted",
            args=([3, 1, 2],),
            expected=False,
            description="§10 is_sorted on unordered input",
        ),
        Case(
            target="is_sorted",
            args=([],),
            expected=True,
            description="§10 empty list is trivially sorted",
        ),
    ],
    error_cases=[
        ErrorCase(
            target="sorted_copy",
            args=([1, "a", 2],),
            exc_name="TypeError",
            description="§9 incomparable keys raise TypeError",
        ),
    ],
    prohibitions=[
        Prohibition(
            reason=(
                "§11 forbids delegating to the built-in sort — the explicit "
                "merge-sort algorithm is the deliverable, and a wrapper around "
                "list.sort would pass every functional check while implementing "
                "nothing"
            ),
            name_calls=("sorted",),
            attr_calls=("sort",),
        ),
    ],
)
