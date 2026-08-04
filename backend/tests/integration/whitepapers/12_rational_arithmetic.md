# Exact Rational Arithmetic with Automatic Normalisation

Python Library Specification

## Abstract

This document specifies a Python library implementing exact arithmetic over the
rational numbers. A rational is an immutable pair of arbitrary-precision integers
held in canonical form: lowest terms, with a strictly positive denominator. That
form is established at construction and re-established after every operation, so
no code path can observe an unnormalised value. Arithmetic, comparison, equality,
and hashing are exact: none of them may route through binary floating point. Only
the two explicitly float-facing entry points, `to_float` and `approximate`, touch
a `float` at all.

## 1. Overview and Design Rationale

Rationals are the smallest interesting example of a type whose correctness is an
*invariant* rather than a *result*. `Rational(2, 4)` and `Rational(1, 2)` denote
the same number, and every derived behaviour — equality, hashing, printing, set
membership, dictionary lookup — is wrong unless the two are indistinguishable
once constructed. Each individual arithmetic formula is easy; keeping one
invariant true across a dozen entry points is the difficulty.

The design therefore funnels every construction through a single normalising
helper. Operations compute a raw `(numerator, denominator)` pair using integer
arithmetic only and hand that pair to the constructor; they never assign fields
directly and never assume their operands need re-checking.

## 2. Canonical Form

A `Rational` stores exactly two `int` fields, `n` and `d`, subject to four
invariants that hold for every instance a caller can reach:

- **I1 — Positive denominator.** `d > 0`. Zero is impossible; negative is
  normalised away.
- **I2 — Lowest terms.** `gcd(|n|, d) == 1`.
- **I3 — Canonical zero.** `n == 0` implies `d == 1`. A corollary of I2, since
  `gcd(0, d) == d`, called out because it is the case most often missed.
- **I4 — Integrality.** `n` and `d` are `int`. `bool` is not accepted anywhere an
  `int` is required, despite being a subclass of `int`.

Two rationals therefore denote the same number if and only if their `(n, d)`
pairs are identical. Everything in §5 and §6 rests on that.

### 2.1 Normalisation

```
gcd(a, b):                      # iterative Euclid; gcd(0, 0) == 0
    a, b = abs(a), abs(b)
    while b: a, b = b, a % b
    return a

normalise(n, d):
    if d == 0: raise ZeroDivisionError
    if d < 0:  n, d = -n, -d    # the sign lives in the numerator, and only there
    g = gcd(n, d)               # g >= 1 because d != 0
    return n // g, d // g
```

`gcd` is public API and must be the explicit loop above.

## 3. Construction and Parsing

`Rational(numerator, denominator=1)` normalises per §2.1; both arguments must be
`int` (I4). `Rational(5)` is `5/1`, `Rational(3, -4)` is `-3/4`, `Rational(-3, -4)`
is `3/4`, `Rational(0, -5)` is `0/1`.

`parse_rational(text)` accepts exactly this grammar, after leading and trailing
whitespace is stripped:

```
rational := sign? digits ( "/" sign? digits )?
sign     := "+" | "-"
digits   := one or more ASCII characters "0".."9"
```

Nothing else is accepted. In particular `"3 / 4"` (internal whitespace) and
`"1_0/2"` (digit separator) are malformed even though Python's `int()` accepts
both, as is `"٣/٤"` (non-ASCII digits, which `int()` also accepts). Splitting on
`"/"` and calling `int()` on each side therefore does **not** implement this
section. Leading zeros are permitted: `"007/002"` is `7/2`.

## 4. Arithmetic

For `x = a/b` and `y = c/d`, with `b, d > 0`, each row below gives the *raw* pair
an operator computes. Every one of them is handed to the constructor, which
applies §2.1; no operator normalises for itself (§12):

| Operation | Raw result |
|---|---|
| `x + y` | `(a·d + c·b) / (b·d)` |
| `x - y` | `(a·d - c·b) / (b·d)` |
| `x * y` | `(a·c) / (b·d)` |
| `x / y` | `(a·d) / (b·c)`, `ZeroDivisionError` if `c == 0` |
| `-x`, `abs(x)` | `(-a) / b`, `|a| / b` |
| `x ** k`, `k >= 0` | `a^k / b^k` |
| `x ** k`, `k < 0` | `b^|k| / a^|k|`, `ZeroDivisionError` if `a == 0` |

`x.reciprocal()` is `b/a`, raising `ZeroDivisionError` when `a == 0`. `bool(x)` is
`a != 0`. Multiplication may instead cancel crosswise before multiplying — divide
`a`, `d` by `gcd(a, d)` and `c`, `b` by `gcd(c, b)` — which reaches the same
normalised value from smaller intermediates. That is a preference about cost, not
a change of meaning.

### 4.1 Mixed Operands

