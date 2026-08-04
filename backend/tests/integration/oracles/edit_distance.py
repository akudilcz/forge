"""Oracle for whitepapers/06_edit_distance.md.

Authored from the whitepaper only; never shown to any agent.

Edit distance is the whitepaper where a *nearly* right implementation is hardest
to tell from a right one: the interior recurrence is easy, and every defect lives
in the two boundary rows or in the off-by-one between sequence indices and matrix
indices. Symmetric unit costs hide both, so the checks below deliberately use
unequal insert/delete costs and unequal lengths, where a transposed index changes
the answer instead of cancelling out.

Four things carry most of the weight:

* **Boundary rows (§3, §9.4).** Checked directly, at many lengths, with several
  cost pairs including zero, and for `str`, `list` and `tuple` inputs.
* **Variant agreement (§9.5).** The API exposes one distance entry point and one
  script builder, so the full matrix and the space-optimised row pair are
  compared *through* them: §4.1 makes the summed cost of the script (full matrix
  plus backtrace) equal the returned distance (two-row). A build whose variants
  disagree cannot satisfy both at once. The transpose identity
  ``d(a, b, ci, cd) == d(b, a, cd, ci)`` catches the classic space optimisation
  that swaps the shorter sequence into the inner loop and forgets to swap the
  insert and delete costs with it.
* **The band is real (§5, §8).** `distance_banded` is driven with symbols that
  count their own comparisons, so an implementation that fills the whole matrix
  and clamps the answer afterwards is caught even though every value it returns
  is correct.
* **Not only strings (§7).** Word lists are used throughout, and
  `distance(["ab", "cd"], ["abcd"])` is 2 for a real sequence algorithm and 0 for
  anything that joined its input into a string first.
"""

from __future__ import annotations

import functools
import random
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, ClassVar

from backend.tests.integration.oracles._base import Case, ErrorCase, Oracle, Prohibition


def _safe(check: Callable[[Any], bool]) -> Callable[[Any], bool]:
    """Turn a raise from the generated code into a reported failure.

    ``run_oracle`` guards the *call* of a case target but not the ``check``
    itself, so an exception escaping a multi-step check aborts the whole run
    instead of being collected.
    """

    @functools.wraps(check)
    def wrapper(obj: Any) -> bool:
        try:
            return check(obj)
        except Exception:  # noqa: BLE001 — any raise mid-property is a failure
            return False

    return wrapper


def _api(obj: Any, *names: str) -> dict[str, Any] | None:
    """Resolve sibling public names from the module that defines ``obj``.

    Round-tripping needs `edit_script`, `apply_script` and `distance` at once,
    but a check only receives one resolved object, and the agent may have split
    the API across modules. Returns None rather than raising, because the
    framework reports a False return and lets an exception escape.
    """
    module = sys.modules.get(getattr(obj, "__module__", ""))
    if module is None:
        return None
    root = Path(getattr(module, "__file__", "") or ".").parent
    found: dict[str, Any] = {}
    for name in names:
        if hasattr(module, name):
            found[name] = getattr(module, name)
            continue
        for other in list(sys.modules.values()):
            path = getattr(other, "__file__", None)
            if path and Path(path).parent == root and hasattr(other, name):
                found[name] = getattr(other, name)
                break
        else:
            return None
    return found


# ── independent references ───────────────────────────────────────────────────
# Deliberately shaped unlike the specified iterative row DP: a memoised top-down
# recursion, and an LCS-based closed form. Agreement across three different
# derivations is far stronger evidence than any single worked example.


