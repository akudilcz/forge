"""Oracle for whitepapers/15_semver.md.

Authored from the whitepaper only; never shown to any agent.

SemVer is the whitepaper where "looks right" and "is right" diverge most
cheaply. A comparator built from `tuple(map(int, core)) + prerelease` orders
every release correctly and gets *every* pre-release backwards, and a
`>=1.0.0` range that admits `2.0.0-alpha` behaves perfectly until the day it
resolves a dependency to an alpha build. So the oracle's weight is on §2's four
precedence rules and §3.2's visibility rule, not on the API surface — a naive
implementation passes everything else.

Section references below point at the whitepaper clause each check enforces.
"""

from __future__ import annotations

import random
from typing import Any

from backend.tests.integration.oracles._base import Case, ErrorCase, Oracle, Prohibition

# §2.1 — the reference chain, strictly ascending. Every neighbouring pair
# exercises a different precedence rule, and no two entries are equally
# precedent, so any correct sort must reproduce this list exactly.
_CHAIN = [
    "0.0.4",
    "0.1.0",
    "1.0.0-0",  # numeric identifier ranks below any alphanumeric one
    "1.0.0-Alpha",  # ASCII code-point order: 'A' < 'a'
    "1.0.0-alpha",
    "1.0.0-alpha.1",  # a longer identifier list is higher
    "1.0.0-alpha.beta",  # numeric "1" < alphanumeric "beta"
    "1.0.0-beta",
    "1.0.0-beta.2",
    "1.0.0-beta.11",  # numeric compare, not lexical
    "1.0.0-rc.1",
    "1.0.0",  # a pre-release is lower than the bare core
    "1.0.1",
    "1.1.0",
    "2.0.0",
    "10.0.0",  # integer core compare, not lexical
]

# A pool that additionally contains equally-precedent members (build metadata
# only), used for the algebraic properties of §5.2.
_POOL = [*_CHAIN, "1.0.0+build.1", "1.0.0+build.2", "1.0.0-beta.2+exp.sha.5114f85"]

_SHUFFLED_CHAIN = list(_CHAIN)
random.Random(20260803).shuffle(_SHUFFLED_CHAIN)


def _parses_every_component(version: Any) -> bool:
    """§1.1 — identifiers are retained verbatim, as tuples of `str`.

    An implementation that converts numeric identifiers at parse time (storing
    `1` rather than `"1"`) cannot round-trip, and one that stores lists has not
    implemented the documented type.
    """
    return bool(
        version.major == 1
        and version.minor == 20
        and version.patch == 3
        and version.prerelease == ("alpha", "1", "0valid")
        and version.build == ("build", "01")
        and version.is_prerelease is True
    )


def _release_has_empty_identifier_tuples(version: Any) -> bool:
    """§1.1 / §7 — a bare release carries empty tuples, not None."""
    return bool(
        version.major == 1
        and version.minor == 2
        and version.patch == 3
        and version.prerelease == ()
        and version.build == ()
        and version.is_prerelease is False
    )


def _round_trips_verbatim(parse: Any) -> bool:
    """§1.1 / §5.1 — `str(parse(s)) == s`, build metadata included.

    `1.0.0+001.build-7` is the discriminating case: build identifiers may carry
    leading zeroes precisely because they are never read as numbers, so an
    implementation that normalises them through `int` both corrupts the round
    trip and would have rejected the input.
    """
    for text in (
        "0.0.0",
        "1.2.3",
        "1.0.0-alpha.1",
        "1.0.0-alpha-1",
        "1.0.0+001.build-7",
        "1.0.0-x.7.z.92+exp.sha.5114f85",
        "10.20.30-0",
    ):
        if str(parse(text)) != text:
            return False
    return True


def _version_objects_are_consistent(parse: Any) -> bool:
    """§5.6 — ordering dunders follow `compare`, hash follows equality.

    A `@dataclass(frozen=True, order=True)` gets this wrong twice over: its
    `__eq__` compares build metadata, and its `__lt__` compares pre-release
    tuples lexically and puts pre-releases above releases.
    """
    a, b = parse("1.0.0+build.1"), parse("1.0.0+build.2")
    if not (a == b and hash(a) == hash(b)) or a != b:
        return False
    lo, hi = parse("1.0.0-rc.1"), parse("1.0.0")
    if not (lo < hi and hi > lo and lo <= hi and hi >= lo and lo != hi):
        return False
    # The same six operators must also answer False in the other direction; a
    # dunder that returns NotImplemented reads as truthy and passes the above.
    if lo == hi or hi < lo or lo > hi or hi <= lo or lo >= hi:
        return False
    shuffled = [parse(s) for s in _SHUFFLED_CHAIN]
    return bool([str(v) for v in sorted(shuffled)] == _CHAIN)