Every binary operator accepts a `Rational` or an `int` on either side, treating
`int k` as `k/1`; the reflected operators supply the left-hand case. Any other
operand type — notably `float`, and notably `bool` (I4) — yields `NotImplemented`,
so Python raises `TypeError`. Silently mixing exact and inexact numbers is
precisely the defect this library exists to avoid.

## 5. Comparison and Equality

With `b, d > 0`, `x < y` if and only if `a·d < c·b`; all six comparisons are
computed this way. **No comparison may convert an operand to `float`**: for
`x = 10^18/3` and `y = (10^18 + 1)/3` the two `float` images are bit-identical,
yet `x < y`.

`x == y` is `(a, b) == (c, d)`, which by §2 is exactly numeric equality; `x == k`
for `int k` is `b == 1 and a == k`. Against any other type `__eq__` returns
`NotImplemented`, so `Rational(1, 2) == 0.5` is `False` while `Rational(1, 2) < 0.5`
raises `TypeError`.

## 6. Hashing, Immutability, Representation

```
hash(x) := hash(n)  if d == 1,  else  hash((n, d))
```

This makes `hash` agree with `__eq__` in both directions that matter: equal
rationals hash equally because their canonical pairs are identical, and an
integral rational hashes as the equal `int`, so `{Rational(4, 2), 2}` has one
element. Compatibility with `hash(float)` is explicitly *not* required.

Instances are immutable: `numerator` and `denominator` are read-only properties,
assignment to either raises `AttributeError`, and no operation mutates an operand.

`str(x)` is `"n/d"`, or `"n"` when `d == 1`. `repr(x)` is `"Rational(n, d)"`,
always with both fields. `parse_rational(str(x)) == x` for every `x`.

## 7. Approximation

`x.limit_denominator(m)` returns the closest rational to `x` with denominator at
most `m`, via the continued-fraction (Stern–Brocot) expansion:

```
if m < 1:   raise ValueError
if x.d <= m: return x
p0, q0, p1, q1 = 0, 1, 1, 0
n, dd = x.n, x.d
loop:
    a = n // dd
    q2 = q0 + a*q1
    if q2 > m: break
    p0, q0, p1, q1 = p1, q1, p0 + a*p1, q2
    n, dd = dd, n - a*dd
k = (m - q0) // q1
candidates = [ (p0 + k*p1)/(q0 + k*q1),  p1/q1 ]
return the candidate nearer x; on a tie, the one with the smaller denominator
```

A linear scan over denominators `1..m` yields the same answer, and is the ground
truth this section is defined against, but it is O(m) and so does not meet the
bound in §8.

`approximate(value, *, max_denominator)` converts an `int` or `float` to a
`Rational`. A `float` is first expanded *exactly* — doubling numerator and
denominator until the numerator is integral, terminating within ~1080 steps — and
that exact value is passed through `limit_denominator`. So
`approximate(0.1, max_denominator=10**6)` is `1/10`, even though the exact value
of the `float` `0.1` is `3602879701896397/36028797018963968`.

`x.to_float()` returns `n / d`. `rational_sum(values)` sums an iterable of
`Rational` or `int` exactly, returns `0/1` for an empty iterable, and must consume
the iterable lazily rather than materialising it.

## 8. Complexity

Let `k` bound the bit length of the integers involved.

| Operation | Time | Space |
|---|---|---|
| `gcd` | O(k) division steps, O(k²) bit operations | O(1) |
| `+ - * /` | O(k²), dominated by the `gcd` in `normalise` | O(k) |
| comparison, equality | O(k²) for the two cross products | O(k) |
| `hash`, `str` | O(k) | O(k) |
| `limit_denominator(m)` | O(log m) iterations | O(1) |
| `rational_sum` over n values | O(n) operations | O(1) beyond the accumulator |

All algorithms are iterative; nothing here recurses, so no input can raise
`RecursionError`.

## 9. Correctness Properties

1. **Canonical form** — I1–I4 hold for every instance returned by any
   constructor, operator, method, or module-level function.
2. **Sign placement** — the sign is carried by the numerator alone; the
   denominator is never negative and never zero.
3. **Exactness** — arithmetic loses no information: `1/3 + 1/3 + 1/3 == 1`, and
   `(x / y) * y == x` for every `x` and every non-zero `y`.
4. **Equality–hash agreement** — `x == y` implies `hash(x) == hash(y)`, and
   equivalent literal forms (`2/4`, `1/2`, `-1/-2`) collapse to one element in a
   `set` and one key in a `dict`.
5. **Field axioms** — `+` and `*` are associative and commutative, `*`
   distributes over `+`, `0/1` and `1/1` are the identities, and every non-zero
   value has an exact multiplicative inverse.
6. **Order consistency** — `<` is a strict total order agreeing with exact
   arithmetic: `x < y` if and only if `(y - x).numerator > 0`, even where
   `float(x) == float(y)`.