def _ref_distance(
    a: Sequence[Any],
    b: Sequence[Any],
    ci: float = 1.0,
    cd: float = 1.0,
    cs: float = 1.0,
    osa: bool = False,
) -> float:
    """§3 / §3.1 recurrence, top-down with memoisation."""
    memo: dict[tuple[int, int], float] = {}

    def go(i: int, j: int) -> float:
        if i == 0:
            return j * ci
        if j == 0:
            return i * cd
        cached = memo.get((i, j))
        if cached is not None:
            return cached
        best = min(
            go(i - 1, j) + cd,
            go(i, j - 1) + ci,
            go(i - 1, j - 1) + (0.0 if a[i - 1] == b[j - 1] else cs),
        )
        if osa and i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
            best = min(best, go(i - 2, j - 2) + 1.0)
        memo[(i, j)] = best
        return best

    return go(len(a), len(b))


def _lcs_length(a: Sequence[Any], b: Sequence[Any]) -> int:
    """Longest common subsequence — the basis of the indel closed form."""
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b, 1):
            cur.append(prev[j - 1] + 1 if x == y else max(prev[j], cur[j - 1]))
        prev = cur
    return prev[-1]


_ALPHABET = "abcd"
_WORDS = ("the", "quick", "brown", "fox", "jumps", "over", "lazy", "dog")


def _random_text(rng: random.Random) -> str:
    return "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(0, 10)))


def _random_words(rng: random.Random) -> list[str]:
    return [rng.choice(_WORDS) for _ in range(rng.randint(0, 8))]


def _script_cost(script: Any, ci: float, cd: float, cs: float) -> float:
    """§4.1 — the summed cost of the non-match operations."""
    costs = {"match": 0.0, "insert": ci, "delete": cd, "substitute": cs}
    total = 0.0
    for op in script:
        if op.kind not in costs:
            raise ValueError(f"unknown operation kind {op.kind!r}")
        total += costs[op.kind]
    return total


class _Counted:
    """A symbol that records every equality comparison it takes part in.

    §5 and §8 make the band a *complexity* claim, and complexity claims are
    invisible to value-based checks. Counting comparisons makes them observable.
    """

    __slots__ = ("value",)
    comparisons: ClassVar[int] = 0

    def __init__(self, value: int) -> None:
        self.value = value

    def __eq__(self, other: object) -> bool:
        _Counted.comparisons += 1
        return isinstance(other, _Counted) and self.value == other.value

    def __hash__(self) -> int:
        return hash(self.value)


# ── properties no single call reveals ────────────────────────────────────────


@_safe
def _matches_independent_reference(distance: Any) -> bool:
    """§3 — 400 random pairs, random costs, against the memoised recursion."""
    rng = random.Random(20260806)
    for _ in range(400):
        a, b = _random_text(rng), _random_text(rng)
        ci, cd, cs = (float(rng.choice((1, 2, 3))) for _ in range(3))
        got = distance(a, b, cost_insert=ci, cost_delete=cd, cost_substitute=cs)
        if abs(got - _ref_distance(a, b, ci, cd, cs)) > 1e-9:
            return False
    return True


@_safe
def _damerau_stays_restricted(distance: Any) -> bool:
    """§3.1 — optimal string alignment, not unrestricted Damerau.

    The two agree on most inputs and differ on inputs like "CA"/"ABC", so a
    random corpus separates them where a worked example alone might not.
    """
    rng = random.Random(20260807)
    for _ in range(300):
        a, b = _random_text(rng), _random_text(rng)
        got = distance(a, b, transpositions=True)
        if abs(got - _ref_distance(a, b, osa=True)) > 1e-9:
            return False
    return True


@_safe
def _indel_distance_matches_lcs(distance: Any) -> bool:
    """§2/§10 — once substitution costs as much as a delete-then-insert pair the
    result is the indel distance, m + n - 2·LCS. An entirely different
    derivation, so it shares no failure mode with the recurrence itself."""
    rng = random.Random(20260808)
    for _ in range(200):
        a, b = _random_text(rng), _random_text(rng)
        got = distance(a, b, cost_substitute=2)
        if abs(got - (len(a) + len(b) - 2 * _lcs_length(a, b))) > 1e-9:
            return False
    return True


