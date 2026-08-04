# Numerically Stable Online Statistics via Welford's Algorithm

Python Library Specification

## Abstract

This document specifies a Python library computing running statistics — count,
mean, variance, standard deviation, skewness, and kurtosis — in a single pass
over a stream of real numbers, without retaining the samples. The library uses
Welford's online algorithm and its higher-moment extensions, which are
numerically stable where the textbook "sum of squares minus square of sum"
formulation catastrophically loses precision. Accumulators are mergeable, so
statistics computed over partitions can be combined exactly.

## 1. Overview and Design Rationale

The naive variance formula computes `E[x²] - E[x]²`. When the mean is large
relative to the spread, these two terms are nearly equal and their difference
loses most of its significant digits — for the sample `[1e9, 1e9+1, 1e9+2]` the
naive formula can return a negative variance. Welford's recurrence instead
updates the mean and the sum of squared deviations incrementally, so the
subtraction never happens.

The library holds only O(1) state regardless of stream length, which is the point:
it is intended for streams too large to materialise.

## 2. State

An accumulator holds:

| Field | Meaning |
|---|---|
| `n` | number of samples seen |
| `mean` | running arithmetic mean |
| `M2` | sum of squared deviations from the current mean |
| `M3` | sum of cubed deviations (for skewness) |
| `M4` | sum of fourth-power deviations (for kurtosis) |
| `min_value`, `max_value` | running extremes |

All are initialised to zero, except the extremes which are `None` until the first
sample.

## 3. Update Recurrence

On observing sample x with `n` samples already seen:

```
n1    = n
n     = n + 1
delta = x - mean
delta_n  = delta / n
delta_n2 = delta_n * delta_n
term  = delta * delta_n * n1

mean += delta_n
M4   += term * delta_n2 * (n*n - 3*n + 3) + 6 * delta_n2 * M2 - 4 * delta_n * M3
M3   += term * delta_n * (n - 2) - 3 * delta_n * M2
M2   += term
```

The order of these five assignments is significant: `M4` reads the old `M2` and
`M3`, and `M3` reads the old `M2`. Updating `M2` first silently corrupts the
higher moments, and this is a required test.

## 4. Derived Statistics

| Statistic | Definition | Defined when |
|---|---|---|
| `mean` | running mean | n ≥ 1 |
| `variance` (sample) | `M2 / (n - 1)` | n ≥ 2 |
| `variance_population` | `M2 / n` | n ≥ 1 |
| `stddev` | `sqrt(variance)` | n ≥ 2 |
| `skewness` | `sqrt(n) * M3 / M2^1.5` | n ≥ 3 and M2 > 0 |
| `kurtosis` (excess) | `n * M4 / (M2 * M2) - 3` | n ≥ 4 and M2 > 0 |

Requesting a statistic with too few samples raises `InsufficientDataError`
naming the statistic and the required minimum. Returning `None` or `nan` in this
case is explicitly not acceptable — a silent `nan` propagates into downstream
computation undetected.

When all samples are identical, `M2` is exactly 0; `variance` and `stddev` are
0.0, while `skewness` and `kurtosis` raise `UndefinedStatisticError` because the
ratios are 0/0.

## 5. Merging

Two accumulators over disjoint samples combine into one:

```
delta = b.mean - a.mean
n     = a.n + b.n

mean = a.mean + delta * b.n / n
M2   = a.M2 + b.M2 + delta² * a.n * b.n / n
M3   = a.M3 + b.M3 + delta³ * a.n * b.n * (a.n - b.n) / n²
     + 3 * delta * (a.n * b.M2 - b.n * a.M2) / n
M4   = a.M4 + b.M4 + delta⁴ * a.n * b.n * (a.n² - a.n*b.n + b.n²) / n³
     + 6 * delta² * (a.n² * b.M2 + b.n² * a.M2) / n²
     + 4 * delta * (a.n * b.M3 - b.n * a.M3) / n
```

