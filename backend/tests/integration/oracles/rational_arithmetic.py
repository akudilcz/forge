"""Oracle for whitepapers/12_rational_arithmetic.md.

Authored from the whitepaper only; never shown to any agent.

Rationals are a specification whose difficulty is entirely in invariant
maintenance, so this oracle is weighted accordingly. Almost any implementation
gets `Rational(1, 2) + Rational(1, 3)` right; what separates a real one from a
plausible one is whether the canonical form of §2 survives *every* entry point
(§9.1), whether comparison is exact rather than float-mediated (§5), and whether
`__hash__` and `__eq__` agree closely enough that equivalent literal forms
collapse to one set element (§6).

The centrepiece is a differential check against `fractions.Fraction` — the very
module §12 forbids the generated code from touching. The oracle may use it as an
independent authority precisely because the deliverable may not.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from fractions import Fraction
from typing import Any

from backend.tests.integration.oracles._base import Case, ErrorCase, Oracle, Prohibition


def _gcd(a: int, b: int) -> int:
    """The oracle's own Euclid, so canonicity is checked independently."""
    a, b = abs(a), abs(b)
    while b:
        a, b = b, a % b
    return a


def _is_canonical(value: Any) -> bool:
    """§2 — I1 (d > 0), I2 (lowest terms), I3 (zero is 0/1), I4 (plain ints)."""
    n, d = value.numerator, value.denominator
    if isinstance(n, bool) or isinstance(d, bool):
        return False
    if not isinstance(n, int) or not isinstance(d, int):
        return False
    if d <= 0 or _gcd(n, d) != 1:
        return False
    return not (n == 0 and d != 1)


def _pair(value: Any) -> tuple[Any, Any]:
    return (value.numerator, value.denominator)


def _pair_is(numerator: int, denominator: int) -> Callable[[Any], bool]:
    """Build a check comparing against a canonical pair.

    Cases cannot name the expected value directly: the oracle has no way to
    construct an instance of the *generated* class, so every expectation is
    stated as the `(numerator, denominator)` pair §2 says is unique per value.
    """

    def check(value: Any) -> bool:
        return bool(value.numerator == numerator and value.denominator == denominator)

    return check


def _seeds(cls: Any) -> list[Any]:
    return [
        cls(a, b)
        for a, b in ((1, 2), (-3, 7), (22, 7), (0, 1), (5, 1), (-9, 4), (13, 11), (-1, -6))
    ]


def _canonical_form_survives_churn(cls: Any) -> bool:
    """§9.1 — I1–I4 hold for every value produced by every operation.

    A single misplaced `normalise` shows up here and nowhere else: constructors
    are usually right, and it is the operator that builds its result field-by-field
    that leaks an unreduced or negative-denominator pair into circulation.
    """
    seeds = _seeds(cls)
    accumulator = cls(0, 1)
    for i in range(200):
        x = seeds[i % len(seeds)]
        y = seeds[(i * 5 + 3) % len(seeds)]
        produced = [x + y, x - y, x * y, -x, +x, abs(y), x**2, x**0, x + 1, 2 * x, y - 3]
        if y.numerator != 0:
            produced.extend([x / y, y.reciprocal(), 1 / y])
        for value in produced:
            if not _is_canonical(value):
                return False
        accumulator = accumulator + x * y
        if not _is_canonical(accumulator):
            return False
        if accumulator.denominator > 10**9:
            accumulator = cls(0, 1)
    return True


def _sign_is_carried_by_the_numerator(cls: Any) -> bool:
    """§2 I1 / §9.2 — the denominator is never negative, never zero."""
    expected = {
        (3, -4): (-3, 4),
        (-3, 4): (-3, 4),
        (-3, -4): (3, 4),
        (3, 4): (3, 4),
        (0, -5): (0, 1),
        (0, 7): (0, 1),
        (-6, -8): (3, 4),
        (10, -5): (-2, 1),
    }
    for (n, d), want in expected.items():
        if _pair(cls(n, d)) != want:
            return False
    return bool(_pair(-cls(1, 2)) == (-1, 2) and _pair(cls(1, -2) * cls(-1, 3)) == (1, 6))