@_safe
def _symmetry_holds_exactly_when_specified(distance: Any) -> bool:
    """§9.2 — symmetric when insertion and deletion cost the same, and *not*
    symmetric when they differ. An implementation that normalises the argument
    order to save work passes the first half and fails the second."""
    rng = random.Random(20260809)
    asymmetry_observed = False
    for _ in range(200):
        a, b = _random_text(rng), _random_text(rng)
        if distance(a, b) != distance(b, a):
            return False
        if distance(a, b, cost_delete=4) != distance(b, a, cost_delete=4):
            asymmetry_observed = True
    return asymmetry_observed


@_safe
def _transpose_identity_holds(distance: Any) -> bool:
    """§8 — d(a, b, ci, cd) == d(b, a, cd, ci).

    The §8 space bound is O(min(m, n)), which implementations reach by putting
    the shorter sequence in the inner loop. Swapping the sequences without
    swapping the insert and delete costs is the standard bug, and it is invisible
    under unit costs.
    """
    rng = random.Random(20260810)
    for _ in range(200):
        a, b = _random_text(rng), _random_text(rng)
        left = distance(a, b, cost_insert=1, cost_delete=4, cost_substitute=3)
        right = distance(b, a, cost_insert=4, cost_delete=1, cost_substitute=3)
        if abs(left - right) > 1e-9:
            return False
    return True


@_safe
def _triangle_inequality_holds(distance: Any) -> bool:
    """§9.3 — d(a, c) <= d(a, b) + d(b, c) under default unit costs."""
    rng = random.Random(20260811)
    for _ in range(200):
        a, b, c = _random_text(rng), _random_text(rng), _random_text(rng)
        if distance(a, c) > distance(a, b) + distance(b, c) + 1e-9:
            return False
    return True


@_safe
def _identity_and_upper_bound_hold(distance: Any) -> bool:
    """§9.1 and §9.7 — d(x, x) == 0, and d never exceeds max(m, n)·max(cost)."""
    rng = random.Random(20260812)
    for _ in range(200):
        a, b = _random_text(rng), _random_text(rng)
        ci, cd, cs = (float(rng.choice((1, 2, 5))) for _ in range(3))
        if distance(a, a, cost_insert=ci, cost_delete=cd, cost_substitute=cs) != 0:
            return False
        got = distance(a, b, cost_insert=ci, cost_delete=cd, cost_substitute=cs)
        if got > max(len(a), len(b)) * max(ci, cd, cs) + 1e-9:
            return False
    return True


@_safe
def _empty_boundary_rows_are_exact(distance: Any) -> bool:
    """§3 base cases and §9.4 — d("", b) == len(b)·ci and d(a, "") == len(a)·cd.

    Swept across lengths 0..15, three container types and five cost pairs
    including zero costs, because a boundary written as ``i`` rather than
    ``i * cost_delete`` is correct for exactly the default cost.
    """
    for length in range(16):
        text = "ab" * 8
        containers: tuple[tuple[Any, Any], ...] = (
            (text[:length], ""),
            (list(text[:length]), []),
            (tuple(text[:length]), ()),
        )
        for filled, empty in containers:
            for ci, cd in ((1, 1), (2, 3), (0, 1), (1, 0), (0.5, 2.5)):
                forward = distance(empty, filled, cost_insert=ci, cost_delete=cd)
                backward = distance(filled, empty, cost_insert=ci, cost_delete=cd)
                if abs(forward - length * ci) > 1e-9:
                    return False
                if abs(backward - length * cd) > 1e-9:
                    return False
    return True