def _is_a_total_order(compare: Any) -> bool:
    """§5.2 — `compare` returns only -1/0/+1, is antisymmetric and transitive.

    Transitivity is the property a hand-rolled comparator loses first, because
    the numeric/alphanumeric rule makes the relation non-lexicographic.
    """
    for a in _POOL:
        for b in _POOL:
            ab, ba = compare(a, b), compare(b, a)
            if ab not in (-1, 0, 1) or ab != -ba:
                return False
    for a in _POOL:
        for b in _POOL:
            if compare(a, b) > 0:
                continue
            for c in _POOL:
                if compare(b, c) <= 0 and compare(a, c) > 0:
                    return False
    return True


def _agrees_with_tuple_order_on_releases(compare: Any) -> bool:
    """§5.3 — differential check against the stdlib tuple order.

    For versions with no pre-release, precedence is *exactly* Python's tuple
    comparison on `(major, minor, patch)`. The generated code may not import a
    version library, but the oracle can hold it against the one ordering the
    language already defines. Components run past 9 so that a string compare
    ("10" < "2") is caught.
    """
    rng = random.Random(20260804)
    for _ in range(400):
        a = (rng.randrange(14), rng.randrange(14), rng.randrange(14))
        b = (rng.randrange(14), rng.randrange(14), rng.randrange(14))
        want = (a > b) - (a < b)
        got = compare(".".join(str(n) for n in a), ".".join(str(n) for n in b))
        if got != want:
            return False
    return True


def _sort_is_permutation_invariant(sort_versions: Any) -> bool:
    """§5.5 — every permutation of a distinct-precedence pool sorts identically."""
    for seed in range(6):
        pool = list(_CHAIN)
        random.Random(seed).shuffle(pool)
        if [str(v) for v in sort_versions(pool)] != _CHAIN:
            return False
    return True


def _sort_is_stable_for_equal_precedence(sort_versions: Any) -> bool:
    """§5.5 — equally precedent elements keep their input order.

    Build metadata is the only way two distinct strings can be equally
    precedent, so this doubles as a check that the sort key ignores it.
    """
    given = ["1.0.0+c", "1.0.0+a", "1.0.0+b"]
    return bool([str(v) for v in sort_versions(given)] == given)


def _caret_desugars_correctly(satisfies: Any) -> bool:
    """§3.1 — caret keeps the left-most non-zero element."""
    expectations = [
        ("1.2.3", "^1.2.3", True),
        ("1.9.9", "^1.2.3", True),
        ("1.2.2", "^1.2.3", False),
        ("2.0.0", "^1.2.3", False),
        ("0.2.3", "^0.2.3", True),
        ("0.2.9", "^0.2.3", True),
        ("0.3.0", "^0.2.3", False),
        ("0.0.3", "^0.0.3", True),
        ("0.0.4", "^0.0.3", False),
    ]
    return all(bool(satisfies(v, r)) is want for v, r, want in expectations)


def _tilde_desugars_correctly(satisfies: Any) -> bool:
    """§3.1 — tilde always keeps major and minor, so `~0.0.3` != `^0.0.3`."""
    expectations = [
        ("1.2.3", "~1.2.3", True),
        ("1.2.9", "~1.2.3", True),
        ("1.3.0", "~1.2.3", False),
        ("0.0.3", "~0.0.3", True),
        ("0.0.9", "~0.0.3", True),
        ("0.1.0", "~0.0.3", False),
    ]
    return all(bool(satisfies(v, r)) is want for v, r, want in expectations)


def _combines_comparator_sets(satisfies: Any) -> bool:
    """§3 — conjunction within a set, disjunction across `||`, `*`, and `=`."""
    expectations = [
        ("1.2.5", ">=1.2.0 <1.3.0", True),
        ("1.3.0", ">=1.2.0 <1.3.0", False),
        ("1.1.9", ">=1.2.0 <1.3.0", False),
        ("2.0.0", "^1.0.0 || ^2.0.0", True),
        ("1.5.0", "^1.0.0 || ^2.0.0", True),
        ("3.0.0", "^1.0.0 || ^2.0.0", False),
        ("1.0.0", "*", True),
        ("99.99.99", "*", True),
        ("1.0.0", "1.0.0", True),
        ("1.0.1", "=1.0.0", False),
        # §2.4 — build metadata plays no part in matching either.
        ("1.0.0+build.5", "=1.0.0", True),
        ("1.0.0+build.5", ">1.0.0", False),
    ]
    return all(bool(satisfies(v, r)) is want for v, r, want in expectations)