def _matches_fractions_across_operations(cls: Any) -> bool:
    """§4 / §5 — differential comparison against the stdlib the build may not use.

    `fractions.Fraction` is forbidden to the generated code by §12 and is
    therefore an independent authority here. 150 random operand pairs across
    every operator catch sign errors, reversed operands in `__rsub__` /
    `__rtruediv__`, and off-by-one exponent handling that worked examples miss.
    """
    rng = random.Random(20260803)
    for _ in range(150):
        a = rng.randint(-60, 60)
        b = rng.randint(-60, 60) or 7
        c = rng.randint(-60, 60)
        d = rng.randint(-60, 60) or 11
        x, y = cls(a, b), cls(c, d)
        fx, fy = Fraction(a, b), Fraction(c, d)

        compared: list[tuple[Any, Fraction]] = [
            (x + y, fx + fy),
            (x - y, fx - fy),
            (x * y, fx * fy),
            (-x, -fx),
            (abs(y), abs(fy)),
            (x + c, fx + c),
            (c - x, c - fx),
            (c * y, c * fy),
        ]
        if c != 0:
            compared.extend([(x / y, fx / fy), (y.reciprocal(), 1 / fy), (a / y, a / fy)])
        for got, want in compared:
            if _pair(got) != (want.numerator, want.denominator):
                return False

        for k in (-2, -1, 0, 1, 3):
            if a == 0 and k < 0:
                continue
            if _pair(x**k) != ((fx**k).numerator, (fx**k).denominator):
                return False

        got_order = (x < y, x <= y, x > y, x >= y, x == y)
        want_order = (fx < fy, fx <= fy, fx > fy, fx >= fy, fx == fy)
        if got_order != want_order:
            return False
    return True


def _arithmetic_is_exact(cls: Any) -> bool:
    """§9.3 — no information is lost, including where float loses it."""
    third = cls(1, 3)
    if third + third + third != cls(1, 1):
        return False
    total = cls(0, 1)
    for _ in range(10):
        total = total + cls(1, 10)
    if total != cls(1, 1):
        return False
    for a, b, c, d in ((3, 7, 11, 13), (-5, 9, 2, 3), (1, 1000003, 7, 5)):
        x, y = cls(a, b), cls(c, d)
        if (x / y) * y != x:
            return False
    return bool(cls(1, 3) * 3 == cls(1, 1))


def _comparison_does_not_go_through_float(cls: Any) -> bool:
    """§5 — the two values below have bit-identical float images, yet differ.

    Any comparison implemented as `self.to_float() < other.to_float()` reports
    them equal. The premise is verified with the oracle's own floats so a broken
    `to_float` cannot make this case pass vacuously.
    """
    assert (10**18) / 3 == (10**18 + 1) / 3
    assert (10**17) / 7 == (10**17 + 1) / 7
    x, y = cls(10**18, 3), cls(10**18 + 1, 3)
    if not (x < y) or x == y or x >= y or not (y > x):
        return False
    if (y - x).numerator <= 0:  # §9.6
        return False
    p, q = cls(10**17, 7), cls(10**17 + 1, 7)
    return bool(p < q and p != q and q >= p)


def _equality_and_hash_agree(cls: Any) -> bool:
    """§6 / §9.4 — equivalent literal forms collapse to one set element."""
    forms = [cls(1, 2), cls(2, 4), cls(-1, -2), cls(50, 100), cls(3, 6)]
    if len(set(forms)) != 1 or len({_pair(v) for v in forms}) != 1:
        return False
    keyed = {value: index for index, value in enumerate(forms)}
    if len(keyed) != 1 or keyed[cls(1, 2)] != 4:
        return False
    if len({cls(1, 2), cls(1, 3), cls(2, 4), cls(-1, 3)}) != 3:
        return False
    for a, b in ((1, 2), (3, 4), (-7, 9), (5, 1), (0, 1)):
        x, y = cls(a, b), cls(a * 3, b * 3)
        if x != y or hash(x) != hash(y):
            return False
    return True