@_safe
def _script_reproduces_b_and_costs_the_distance(edit_script: Any) -> bool:
    """§4.1 and §9.5 — apply_script(a, script) == b, and the script's cost is
    exactly the returned distance.

    This is the variant-agreement check: the script comes from the full matrix
    and the distance from the space-optimised variant, so the two must land on
    the same number for every input and every cost triple.
    """
    api = _api(edit_script, "edit_script", "apply_script", "distance")
    if api is None:
        return False
    rng = random.Random(20260813)
    triples = ((1, 1, 1), (1, 1, 2), (2, 1, 1), (1, 3, 2), (1, 1, 5), (3, 2, 4))
    for trial in range(240):
        a: Any
        b: Any
        if trial % 2:
            a, b = _random_text(rng), _random_text(rng)
        else:
            a, b = _random_words(rng), _random_words(rng)
        ci, cd, cs = rng.choice(triples)
        costs = {"cost_insert": ci, "cost_delete": cd, "cost_substitute": cs}
        script = api["edit_script"](a, b, **costs)
        if list(api["apply_script"](a, script)) != list(b):
            return False
        if abs(_script_cost(script, ci, cd, cs) - api["distance"](a, b, **costs)) > 1e-9:
            return False
    return True


@_safe
def _script_symbols_line_up_with_the_inputs(edit_script: Any) -> bool:
    """§4 — every operation records the symbols it acts on.

    The operations that consume a symbol of `a` must spell out `a` in order, and
    those that produce a symbol of `b` must spell out `b`. A script whose indices
    or symbols are drawn from the wrong sequence still applies correctly if
    `apply_script` reads the same wrong field, so this is checked separately.
    """
    rng = random.Random(20260814)
    for trial in range(120):
        a: Any
        b: Any
        if trial % 2:
            a, b = _random_text(rng), _random_text(rng)
        else:
            a, b = _random_words(rng), _random_words(rng)
        script = edit_script(a, b)
        consumed = [op.symbol_a for op in script if op.kind in ("match", "substitute", "delete")]
        produced = [op.symbol_b for op in script if op.kind in ("match", "substitute", "insert")]
        if consumed != list(a) or produced != list(b):
            return False
        if any(op.symbol_a != op.symbol_b for op in script if op.kind == "match"):
            return False
    return True


@_safe
def _script_tie_break_prefers_the_diagonal(edit_script: Any) -> bool:
    """§4 — ties break match, substitute, delete, insert, in that order.

    Both inputs below have several optimal scripts of equal cost, so only the
    stated priority order fixes which one is returned:

    * "ab" -> "ba": the final cell ties across substitute, delete and insert at
      cost 2; substitute outranks both, twice over.
    * "aa" -> "a": the final cell ties between matching the second 'a' and
      deleting it; match outranks delete, which puts the deletion first.
    """
    if [op.kind for op in edit_script("ab", "ba")] != ["substitute", "substitute"]:
        return False
    return bool([op.kind for op in edit_script("aa", "a")] == ["delete", "match"])


@_safe
def _script_is_deterministic(edit_script: Any) -> bool:
    """§4 — repeated calls return the same script, and equal-but-distinct inputs
    produce equal scripts. Iterating a set or dict for the backtrace choice shows
    up here and nowhere else."""
    rng = random.Random(20260815)
    for _ in range(60):
        a, b = _random_words(rng), _random_words(rng)
        first = [(op.kind, op.symbol_a, op.symbol_b) for op in edit_script(a, b)]
        for _ in range(3):
            again = edit_script(list(a), list(b))
            if [(op.kind, op.symbol_a, op.symbol_b) for op in again] != first:
                return False
    return True


@_safe
def _script_costs_route_around_substitution(edit_script: Any) -> bool:
    """§2 — a substitution cost above cost_insert + cost_delete must make the
    script use a delete/insert pair instead. A script builder that ignores the
    cost keywords returns a single substitute and is caught here."""
    api = _api(edit_script, "edit_script", "apply_script")
    if api is None:
        return False
    script = api["edit_script"]("a", "b", cost_substitute=3)
    kinds = sorted(op.kind for op in script)
    if kinds != ["delete", "insert"]:
        return False
    return bool(list(api["apply_script"]("a", script)) == ["b"])