def _prerelease_visibility_holds(satisfies: Any) -> bool:
    """§3.2 — the rule that makes `>=1.0.0` safe to depend on.

    A pre-release is invisible to a comparator set unless that set names a
    pre-release on the *same* core. Implementations that simply run the
    comparators admit `2.0.0-alpha` into `>=1.0.0`; implementations that
    over-correct reject `1.2.3-beta.4` from `^1.2.3-beta.2`.
    """
    expectations = [
        ("2.0.0-alpha", ">=1.0.0", False),
        ("1.0.0-alpha", "<1.0.0", False),
        ("1.0.0-rc.1", "*", False),
        ("1.5.0-rc.1", "^1.2.3", False),
        ("1.0.0-alpha", ">=1.0.0-alpha", True),
        ("1.0.0-beta", ">=1.0.0-alpha <2.0.0", True),
        ("1.2.3-beta.4", "^1.2.3-beta.2", True),
        ("1.2.3-beta.1", "^1.2.3-beta.2", False),
        ("1.2.4-alpha", "^1.2.3-beta.2", False),
        ("1.2.3", "^1.2.3-beta.2", True),
        ("1.9.0", "^1.2.3-beta.2", True),
    ]
    return all(bool(satisfies(v, r)) is want for v, r, want in expectations)


def _sorts_the_reference_chain(result: Any) -> bool:
    """§2.1 — the shuffled chain comes back in exactly the specified order."""
    return bool([str(v) for v in result] == _CHAIN)


def _picks_the_highest_satisfying(result: Any) -> bool:
    """§5.8 — 1.3.0-rc.1 outranks 1.2.9 numerically but is invisible (§3.2)."""
    return bool(str(result) == "1.2.9")


def _breaks_ties_by_input_order(result: Any) -> bool:
    """§5.8 — equally precedent candidates resolve to the earliest given."""
    return bool(str(result) == "1.0.0+first")


