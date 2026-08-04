# Semantic Version Parsing, Precedence Ordering, and Range Matching

Python Library Specification

## Abstract

This document specifies a Python library implementing Semantic Versioning 2.0.0:
a strict parser, a total precedence order over versions, and a comparator-based
range language for dependency constraints. The pre-release rules of §2 are its
substance, and each is a place where the natural Python expression gives the wrong
answer: a pre-release is *lower* than the bare core, yet a non-empty tuple sorts
above an empty one; numeric identifiers compare as integers, yet `"11" < "2"` as
strings; build metadata is no part of precedence, yet a field-by-field `__eq__`
compares it. §3.2 adds a fourth of the same character — without it `>=1.0.0`
silently admits `2.0.0-alpha`.

## 1. Grammar

Matched in full: no surrounding whitespace, no `v` prefix, no partial forms.

```
version     ::= core [ "-" pre-release ] [ "+" build ]
core        ::= numeric "." numeric "." numeric
numeric     ::= "0" | [1-9] [0-9]*
pre-release ::= pre-id ( "." pre-id )*
pre-id      ::= numeric | alnum-id
build       ::= build-id ( "." build-id )*
build-id    ::= [0-9A-Za-z-]+
alnum-id    ::= [0-9A-Za-z-]+ with at least one character that is not a digit
```

Leading zeroes are forbidden in the core and in numeric pre-release identifiers
(`01.2.3`, `1.0.0-01`) but **permitted** in build identifiers (`1.0.0+001`),
which are never interpreted as numbers. No identifier may be empty (`1.0.0-`,
`1.0.0+`, `1.0.0-alpha..1`). A hyphen inside an identifier is ordinary, so
`1.0.0-alpha-1` has the single pre-release identifier `alpha-1`. Scanning order,
which resolves every ambiguity above: split the build off at the **first** `+`,
then the pre-release at the **first** `-` of what remains; the rest is the core.

### 1.1 Retention

Identifiers are retained verbatim as `tuple[str, ...]`; numeric interpretation
happens at comparison time, never at parse time. So
`parse("1.0.0-alpha.1").prerelease == ("alpha", "1")` — the string, not the
integer — and `str(parse(s)) == s` for every valid `s`.

## 2. Precedence

`compare(a, b)` returns exactly `-1`, `0`, or `+1`.

1. **Core** — compare `major`, then `minor`, then `patch` as integers, not
   strings: `2.0.0 < 10.0.0`.
2. **Pre-release presence** — with equal cores, the side that has a pre-release
   is lower; if neither has one the versions are equal.
3. **Pre-release identifiers** — with both sides carrying pre-releases:

```
compare_prerelease(a, b):
    for (x, y) in zip(a, b):
        if x and y are both numeric:  compare int(x) with int(y)
        elif x is numeric:            x is lower      # numeric < alphanumeric
        elif y is numeric:            y is lower
        else:                         compare x with y by ASCII code point
        if that comparison was decisive: return it
    return sign(len(a) - len(b))      # the longer identifier list is higher
```

   Numeric identifiers are arbitrary-precision, so
   `1.0.0-9 < 1.0.0-99999999999999999999`. Code-point order is case sensitive, so
   `1.0.0-Alpha < 1.0.0-alpha`; case folding is wrong.
4. **Build metadata** — ignored entirely. `Version` equality and `__hash__` follow
   precedence, so `parse("1.0.0+a") == parse("1.0.0+b")` is True and the two hash
   equally, while `str` still reproduces each verbatim.

### 2.1 Reference chain

Strictly ascending; each neighbouring pair exercises a different rule above.

```
0.0.4 < 0.1.0 < 1.0.0-0 < 1.0.0-Alpha < 1.0.0-alpha < 1.0.0-alpha.1
      < 1.0.0-alpha.beta < 1.0.0-beta < 1.0.0-beta.2 < 1.0.0-beta.11
      < 1.0.0-rc.1 < 1.0.0 < 1.0.1 < 1.1.0 < 2.0.0 < 10.0.0
```

## 3. Ranges

```
range          ::= comparator-set ( "||" comparator-set )*
comparator-set ::= comparator ( whitespace comparator )*
comparator     ::= operator? version | "*"
operator       ::= ">=" | "<=" | ">" | "<" | "=" | "^" | "~"
```

Comparators within a set are conjunctive, sets are disjunctive, a missing operator
means `=`. A comparator's version part must be a **full** version per §1; partial
forms such as `^1.2` are rejected. A range with no comparators is invalid.

### 3.1 Desugaring

| Comparator | Expands to | Note |
|---|---|---|
| `^1.2.3` | `>=1.2.3 <2.0.0` | caret keeps the left-most non-zero element |
| `^0.2.3` | `>=0.2.3 <0.3.0` | |
| `^0.0.3` | `>=0.0.3 <0.0.4` | |
| `~1.2.3` | `>=1.2.3 <1.3.0` | tilde always keeps major and minor |
| `~0.0.3` | `>=0.0.3 <0.1.0` | differs from `^0.0.3` |
| `*` | `>=0.0.0` | |

Caret and tilde retain any pre-release on their lower bound: `^1.2.3-beta.2`
expands to `>=1.2.3-beta.2 <2.0.0`.

### 3.2 Pre-release visibility