@_safe
def _banded_agrees_whenever_within_threshold(distance_banded: Any) -> bool:
    """§5 and §9.5 — equals the true distance when it is <= k, and returns the
    k + 1 sentinel otherwise. Swept over every k from 0 to 6 for each pair, so
    the transition either side of the threshold is exercised directly."""
    api = _api(distance_banded, "distance_banded", "distance")
    if api is None:
        return False
    rng = random.Random(20260816)
    for _ in range(150):
        a, b = _random_text(rng), _random_text(rng)
        true_distance = api["distance"](a, b)
        for k in range(7):
            got = api["distance_banded"](a, b, k)
            want = true_distance if true_distance <= k else k + 1
            if abs(got - want) > 1e-9:
                return False
    return True


@_safe
def _banded_returns_early_on_a_length_gap(distance_banded: Any) -> bool:
    """§5 — when abs(m - n) > k the answer is the k + 1 sentinel, for every k,
    including k = 0 where an empty band is easy to compute as 0."""
    rng = random.Random(20260817)
    for _ in range(150):
        a, b = _random_text(rng), _random_text(rng)
        for k in range(5):
            if abs(len(a) - len(b)) > k and abs(distance_banded(a, b, k) - (k + 1)) > 1e-9:
                return False
    return True


@_safe
def _banded_visits_only_the_band(distance_banded: Any) -> bool:
    """§5 and §8 — O(k · min(m, n)) work, not O(mn).

    400 symbols with k = 2 means a real band performs about 400 · 5 = 2000
    comparisons; filling the whole matrix performs 160,000. The bound below sits
    an order of magnitude above the first and an order below the second, so it
    catches an implementation that computes everything and then clamps.
    """
    size = 400
    a = [_Counted(i % 7) for i in range(size)]
    b = [_Counted(i % 7) for i in range(size)]
    b[137] = _Counted(99)
    _Counted.comparisons = 0
    got = distance_banded(a, b, 2)
    used = _Counted.comparisons
    return bool(got == 1 and used <= 25 * size)


@_safe
def _banded_handles_long_sequences(distance_banded: Any) -> bool:
    """§10 — 5,000-element sequences complete iteratively; no RecursionError and
    no materialised 25-million-cell matrix."""
    size = 5000
    a = [0] * size
    b = [0] * size
    b[1000] = 1
    b[3500] = 1
    return bool(distance_banded(a, b, 3) == 2)


@_safe
def _normalisation_tracks_the_distance(normalized_distance: Any) -> bool:
    """§6 — distance / max(m, n), in [0, 1], with similarity its complement and
    no ZeroDivisionError when both sequences are empty."""
    api = _api(normalized_distance, "normalized_distance", "similarity", "distance")
    if api is None:
        return False
    rng = random.Random(20260818)
    for _ in range(200):
        a, b = _random_text(rng), _random_text(rng)
        got = api["normalized_distance"](a, b)
        longest = max(len(a), len(b))
        want = 0.0 if longest == 0 else api["distance"](a, b) / longest
        if not 0.0 <= got <= 1.0 or abs(got - want) > 1e-12:
            return False
        if abs(api["similarity"](a, b) - (1.0 - got)) > 1e-12:
            return False
    return True


@_safe
def _operates_on_word_sequences(distance: Any) -> bool:
    """§7 — tokens, not characters, and no `str`-only shortcuts.

    The three literal pairs at the end all have a character-level distance that
    differs from their token-level distance, so a build that joins its input
    before measuring fails every one of them.
    """
    rng = random.Random(20260819)
    for _ in range(150):
        a, b = _random_words(rng), _random_words(rng)
        if abs(distance(a, b) - _ref_distance(a, b)) > 1e-9:
            return False
    return bool(
        distance(["ab", "cd"], ["abcd"]) == 2
        and distance(["a", "a"], ["aa"]) == 2
        and distance([(1, 2), (3, 4)], [(1, 2), (9, 9), (3, 4)]) == 1
    )