def _hash_follows_the_specified_formula(cls: Any) -> bool:
    """§6 — hash(n) when d == 1, else hash((n, d)), exactly.

    The integral half is what makes `{Rational(4, 2), 2}` a one-element set, so
    it is a behavioural requirement rather than an implementation detail.
    """
    for k in (0, 1, -1, 7, -12345, 2**70):
        x = cls(k, 1)
        if hash(x) != hash(k) or x != k or len({x, k}) != 1:
            return False
    if hash(cls(6, 3)) != hash(2) or len({cls(6, 3), 2}) != 1:
        return False
    return bool(hash(cls(3, 4)) == hash((3, 4)) and hash(cls(-6, 8)) == hash((-3, 4)))


def _instances_are_immutable(cls: Any) -> bool:
    """§6 / §9.7 — operands are unchanged and the fields are read-only."""
    x, y = cls(1, 2), cls(3, 4)
    before = (_pair(x), _pair(y))
    for _ in (x + y, x - y, x * y, x / y, -x, abs(y), x**3, y.reciprocal()):
        pass
    if (_pair(x), _pair(y)) != before:
        return False
    for attribute in ("numerator", "denominator"):
        try:
            setattr(x, attribute, 99)
        except AttributeError:
            continue
        return False
    return bool(_pair(x) == (1, 2))


def _field_axioms_hold(cls: Any) -> bool:
    """§9.5 — associativity, commutativity, distributivity, identities, inverses."""
    rng = random.Random(20260804)
    zero, one = cls(0, 1), cls(1, 1)
    for _ in range(60):
        x = cls(rng.randint(-40, 40), rng.randint(-40, 40) or 3)
        y = cls(rng.randint(-40, 40), rng.randint(-40, 40) or 5)
        z = cls(rng.randint(-40, 40), rng.randint(-40, 40) or 7)
        if (x + y) + z != x + (y + z) or (x * y) * z != x * (y * z):
            return False
        if x + y != y + x or x * y != y * x:
            return False
        if x * (y + z) != x * y + x * z:
            return False
        if x + zero != x or x * one != x or x - x != zero:
            return False
        if x.numerator != 0 and x * x.reciprocal() != one:
            return False
    return True


def _int_operands_are_accepted_on_both_sides(cls: Any) -> bool:
    """§4.1 — an int is k/1, and the reflected operators supply the left case."""
    half = cls(1, 2)
    if _pair(half + 1) != (3, 2) or _pair(1 + half) != (3, 2):
        return False
    if _pair(half - 1) != (-1, 2) or _pair(1 - half) != (1, 2):
        return False
    if _pair(half * 4) != (2, 1) or _pair(4 * half) != (2, 1):
        return False
    if _pair(half / 2) != (1, 4) or _pair(2 / half) != (4, 1):
        return False
    if not (half < 1) or not (1 > half) or half == 1:
        return False
    return bool(cls(4, 2) == 2 and 2 == cls(4, 2) and cls(4, 2) <= 2)


def _float_and_bool_operands_are_rejected(cls: Any) -> bool:
    """§4.1 / §10 — inexact operands raise; `==` against a float is False, not an error."""
    half = cls(1, 2)
    rejected: list[Callable[[], Any]] = [
        lambda: half + 0.5,
        lambda: 0.5 + half,
        lambda: half - 0.5,
        lambda: half * 0.5,
        lambda: half / 0.5,
        lambda: 0.5 / half,
        lambda: half < 0.5,
        lambda: half >= 0.5,
        lambda: half + True,
        lambda: True + half,
        lambda: half + "1",
    ]
    for thunk in rejected:
        try:
            thunk()
        except TypeError:
            continue
        return False
    return bool((half == 0.5) is False and (half != 0.5) is True and (half == "1/2") is False)