A candidate carrying a pre-release satisfies a comparator set only if **both**
hold: (a) every comparator in the set is satisfied under §2, and (b) some
comparator in that set names a version that itself carries a pre-release **and**
has the same `(major, minor, patch)` triple as the candidate. A candidate with no
pre-release is subject to (a) only. Consequences: `2.0.0-alpha` does not satisfy
`>=1.0.0`; `1.0.0-alpha` does not satisfy `<1.0.0`; `1.0.0-rc.1` does not satisfy
`*`; `1.2.3-beta.4` does satisfy `^1.2.3-beta.2`; `1.2.4-alpha` does not, the only
pre-release comparator having core `1.2.3`; `1.2.3` satisfies it normally.

## 4. Complexity

| Operation | Time | Space |
|---|---|---|
| `parse` | O(L) in the string length | O(L) |
| `compare` | O(p) in the pre-release identifier count | O(1) |
| `satisfies` | O(k·p) for k comparators | O(k) |
| `sort_versions` | O(n log n) comparisons, stable | O(n) |
| `max_satisfying` | O(n·k) | O(1) beyond the parse |

## 5. Correctness Properties

1. **Round trip and retention** — `str(parse(s)) == s` for valid `s`, build
   metadata included; `prerelease` and `build` are tuples of `str` holding the
   identifiers exactly as written (§1.1).
2. **Total order** — `compare` returns only `-1`, `0`, `+1`, is antisymmetric
   (`compare(a,b) == -compare(b,a)`), and is transitive.
3. **Core dominance** — without pre-releases, precedence is exactly the tuple
   order of `(major, minor, patch)`.
4. **Chain** — the ordering of §2.1 holds exactly.
5. **Permutation invariance** — `sort_versions` yields one order for every
   permutation of an input with pairwise distinct precedence, and is stable for
   elements of equal precedence.
6. **Consistency** — the ordering dunders agree with `compare`, `__hash__` agrees
   with `__eq__`, and both ignore build metadata, which only `str` retains.
7. **Range soundness** — `satisfies` is exactly the conjunction of §3.1
   desugaring, §2 precedence, and the §3.2 pre-release rule.
8. **Maximality** — `max_satisfying` returns the highest-precedence satisfying
   element, or `None`; among equally precedent ones, the earliest in the input.

## 6. Failure Modes and Edge Cases

- `InvalidVersionError` for `"1.2"`, `"1.2.3.4"`, `"01.2.3"`, `"1.0.0-01"`,
  `"1.0.0-"`, `"1.0.0+"`, `"1.0.0-alpha..1"`, `"1.0.0-alpha_1"`, `"v1.2.3"`,
  `" 1.2.3"`, `"1.2.-3"`, and `""`. Every entry point that accepts a version
  string validates it identically, so `compare("1.0", "1.0.0")` raises too.
- `TypeError` for a non-`str` argument to `parse` and for a non-`int` core
  component of `Version`; `InvalidVersionError` for a negative one.
- `InvalidRangeError` — not `InvalidVersionError` — for a malformed range: an
  unparseable comparator version (`"^1.2"`, `"??"`), a bare operator (`">="`), or
  an empty comparator set (`""`, `">1.0.0 || "`), so callers can tell a bad
  constraint from a bad version. `satisfies` with a malformed *version* still
  raises `InvalidVersionError`.
- `max_satisfying` over an empty iterable returns `None` but still validates the
  range. Numeric identifiers may exceed 64 bits and must not overflow. Both error
  classes subclass `ValueError`.

## 7. Public API

```python
class InvalidVersionError(ValueError): ...
class InvalidRangeError(ValueError): ...

class Version:
    """Immutable, hashable. major/minor/patch: int. prerelease/build: tuple[str, ...]."""
    def __init__(self, major: int, minor: int, patch: int,
                 prerelease: Iterable[str] = (), build: Iterable[str] = ()) -> None: ...
    def __str__(self) -> str: ...                     # verbatim round trip (§1.1)
    def __eq__(self, other: object) -> bool: ...      # build-insensitive (§2.4)
    def __hash__(self) -> int: ...
    def __lt__(self, other: "Version") -> bool: ...   # also __le__, __gt__, __ge__
    @property
    def is_prerelease(self) -> bool: ...

def parse(text: str) -> Version: ...
def compare(a: str | Version, b: str | Version) -> int: ...
def sort_versions(versions: Iterable[str | Version]) -> list[Version]: ...
def satisfies(version: str | Version, range_expr: str) -> bool: ...
def max_satisfying(versions: Iterable[str | Version], range_expr: str) -> Version | None: ...
```

## 8. Implementation Notes

- Do not import `packaging`, `distutils`, `pkg_resources`, or any third-party
  version library. Their rules are PEP 440's, not SemVer's, and they disagree on
  exactly the pre-release cases this library exists to get right — delegating
  would pass a casual review and fail §2.
- Do not import `re`. The grammar of §1 must be an explicit character scanner: a
  regular expression collapses the validation rules into one opaque literal that
  cannot be reviewed clause by clause, and the leading-zero rules are precisely
  where such literals go wrong.
- Parse once per operation: `satisfies` and `max_satisfying` must not re-parse the
  range per candidate. Validate before constructing — a `Version` must never exist
  in an invalid state.