def _is_three_sevenths(value: Any) -> bool:
    return bool(abs(value - 3 / 7) < 1e-12)


def _is_four_sevenths(value: Any) -> bool:
    return bool(abs(value - 4 / 7) < 1e-12)


@_safe
def _is_three_deletes_of_abc(script: Any) -> bool:
    ops = list(script)
    return bool(
        [op.kind for op in ops] == ["delete"] * 3 and [op.symbol_a for op in ops] == ["a", "b", "c"]
    )


@_safe
def _is_three_inserts_of_abc(script: Any) -> bool:
    ops = list(script)
    return bool(
        [op.kind for op in ops] == ["insert"] * 3 and [op.symbol_b for op in ops] == ["a", "b", "c"]
    )


ORACLE = Oracle(
    whitepaper="06_edit_distance.md",
    package_hint="edit",
    required_names=[
        "distance",
        "distance_banded",
        "edit_script",
        "apply_script",
        "normalized_distance",
        "similarity",
        "EditOp",
    ],
    cases=[
        # §3 — worked examples and the classic off-by-one traps
        Case(
            target="distance",
            args=("kitten", "sitting"),
            expected=3,
            description="§3 the canonical kitten/sitting distance is 3",
        ),
        Case(
            target="distance",
            args=("saturday", "sunday"),
            expected=3,
            description="§3 saturday/sunday distance is 3",
        ),
        Case(
            target="distance",
            args=("flaw", "lawn"),
            expected=2,
            description="§3 flaw/lawn distance is 2",
        ),
        Case(
            target="distance",
            args=("abc", "abc"),
            expected=0,
            description="§9.1 identical sequences have distance 0",
        ),
        Case(
            target="distance",
            args=("abc", "xyz"),
            expected=3,
            description="§10 disjoint alphabets of equal length cost the length",
        ),
        Case(
            target="distance",
            args=("ab", "ba"),
            expected=2,
            description="§3 without transpositions a swap costs two substitutions",
        ),
        # §10 / §9.4 — the empty boundaries
        Case(
            target="distance",
            args=("", ""),
            expected=0,
            description="§10 both sequences empty gives distance 0",
        ),
        Case(
            target="distance",
            args=("", "abcd"),
            expected=4,
            description="§9.4 empty source costs len(b) insertions",
        ),
        Case(
            target="distance",
            args=("abcd", ""),
            expected=4,
            description="§9.4 empty target costs len(a) deletions",
        ),
        Case(
            target="distance",
            args=("abcd", ""),
            kwargs={"cost_delete": 0},
            expected=0,
            description="§2 a zero cost is non-negative and must be honoured",
        ),
        Case(
            target="distance",
            args=("", "x" * 10_000),
            expected=10_000,
            description="§10 a 10,000-element boundary row is iterative, not recursive",
        ),
        # §2 — the cost model actually changes the chosen alignment
        Case(
            target="distance",
            args=("abc", "xyz"),
            kwargs={"cost_substitute": 3},
            expected=6,
            description="§2 substitution dearer than delete+insert is never used",
        ),
        Case(
            target="distance",
            args=("abcdef", "abc"),
            kwargs={"cost_delete": 5},
            expected=15,
            description="§3 asymmetric costs, longer source: three deletions at 5",
        ),
        Case(
            target="distance",
            args=("abc", "abcdef"),
            kwargs={"cost_delete": 5},
            expected=3,
            description="§3 the same pair reversed costs three insertions at 1",
        ),
        # §3.1 — Damerau, restricted form
        Case(
            target="distance",
            args=("ab", "ba"),
            kwargs={"transpositions": True},
            expected=1,
            description="§3.1 a transposition costs 1 when enabled",
        ),
        Case(
            target="distance",
            args=("CA", "ABC"),
            kwargs={"transpositions": True},
            expected=3,
            description="§3.1 restricted (OSA) form: CA/ABC is 3, not the unrestricted 2",
        ),
        # §7 — sequences, not strings
        Case(
            target="distance",
            args=(["the", "quick", "brown", "fox"], ["the", "quick", "red", "fox"]),
            expected=1,
            description="§7 word lists differ by one token substitution",
        ),
        Case(
            target="distance",
            args=(["ab", "cd"], ["abcd"]),
            expected=2,
            description="§7 tokens are opaque: joining the input would give 0",
        ),
        Case(
            target="distance",
            args=((1, 2, 3), (1, 2, 3, 4)),
            expected=1,
            description="§7 tuples of ints work as well as strings",
        ),
        # §5 — banded
        Case(
            target="distance_banded",
            args=("kitten", "sitting", 3),
            expected=3,
            description="§5 banded equals the true distance when it is within k",
        ),
        Case(
            target="distance_banded",
            args=("kitten", "sitting", 1),
            expected=2,
            description="§5 a true distance above k returns the k+1 sentinel",
        ),
        Case(
            target="distance_banded",
            args=("abc", "abc", 0),
            expected=0,
            description="§5 k=0 on identical sequences is 0, not a sentinel",
        ),
        Case(
            target="distance_banded",
            args=("abcdef", "abc", 1),
            expected=2,
            description="§5 abs(m-n) > k returns k+1 without computing",
        ),
        # §6 — normalisation
        Case(
            target="normalized_distance",
            args=("kitten", "sitting"),
            check=_is_three_sevenths,
            description="§6 normalised distance divides by max(m, n)",
        ),
        Case(
            target="normalized_distance",
            args=("", ""),
            expected=0.0,
            description="§6 two empty sequences normalise to 0.0, not ZeroDivisionError",
        ),
        Case(
            target="normalized_distance",
            args=("abc", "xyz"),
            expected=1.0,
            description="§6 wholly different sequences normalise to 1.0",
        ),
        Case(
            target="similarity",
            args=("kitten", "sitting"),
            check=_is_four_sevenths,
            description="§6 similarity is 1 - normalised distance",
        ),
        Case(
            target="similarity",
            args=("", ""),
            expected=1.0,
            description="§6 two empty sequences are perfectly similar",
        ),
        Case(
            target="similarity",
            args=("abc", "xyz"),
            expected=0.0,
            description="§6 disjoint sequences have similarity 0.0",
        ),
        # §4 — scripts at the boundaries
        Case(
            target="edit_script",
            args=("", ""),
            expected=[],
            description="§10 both empty gives the empty script",
        ),
        Case(
            target="edit_script",
            args=("abc", ""),
            check=_is_three_deletes_of_abc,
            description="§4 emptying a sequence is three deletions carrying its symbols",
        ),
        Case(
            target="edit_script",
            args=("", "abc"),
            check=_is_three_inserts_of_abc,
            description="§4 filling from empty is three insertions carrying b's symbols",
        ),
        # ── properties no single call reveals ────────────────────────────────
        Case(
            target="distance",
            call=False,
            check=_matches_independent_reference,
            description="§3 agrees with an independent recursion over 400 random pairs",
        ),
        Case(
            target="distance",
            call=False,
            check=_indel_distance_matches_lcs,
            description="§10 cost_substitute=2 reproduces m+n-2·LCS",
        ),
        Case(
            target="distance",
            call=False,
            check=_damerau_stays_restricted,
            description="§3.1 transpositions give the restricted, not unrestricted, distance",
        ),
        Case(
            target="distance",
            call=False,
            check=_empty_boundary_rows_are_exact,
            description="§3 boundary rows are exact for every length, type and cost",
        ),
        Case(
            target="distance",
            call=False,
            check=_symmetry_holds_exactly_when_specified,
            description="§9.2 symmetric under equal indel costs, asymmetric otherwise",
        ),
        Case(
            target="distance",
            call=False,
            check=_transpose_identity_holds,
            description="§8 swapping the sequences must swap the insert/delete costs",
        ),
        Case(
            target="distance",
            call=False,
            check=_triangle_inequality_holds,
            description="§9.3 triangle inequality holds under unit costs",
        ),
        Case(
            target="distance",
            call=False,
            check=_identity_and_upper_bound_hold,
            description="§9.1/§9.7 identity is 0 and the upper bound is respected",
        ),
        Case(
            target="distance",
            call=False,
            check=_operates_on_word_sequences,
            description="§7 token sequences behave as sequences, not as joined text",
        ),
        Case(
            target="edit_script",
            call=False,
            check=_script_reproduces_b_and_costs_the_distance,
            description="§4.1/§9.5 script applies to give b and costs exactly the distance",
        ),
        Case(
            target="edit_script",
            call=False,
            check=_script_symbols_line_up_with_the_inputs,
            description="§4 recorded symbols spell out a and b in order",
        ),
        Case(
            target="edit_script",
            call=False,
            check=_script_tie_break_prefers_the_diagonal,
            description="§4 ties break match, substitute, delete, insert",
        ),
        Case(
            target="edit_script",
            call=False,
            check=_script_is_deterministic,
            description="§4 the returned script is deterministic across calls",
        ),
        Case(
            target="edit_script",
            call=False,
            check=_script_costs_route_around_substitution,
            description="§2 edit_script honours its cost keywords",
        ),
        Case(
            target="distance_banded",
            call=False,
            check=_banded_agrees_whenever_within_threshold,
            description="§5 banded matches the full distance for every k in 0..6",
        ),
        Case(
            target="distance_banded",
            call=False,
            check=_banded_returns_early_on_a_length_gap,
            description="§5 abs(m-n) > k always yields the k+1 sentinel",
        ),
        Case(
            target="distance_banded",
            call=False,
            check=_banded_visits_only_the_band,
            description="§8 banded does O(k·n) comparisons, not O(mn)",
        ),
        Case(
            target="distance_banded",
            call=False,
            check=_banded_handles_long_sequences,
            description="§10 5,000-element sequences complete without recursion",
        ),
        Case(
            target="normalized_distance",
            call=False,
            check=_normalisation_tracks_the_distance,
            description="§6 normalisation stays in [0, 1] and similarity complements it",
        ),
    ],
    error_cases=[
        ErrorCase(
            target="distance",
            args=("a", "b"),
            kwargs={"cost_insert": -1},
            exc_name="ValueError",
            description="§2/§10 a negative insertion cost raises ValueError",
        ),
        ErrorCase(
            target="distance",
            args=("a", "b"),
            kwargs={"cost_delete": -1},
            exc_name="ValueError",
            description="§2/§10 a negative deletion cost raises ValueError",
        ),
        ErrorCase(
            target="distance",
            args=("a", "b"),
            kwargs={"cost_substitute": -0.5},
            exc_name="ValueError",
            description="§2/§10 a negative substitution cost raises ValueError",
        ),
        ErrorCase(
            target="edit_script",
            args=("a", "b"),
            kwargs={"cost_delete": -2},
            exc_name="ValueError",
            description="§2/§10 edit_script validates its costs too",
        ),
        ErrorCase(
            target="distance_banded",
            args=("abc", "abd", -1),
            exc_name="ValueError",
            description="§10 a negative threshold raises ValueError",
        ),
    ],
    prohibitions=[
        Prohibition(
            reason=(
                "§12 forbids delegating to difflib or to a Levenshtein package — "
                "the recurrence is the subject of the specification, and a "
                "wrapper would satisfy every functional check while implementing "
                "nothing"
            ),
            imports=(
                "difflib",
                "Levenshtein",
                "rapidfuzz",
                "editdistance",
                "jellyfish",
                "textdistance",
                "polyleven",
                "pylev",
            ),
        ),
    ],
)