def _zero_denominators_raise_zero_division(cls: Any) -> bool:
    """§10 — every route to a zero denominator raises ZeroDivisionError."""
    zero = cls(0, 1)
    routes: list[Callable[[], Any]] = [
        lambda: cls(1, 2) / zero,
        lambda: cls(1, 2) / 0,
        lambda: 1 / zero,
        lambda: zero.reciprocal(),
        lambda: zero**-1,
        lambda: zero**-3,
    ]
    for thunk in routes:
        try:
            thunk()
        except ZeroDivisionError:
            continue
        return False
    return True


def _power_edge_cases(cls: Any) -> bool:
    """§4 — the sign and the reciprocal both have to be right at once."""
    if _pair(cls(0, 1) ** 0) != (1, 1) or _pair(cls(0, 1) ** 3) != (0, 1):
        return False
    if _pair(cls(2, 3) ** -2) != (9, 4) or _pair(cls(-2, 3) ** -1) != (-3, 2):
        return False
    if _pair(cls(-2, 3) ** 3) != (-8, 27) or _pair(cls(-2, 3) ** 2) != (4, 9):
        return False
    return bool(_pair(cls(-2, 3) ** -2) == (9, 4) and _pair(cls(7, 5) ** 1) == (7, 5))


def _huge_magnitudes_stay_exact(cls: Any) -> bool:
    """§10 — Python ints do not overflow, so exact arithmetic must not either."""
    big = cls(10**400 + 1, 3)
    if _pair(big * 3) != (10**400 + 1, 1):
        return False
    if _pair(cls(10**200, 3 * 10**200)) != (1, 3):
        return False
    return bool(big - big == cls(0, 1) and big > cls(10**399, 3))


def _to_float_converts_and_overflows(cls: Any) -> bool:
    """§7 / §10 — ordinary conversion works; out-of-range propagates OverflowError."""
    if cls(1, 4).to_float() != 0.25 or cls(-3, 2).to_float() != -1.5:
        return False
    if cls(1, 3).to_float() != 1 / 3 or cls(0, 1).to_float() != 0.0:
        return False
    try:
        cls(10**400, 3).to_float()
    except OverflowError:
        return True
    return False


def _text_representation(cls: Any) -> bool:
    """§6 — str drops a unit denominator; repr always shows both fields."""
    if str(cls(3, 4)) != "3/4" or str(cls(6, 3)) != "2" or str(cls(0, 5)) != "0":
        return False
    if str(cls(-1, 2)) != "-1/2" or str(cls(1, -2)) != "-1/2":
        return False
    return bool(repr(cls(3, 4)) == "Rational(3, 4)" and repr(cls(6, 3)) == "Rational(2, 1)")


def _limit_denominator_is_optimal(cls: Any) -> bool:
    """§9.9 — brute force every admissible denominator and compare.

    A truncated continued fraction that stops one convergent early is still a
    good approximation, so only an exhaustive search over denominators 1..m
    distinguishes it from the optimal one.
    """
    for num, den in ((3141592653589793, 1000000000000000), (-355, 113), (99991, 100003)):
        target = Fraction(num, den)
        for m in range(1, 26):
            got = cls(num, den).limit_denominator(m)
            if got.denominator > m or not _is_canonical(got):
                return False
            candidates: list[Fraction] = []
            for q in range(1, m + 1):
                p = (target.numerator * q) // target.denominator
                candidates.extend((Fraction(p, q), Fraction(p + 1, q)))
            best = min(abs(c - target) for c in candidates)
            if abs(Fraction(got.numerator, got.denominator) - target) != best:
                return False
            if got.denominator != min(c.denominator for c in candidates if abs(c - target) == best):
                return False
    return True