Merging with an empty accumulator returns the other unchanged. Merge must be
associative to within floating-point tolerance: `(a+b)+c ≈ a+(b+c)`.

## 6. Numerical Accuracy Requirements

These are the acceptance criteria that distinguish this library from a naive
implementation:

1. For `[1e9, 1e9 + 1, 1e9 + 2]`, sample variance must be 1.0 to within 1e-6
   relative error, and must never be negative.
2. For 10^6 samples drawn from a distribution with known variance, the computed
   variance must agree with `statistics.variance` to within 1e-9 relative error.
3. Variance must never be negative for any input. If floating-point drift makes
   `M2` slightly negative, it is clamped to 0.0.
4. For a constant stream of 10^6 identical values, variance must be exactly 0.0,
   not a small positive residue.
5. Merging a stream split into k partitions must agree with the unsplit
   computation to within 1e-9 relative error, for k from 2 to 100.

## 7. Input Validation

- `NaN` input raises `ValueError` — silently absorbing NaN would poison every
  subsequent statistic irrecoverably.
- `+inf` / `-inf` raise `ValueError` for the same reason.
- Non-numeric input raises `TypeError`.
- Booleans are rejected as `TypeError` despite being `int` subclasses, since
  accumulating booleans is almost always a caller bug.

Validation happens before any state mutation, so a rejected sample leaves the
accumulator exactly as it was. This is a required test: feed a bad value, catch
the error, and assert every field is unchanged.

## 8. Complexity

Update is O(1) time and O(1) space. A stream of n samples costs O(n) time and
O(1) space total. Merge is O(1). No operation retains samples.

## 9. Correctness Properties

1. **Single-pass** — the accumulator never stores individual samples; memory is
   constant in n.
2. **Agreement** — results match `statistics` module equivalents within the
   tolerances of §6.
3. **Merge associativity** — as stated in §5.
4. **Order independence** — mean and variance are invariant (within 1e-9) to the
   order in which samples arrive.
5. **Atomicity** — a rejected sample does not mutate state (§7).
6. **Non-negativity** — variance and stddev are always ≥ 0.

## 10. Failure Modes and Edge Cases

- Zero samples: `mean` raises `InsufficientDataError`.
- One sample: `mean` works; sample `variance` raises; population variance is 0.0.
- Two samples: variance works; skewness raises.
- All-identical samples: variance 0.0; skewness/kurtosis raise
  `UndefinedStatisticError`.
- Very large magnitudes (1e300): must not overflow to inf during the `M4` update.
- Very small magnitudes (1e-300): must not underflow to a false zero variance.
- Mixed positive and negative values spanning many orders of magnitude.

## 11. Public API

```python
class RunningStats:
    def __init__(self) -> None: ...

    def update(self, x: float) -> None:
        """Add one sample. Raises ValueError/TypeError on invalid input."""

    def update_many(self, values: Iterable[float]) -> None:
        """Add many samples. On an invalid value, samples before it are retained."""

    def merge(self, other: "RunningStats") -> "RunningStats":
        """Return a new accumulator combining both. Neither input is mutated."""

    @property
    def n(self) -> int: ...
    @property
    def mean(self) -> float: ...
    @property
    def variance(self) -> float: ...
    @property
    def variance_population(self) -> float: ...
    @property
    def stddev(self) -> float: ...
    @property
    def skewness(self) -> float: ...
    @property
    def kurtosis(self) -> float: ...
    @property
    def minimum(self) -> float: ...
    @property
    def maximum(self) -> float: ...

class InsufficientDataError(ValueError): ...
class UndefinedStatisticError(ValueError): ...
```

## 12. Implementation Notes

- Do not delegate to `statistics`, `numpy`, or `pandas`; the recurrences are the
  subject of the specification. The `statistics` module may be used in tests as
  an oracle.
- `update_many` must not build an intermediate list from the iterable.
- Guard the `M2 > 0` checks with an exact zero comparison, not a tolerance, so
  that a genuinely constant stream is distinguishable from a nearly-constant one.