7. **Immutability** — no operation mutates an operand; every operation returns a
   new instance, and attribute assignment raises `AttributeError`.
8. **Round trip** — `parse_rational(str(x)) == x` and
   `Rational(x.numerator, x.denominator) == x` for every `x`.
9. **Approximation optimality** — `limit_denominator(m)` returns a value whose
   denominator is at most `m` and whose distance to `x` is minimal among all such
   values; if `x`'s denominator is already at most `m`, `x` is returned unchanged.
10. **Determinism** — identical inputs produce identical `(numerator,
    denominator)` pairs, `str`, and `hash` on every run and in every order.

## 10. Failure Modes and Edge Cases

- **Zero denominator** raises `ZeroDivisionError`, never `ValueError`: from
  `Rational(1, 0)`, from `parse_rational("1/0")` (syntactically valid, so the zero
  denominator dominates), from division by `0/1`, from `reciprocal()` on zero, and
  from `Rational(0, 1) ** -1`.
- **Non-integer construction** raises `TypeError`: `Rational(1.5)`,
  `Rational(1, "2")`, `Rational(True, 2)`.
- **Malformed literals** raise `ValueError`: `""`, `"   "`, `"abc"`, `"1.5"`,
  `"3 / 4"`, `"1_0/2"`, `"1/2/3"`, `"1/"`, `"/2"`, `"--1/2"`, `"٣/٤"`. A non-`str`
  argument raises `TypeError`.
- **Float contamination** raises `TypeError` at the operator boundary:
  `Rational(1, 2) + 0.5`, `Rational(1, 2) < 0.5`. Note the asymmetry Python's data
  model requires: `Rational(1, 2) == 0.5` is `False`, not an error.
- `Rational(0, 1) ** 0` is `1/1`; `Rational(0, 1) ** 3` is `0/1`.
- **Unbounded magnitudes** — Python integers do not overflow, so exact arithmetic
  on values such as `10^400/3` must succeed. Only `to_float()` can fail on such a
  value, and it fails by propagating `OverflowError`.
- `approximate` raises `ValueError` for `nan`, `inf`, `-inf`, and for
  `max_denominator < 1`; `limit_denominator(0)` likewise raises `ValueError`.
- `rational_sum([])` is `0/1`; an element that is neither `Rational` nor `int`
  raises `TypeError`.

## 11. Public API

```python
class Rational:
    def __init__(self, numerator: int, denominator: int = 1) -> None: ...

    @property
    def numerator(self) -> int: ...
    @property
    def denominator(self) -> int: ...

    def reciprocal(self) -> "Rational": ...
    def limit_denominator(self, max_denominator: int) -> "Rational": ...
    def to_float(self) -> float: ...

    # __add__ __radd__ __sub__ __rsub__ __mul__ __rmul__ __truediv__ __rtruediv__
    #     (Rational | int) -> Rational; NotImplemented for any other operand type
    # __pow__(exponent: int) -> Rational
    # __neg__ __pos__ __abs__ -> Rational;   __bool__ -> bool
    # __eq__ __lt__ __le__ __gt__ __ge__ (Rational | int) -> bool
    # __hash__ -> int;   __str__ __repr__ -> str


def gcd(a: int, b: int) -> int:
    """Greatest common divisor by iterative Euclid. Non-negative; gcd(0, 0) == 0."""

def parse_rational(text: str) -> Rational:
    """Parse the §3 grammar. ValueError if malformed, ZeroDivisionError if d == 0."""

def approximate(value: float | int, *, max_denominator: int) -> Rational:
    """Nearest rational to `value` with denominator <= max_denominator (§7)."""

def rational_sum(values: Iterable[Rational | int]) -> Rational:
    """Exact sum; 0/1 when empty. Consumes `values` lazily."""
```

## 12. Implementation Notes

- Do not import `fractions` or `decimal`, and do not call `Fraction` or
  `Decimal`. `fractions.Fraction` already implements this entire specification;
  wrapping it would satisfy every functional test while implementing nothing. The
  normalisation machinery is the deliverable.
- Do not import `math`. `gcd` is public API (§2.1) and must be the explicit Euclid
  loop, and non-finite floats are detectable without it: `v != v` is `True` only
  for `nan`, and the infinities compare equal to `float("inf")` / `float("-inf")`.
- Do not call `float.as_integer_ratio()`; §7 specifies the exact expansion.
- Do not use `eval` or `ast.literal_eval` for parsing; §3 defines the grammar.
- Normalise in exactly one private helper, called from exactly one place: the
  constructor. Operators compute a raw `(n, d)` pair and pass it in. An operator
  that normalises for itself is a second copy of the invariant, and the two copies
  will drift.
- `gcd` must be iterative, so adversarially large inputs cannot raise
  `RecursionError`.