def _limit_denominator_boundaries(cls: Any) -> bool:
    """§7 / §9.9 — worked examples, the no-op case, and the rejected bound."""
    pi = cls(3141592653589793, 1000000000000000)
    if _pair(pi.limit_denominator(10)) != (22, 7):
        return False
    if _pair(pi.limit_denominator(100)) != (311, 99):
        return False
    third = cls(1, 3)
    if third.limit_denominator(10) != third or third.limit_denominator(3) != third:
        return False
    if _pair(cls(0, 1).limit_denominator(1)) != (0, 1):
        return False
    for bad in (0, -5):
        try:
            pi.limit_denominator(bad)
        except ValueError:
            continue
        return False
    return True


def _results_are_deterministic(cls: Any) -> bool:
    """§9.10 — repeated evaluation gives identical pairs, strings, and hashes."""

    def build() -> tuple[Any, str, int]:
        value = cls(0, 1)
        for i in range(1, 40):
            value = value + cls((-1) ** i, i)
        return _pair(value), str(value), hash(value)

    first = build()
    return bool(first == build() and first == build())


def _parse_round_trips(fn: Any) -> bool:
    """§6 / §9.8 — parse(str(x)) == x, via the class the parser itself returns."""
    cls = type(fn("1"))
    for a, b in ((3, 4), (-3, 4), (5, 1), (0, 1), (-22, 7), (10**30 + 1, 7)):
        value = cls(a, b)
        parsed = fn(str(value))
        if _pair(parsed) != _pair(value) or parsed != value:
            return False
    return True


def _parse_accepts_the_grammar(fn: Any) -> bool:
    """§3 — signs on either side, surrounding whitespace, and leading zeros."""
    expected = {
        "3/4": (3, 4),
        " -6/-8 ": (3, 4),
        "+3/-4": (-3, 4),
        "007/002": (7, 2),
        "5": (5, 1),
        "-0/-3": (0, 1),
        "\t12/8\n": (3, 2),
        "+7": (7, 1),
    }
    for text, want in expected.items():
        if _pair(fn(text)) != want:
            return False
    return True


def _rational_sum_is_exact_and_streaming(fn: Any) -> bool:
    """§7 / §9.3 — exact harmonic sum, int elements, one-shot iterator accepted.

    The generator argument is the load-bearing part: an implementation that
    indexes or measures `values` before summing rejects it outright.
    """
    cls = type(fn([]))
    if _pair(fn([])) != (0, 1):
        return False
    if _pair(fn(cls(1, i) for i in range(1, 11))) != (7381, 2520):
        return False
    if _pair(fn(iter([1, 2, 3]))) != (6, 1):
        return False
    if _pair(fn([cls(1, 3), 2, cls(-1, 3)])) != (2, 1):
        return False
    tenths = fn([cls(1, 10)] * 10)
    return bool(_pair(tenths) == (1, 1))