ORACLE = Oracle(
    whitepaper="15_semver.md",
    package_hint="ver",
    required_names=[
        "Version",
        "parse",
        "compare",
        "sort_versions",
        "satisfies",
        "max_satisfying",
        "InvalidVersionError",
        "InvalidRangeError",
    ],
    cases=[
        # ── §1 parsing ──────────────────────────────────────────────────────
        Case(
            target="parse",
            args=("1.20.3-alpha.1.0valid+build.01",),
            check=_parses_every_component,
            description="§1.1 every component is retained verbatim as tuples of str",
        ),
        Case(
            target="parse",
            args=("1.2.3",),
            check=_release_has_empty_identifier_tuples,
            description="§1.1 a bare release has empty prerelease/build tuples",
        ),
        Case(
            target="parse",
            call=False,
            check=_round_trips_verbatim,
            description="§5.1 str(parse(s)) == s, leading-zero build ids included",
        ),
        Case(
            target="parse",
            call=False,
            check=_version_objects_are_consistent,
            description="§5.6 ordering dunders follow compare and hash follows equality",
        ),
        # ── §2 precedence: one case per rule that is commonly inverted ──────
        Case(
            target="compare",
            args=("1.0.0-alpha", "1.0.0"),
            expected=-1,
            description="§2.2 a pre-release is LOWER than the bare core",
        ),
        Case(
            target="compare",
            args=("1.0.0", "1.0.0-alpha"),
            expected=1,
            description="§2.2 and the same rule seen from the other side",
        ),
        Case(
            target="compare",
            args=("1.0.0-beta.2", "1.0.0-beta.11"),
            expected=-1,
            description="§2.3 numeric identifiers compare as integers, not strings",
        ),
        Case(
            target="compare",
            args=("1.0.0-alpha.1", "1.0.0-alpha.beta"),
            expected=-1,
            description="§2.3 a numeric identifier ranks below an alphanumeric one",
        ),
        Case(
            target="compare",
            args=("1.0.0-alpha", "1.0.0-alpha.1"),
            expected=-1,
            description="§2.3 the longer identifier list is higher",
        ),
        Case(
            target="compare",
            args=("1.0.0-Alpha", "1.0.0-alpha"),
            expected=-1,
            description="§2.3 ASCII code-point order is case sensitive",
        ),
        Case(
            target="compare",
            args=("1.0.0-9", "1.0.0-99999999999999999999"),
            expected=-1,
            description="§2.3 numeric identifiers are arbitrary-precision",
        ),
        Case(
            target="compare",
            args=("2.0.0", "10.0.0"),
            expected=-1,
            description="§2.1 the core compares as integers, not strings",
        ),
        Case(
            target="compare",
            args=("1.0.0+build.1", "1.0.0+build.2"),
            expected=0,
            description="§2.4 build metadata takes no part in precedence",
        ),
        Case(
            target="compare",
            args=("1.0.0-beta.2+exp.sha.5114f85", "1.0.0-beta.2"),
            expected=0,
            description="§2.4 build metadata is ignored on pre-releases too",
        ),
        Case(
            target="compare",
            call=False,
            check=_is_a_total_order,
            description="§5.2 compare is -1/0/+1, antisymmetric and transitive",
        ),
        Case(
            target="compare",
            call=False,
            check=_agrees_with_tuple_order_on_releases,
            description="§5.3 releases order exactly as (major, minor, patch) tuples",
        ),
        # ── §2.1 the reference chain, end to end ───────────────────────────
        Case(
            target="sort_versions",
            args=(list(_SHUFFLED_CHAIN),),
            check=_sorts_the_reference_chain,
            description="§2.1 a shuffled reference chain sorts back into order",
        ),
        Case(
            target="sort_versions",
            call=False,
            check=_sort_is_permutation_invariant,
            description="§5.5 sorting is invariant across input permutations",
        ),
        Case(
            target="sort_versions",
            call=False,
            check=_sort_is_stable_for_equal_precedence,
            description="§5.5 equally precedent versions keep their input order",
        ),
        # ── §3 ranges ───────────────────────────────────────────────────────
        Case(
            target="satisfies",
            call=False,
            check=_caret_desugars_correctly,
            description="§3.1 caret keeps the left-most non-zero element",
        ),
        Case(
            target="satisfies",
            call=False,
            check=_tilde_desugars_correctly,
            description="§3.1 tilde keeps major and minor, unlike caret on 0.0.x",
        ),
        Case(
            target="satisfies",
            call=False,
            check=_combines_comparator_sets,
            description="§3 conjunction within a set, disjunction across ||, * and =",
        ),
        Case(
            target="satisfies",
            call=False,
            check=_prerelease_visibility_holds,
            description="§3.2 a pre-release is invisible unless the set names one on the same core",
        ),
        Case(
            target="satisfies",
            args=("2.0.0-alpha", ">=1.0.0"),
            expected=False,
            description="§3.2 >=1.0.0 must not admit 2.0.0-alpha",
        ),
        Case(
            target="satisfies",
            args=("1.2.3-beta.4", "^1.2.3-beta.2"),
            expected=True,
            description="§3.2 an explicit pre-release comparator opts that core in",
        ),
        # ── §5.8 max_satisfying ─────────────────────────────────────────────
        Case(
            target="max_satisfying",
            args=(["1.0.0", "1.2.0", "1.2.9", "1.3.0-rc.1", "2.0.0", "1.2.1"], "^1.2.3"),
            check=_picks_the_highest_satisfying,
            description="§5.8 the highest satisfying element wins, pre-releases excluded",
        ),
        Case(
            target="max_satisfying",
            args=(["1.0.0", "1.1.0"], "^2.0.0"),
            expected=None,
            description="§5.8 None when nothing satisfies the range",
        ),
        Case(
            target="max_satisfying",
            args=([], "*"),
            expected=None,
            description="§6 an empty iterable yields None",
        ),
        Case(
            target="max_satisfying",
            args=(["1.0.0+first", "1.0.0+second"], "=1.0.0"),
            check=_breaks_ties_by_input_order,
            description="§5.8 equal precedence resolves to the earliest input",
        ),
    ],
    error_cases=[
        ErrorCase(
            target="parse",
            args=("1.2",),
            exc_name="InvalidVersionError",
            description="§6 a partial version is rejected",
        ),
        ErrorCase(
            target="parse",
            args=("1.2.3.4",),
            exc_name="InvalidVersionError",
            description="§6 a four-component core is rejected",
        ),
        ErrorCase(
            target="parse",
            args=("01.2.3",),
            exc_name="InvalidVersionError",
            description="§1 a leading zero in the core is rejected",
        ),
        ErrorCase(
            target="parse",
            args=("1.0.0-01",),
            exc_name="InvalidVersionError",
            description="§1 a leading zero in a numeric pre-release id is rejected",
        ),
        ErrorCase(
            target="parse",
            args=("1.0.0-",),
            exc_name="InvalidVersionError",
            description="§1 an empty pre-release is rejected",
        ),
        ErrorCase(
            target="parse",
            args=("1.0.0+",),
            exc_name="InvalidVersionError",
            description="§1 an empty build is rejected",
        ),
        ErrorCase(
            target="parse",
            args=("1.0.0-alpha..1",),
            exc_name="InvalidVersionError",
            description="§1 an empty identifier inside a pre-release is rejected",
        ),
        ErrorCase(
            target="parse",
            args=("1.0.0-alpha_1",),
            exc_name="InvalidVersionError",
            description="§1 an out-of-alphabet character is rejected",
        ),
        ErrorCase(
            target="parse",
            args=("v1.2.3",),
            exc_name="InvalidVersionError",
            description="§1 a 'v' prefix is not tolerated",
        ),
        ErrorCase(
            target="parse",
            args=(" 1.2.3",),
            exc_name="InvalidVersionError",
            description="§1 surrounding whitespace is not tolerated",
        ),
        ErrorCase(
            target="parse",
            args=("1.2.-3",),
            exc_name="InvalidVersionError",
            description="§6 a negative-looking patch is rejected",
        ),
        ErrorCase(
            target="parse",
            args=("",),
            exc_name="InvalidVersionError",
            description="§6 the empty string is rejected",
        ),
        ErrorCase(
            target="parse",
            args=(123,),
            exc_name="TypeError",
            description="§6 a non-str argument raises TypeError",
        ),
        ErrorCase(
            target="Version",
            args=(-1, 0, 0),
            exc_name="InvalidVersionError",
            description="§6 a negative core component is rejected by the constructor",
        ),
        ErrorCase(
            target="Version",
            args=(1, 0, 0, ("01",)),
            exc_name="InvalidVersionError",
            description="§6 the constructor validates pre-release identifiers too",
        ),
        ErrorCase(
            target="Version",
            args=(1, 0, "3"),
            exc_name="TypeError",
            description="§6 a non-int core component raises TypeError",
        ),
        ErrorCase(
            target="compare",
            args=("1.0", "1.0.0"),
            exc_name="InvalidVersionError",
            description="§6 compare validates both operands",
        ),
        ErrorCase(
            target="satisfies",
            args=("1.0.0", ""),
            exc_name="InvalidRangeError",
            description="§6 an empty range is invalid",
        ),
        ErrorCase(
            target="satisfies",
            args=("1.0.0", ">="),
            exc_name="InvalidRangeError",
            description="§6 a bare operator is an invalid range",
        ),
        ErrorCase(
            target="satisfies",
            args=("1.0.0", "^1.2"),
            exc_name="InvalidRangeError",
            description="§3 a partial comparator version raises InvalidRangeError",
        ),
        ErrorCase(
            target="satisfies",
            args=("1.0.0", ">1.0.0 || "),
            exc_name="InvalidRangeError",
            description="§6 an empty comparator set is invalid",
        ),
        ErrorCase(
            target="satisfies",
            args=("not-a-version", "*"),
            exc_name="InvalidVersionError",
            description="§6 a malformed version still raises InvalidVersionError",
        ),
        ErrorCase(
            target="max_satisfying",
            args=(["1.0.0"], "??"),
            exc_name="InvalidRangeError",
            description="§6 max_satisfying validates the range",
        ),
    ],
    prohibitions=[
        Prohibition(
            reason=(
                "§8 forbids delegating to packaging/distutils/pkg_resources — those "
                "implement PEP 440 precedence, which disagrees with SemVer on exactly "
                "the pre-release cases this library exists to get right"
            ),
            imports=("packaging", "distutils", "pkg_resources", "setuptools"),
        ),
        Prohibition(
            reason=(
                "§8 forbids `re` — the §1 grammar must be an explicit character "
                "scanner, since a regex literal hides the leading-zero and "
                "empty-identifier rules the oracle is checking"
            ),
            imports=("re", "regex"),
        ),
    ],
)