ORACLE = Oracle(
    whitepaper="12_rational_arithmetic.md",
    package_hint="rational",
    required_names=["Rational", "gcd", "parse_rational", "approximate", "rational_sum"],
    cases=[
        # §2 — the invariant the whole specification exists to maintain
        Case(
            target="Rational",
            call=False,
            check=_canonical_form_survives_churn,
            description="§9.1 canonical form holds across ~2800 produced values",
        ),
        Case(
            target="Rational",
            call=False,
            check=_sign_is_carried_by_the_numerator,
            description="§9.2 the denominator is never negative",
        ),
        # §4/§5 — differential comparison against the forbidden stdlib
        Case(
            target="Rational",
            call=False,
            check=_matches_fractions_across_operations,
            description="§4 all operators agree with fractions.Fraction over 150 random pairs",
        ),
        Case(
            target="Rational",
            call=False,
            check=_arithmetic_is_exact,
            description="§9.3 1/3+1/3+1/3 == 1 and (x/y)*y == x",
        ),
        Case(
            target="Rational",
            call=False,
            check=_comparison_does_not_go_through_float,
            description="§5 ordering is exact where the float images collide",
        ),
        # §6 — equality, hashing, immutability
        Case(
            target="Rational",
            call=False,
            check=_equality_and_hash_agree,
            description="§9.4 equivalent forms are one set element and one dict key",
        ),
        Case(
            target="Rational",
            call=False,
            check=_hash_follows_the_specified_formula,
            description="§6 integral rationals hash as the equal int",
        ),
        Case(
            target="Rational",
            call=False,
            check=_instances_are_immutable,
            description="§9.7 operands are unchanged and fields are read-only",
        ),
        Case(
            target="Rational",
            call=False,
            check=_field_axioms_hold,
            description="§9.5 associativity, commutativity, distributivity, inverses",
        ),
        # §4.1 — mixed operands
        Case(
            target="Rational",
            call=False,
            check=_int_operands_are_accepted_on_both_sides,
            description="§4.1 int operands work on either side of every operator",
        ),
        Case(
            target="Rational",
            call=False,
            check=_float_and_bool_operands_are_rejected,
            description="§4.1 float and bool operands raise TypeError",
        ),
        # §10 — error paths reachable only through operators
        Case(
            target="Rational",
            call=False,
            check=_zero_denominators_raise_zero_division,
            description="§10 every zero-denominator route raises ZeroDivisionError",
        ),
        Case(
            target="Rational",
            call=False,
            check=_power_edge_cases,
            description="§4 negative, zero, and signed exponents",
        ),
        Case(
            target="Rational",
            call=False,
            check=_huge_magnitudes_stay_exact,
            description="§10 exact arithmetic on 400-digit integers",
        ),
        Case(
            target="Rational",
            call=False,
            check=_to_float_converts_and_overflows,
            description="§10 to_float propagates OverflowError out of range",
        ),
        Case(
            target="Rational",
            call=False,
            check=_text_representation,
            description="§6 str and repr formats",
        ),
        Case(
            target="Rational",
            call=False,
            check=_results_are_deterministic,
            description="§9.10 repeated evaluation is bit-identical",
        ),
        # §7 — approximation
        Case(
            target="Rational",
            call=False,
            check=_limit_denominator_is_optimal,
            description="§9.9 limit_denominator beats an exhaustive search on no denominator",
        ),
        Case(
            target="Rational",
            call=False,
            check=_limit_denominator_boundaries,
            description="§7 22/7, 311/99, the no-op case, and max_denominator < 1",
        ),
        # §2.1 — gcd is public API
        Case(target="gcd", args=(48, 18), expected=6, description="§2.1 gcd of two positives"),
        Case(target="gcd", args=(-48, 18), expected=6, description="§2.1 gcd is non-negative"),
        Case(target="gcd", args=(0, 5), expected=5, description="§2.1 gcd(0, n) == n"),
        Case(target="gcd", args=(0, 0), expected=0, description="§2.1 gcd(0, 0) == 0"),
        Case(
            target="gcd",
            args=(17, 5),
            expected=1,
            description="§2.1 coprime arguments give 1",
        ),
        Case(
            target="gcd",
            args=(2**64, 2**40),
            expected=2**40,
            description="§2.1 gcd on large powers of two",
        ),
        # §3 — parsing
        Case(
            target="parse_rational",
            call=False,
            check=_parse_accepts_the_grammar,
            description="§3 signs, whitespace, and leading zeros",
        ),
        Case(
            target="parse_rational",
            call=False,
            check=_parse_round_trips,
            description="§9.8 parse_rational(str(x)) == x",
        ),
        Case(
            target="parse_rational",
            args=("22/7",),
            check=_pair_is(22, 7),
            description="§3 a plain fraction literal",
        ),
        Case(
            target="parse_rational",
            args=("-100/-250",),
            check=_pair_is(2, 5),
            description="§3 parsed literals are normalised too",
        ),
        # §7 — approximate
        Case(
            target="approximate",
            args=(0.5,),
            kwargs={"max_denominator": 100},
            check=_pair_is(1, 2),
            description="§7 an exactly representable float",
        ),
        Case(
            target="approximate",
            args=(0.1,),
            kwargs={"max_denominator": 10**6},
            check=_pair_is(1, 10),
            description="§7 0.1 recovers 1/10 despite its exact binary value",
        ),
        Case(
            target="approximate",
            args=(3.141592653589793,),
            kwargs={"max_denominator": 10},
            check=_pair_is(22, 7),
            description="§7 pi to denominator 10 is 22/7",
        ),
        Case(
            target="approximate",
            args=(3.141592653589793,),
            kwargs={"max_denominator": 100},
            check=_pair_is(311, 99),
            description="§7 pi to denominator 100 is 311/99",
        ),
        Case(
            target="approximate",
            args=(-0.75,),
            kwargs={"max_denominator": 100},
            check=_pair_is(-3, 4),
            description="§7 negative floats keep the sign in the numerator",
        ),
        Case(
            target="approximate",
            args=(7,),
            kwargs={"max_denominator": 3},
            check=_pair_is(7, 1),
            description="§7 an int converts exactly",
        ),
        Case(
            target="approximate",
            args=(0.0,),
            kwargs={"max_denominator": 10},
            check=_pair_is(0, 1),
            description="§7 zero is canonical",
        ),
        # §7 — rational_sum
        Case(
            target="rational_sum",
            call=False,
            check=_rational_sum_is_exact_and_streaming,
            description="§7 exact harmonic sum from a one-shot generator",
        ),
    ],
    error_cases=[
        # §10 — zero denominators
        ErrorCase(
            target="Rational",
            args=(1, 0),
            exc_name="ZeroDivisionError",
            description="§10 Rational(1, 0) raises ZeroDivisionError",
        ),
        ErrorCase(
            target="Rational",
            args=(0, 0),
            exc_name="ZeroDivisionError",
            description="§10 Rational(0, 0) raises ZeroDivisionError",
        ),
        ErrorCase(
            target="parse_rational",
            args=("1/0",),
            exc_name="ZeroDivisionError",
            description="§10 a syntactically valid literal with denominator 0",
        ),
        # §10 — non-integer construction
        ErrorCase(
            target="Rational",
            args=(1.5,),
            exc_name="TypeError",
            description="§10 a float numerator raises TypeError",
        ),
        ErrorCase(
            target="Rational",
            args=(1, 2.0),
            exc_name="TypeError",
            description="§10 a float denominator raises TypeError",
        ),
        ErrorCase(
            target="Rational",
            args=(True, 2),
            exc_name="TypeError",
            description="§2 I4 bool is rejected despite subclassing int",
        ),
        ErrorCase(
            target="Rational",
            args=(1, "2"),
            exc_name="TypeError",
            description="§10 a str denominator raises TypeError",
        ),
        ErrorCase(
            target="Rational",
            args=("1", 2),
            exc_name="TypeError",
            description="§10 a str numerator raises TypeError",
        ),
        ErrorCase(
            target="gcd",
            args=(1.5, 2),
            exc_name="TypeError",
            description="§2.1 gcd rejects non-int arguments",
        ),
        ErrorCase(
            target="gcd",
            args=(4, None),
            exc_name="TypeError",
            description="§2.1 gcd rejects None",
        ),
        # §10 — malformed literals. The first three are accepted by int(),
        # so a split-and-int parser passes everything else and fails here.
        ErrorCase(
            target="parse_rational",
            args=("3 / 4",),
            exc_name="ValueError",
            description="§3 internal whitespace is malformed (int() would accept it)",
        ),
        ErrorCase(
            target="parse_rational",
            args=("1_0/2",),
            exc_name="ValueError",
            description="§3 digit separators are malformed (int() would accept them)",
        ),
        ErrorCase(
            target="parse_rational",
            args=("٣/٤",),
            exc_name="ValueError",
            description="§3 non-ASCII digits are malformed (int() would accept them)",
        ),
        ErrorCase(
            target="parse_rational",
            args=("",),
            exc_name="ValueError",
            description="§10 the empty string is malformed",
        ),
        ErrorCase(
            target="parse_rational",
            args=("   ",),
            exc_name="ValueError",
            description="§10 whitespace alone is malformed",
        ),
        ErrorCase(
            target="parse_rational",
            args=("abc",),
            exc_name="ValueError",
            description="§10 non-numeric text is malformed",
        ),
        ErrorCase(
            target="parse_rational",
            args=("1.5",),
            exc_name="ValueError",
            description="§10 decimals are not part of the grammar",
        ),
        ErrorCase(
            target="parse_rational",
            args=("1/2/3",),
            exc_name="ValueError",
            description="§10 two slashes are malformed",
        ),
        ErrorCase(
            target="parse_rational",
            args=("1/",),
            exc_name="ValueError",
            description="§10 a missing denominator is malformed",
        ),
        ErrorCase(
            target="parse_rational",
            args=("/2",),
            exc_name="ValueError",
            description="§10 a missing numerator is malformed",
        ),
        ErrorCase(
            target="parse_rational",
            args=("--1/2",),
            exc_name="ValueError",
            description="§10 a doubled sign is malformed",
        ),
        ErrorCase(
            target="parse_rational",
            args=(None,),
            exc_name="TypeError",
            description="§10 a non-str argument raises TypeError",
        ),
        # §10 — approximate
        ErrorCase(
            target="approximate",
            args=(float("nan"),),
            kwargs={"max_denominator": 10},
            exc_name="ValueError",
            description="§10 nan is rejected",
        ),
        ErrorCase(
            target="approximate",
            args=(float("inf"),),
            kwargs={"max_denominator": 10},
            exc_name="ValueError",
            description="§10 inf is rejected",
        ),
        ErrorCase(
            target="approximate",
            args=(float("-inf"),),
            kwargs={"max_denominator": 10},
            exc_name="ValueError",
            description="§10 -inf is rejected",
        ),
        ErrorCase(
            target="approximate",
            args=(0.5,),
            kwargs={"max_denominator": 0},
            exc_name="ValueError",
            description="§10 max_denominator 0 is rejected",
        ),
        ErrorCase(
            target="approximate",
            args=(0.5,),
            kwargs={"max_denominator": -3},
            exc_name="ValueError",
            description="§10 a negative max_denominator is rejected",
        ),
        # §10 — rational_sum
        ErrorCase(
            target="rational_sum",
            args=([0.5],),
            exc_name="TypeError",
            description="§10 a float element is rejected",
        ),
        ErrorCase(
            target="rational_sum",
            args=(["1/2"],),
            exc_name="TypeError",
            description="§10 a str element is rejected",
        ),
    ],
    prohibitions=[
        Prohibition(
            reason=(
                "§12 forbids fractions and decimal — Fraction already implements "
                "this entire specification, so wrapping it would satisfy every "
                "functional test while implementing no normalisation at all"
            ),
            imports=("fractions", "decimal"),
            name_calls=("Fraction", "Decimal"),
        ),
        Prohibition(
            reason=(
                "§12 forbids math — gcd is public API (§2.1) and must be the "
                "explicit Euclid loop, and non-finite floats are detectable "
                "without it"
            ),
            imports=("math",),
        ),
        Prohibition(
            reason=(
                "§12 forbids float.as_integer_ratio and eval-based parsing — §7 "
                "specifies the exact binary expansion and §3 the grammar"
            ),
            name_calls=("eval", "literal_eval"),
            attr_calls=("as_integer_ratio", "literal_eval"),
        ),
    ],
)
