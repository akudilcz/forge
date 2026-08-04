"""Oracle for whitepapers/05_online_statistics.md.

Authored from the whitepaper only; never shown to any agent.

This is the whitepaper where a plausible implementation and a correct one are
hardest to tell apart. The textbook `E[x²] - E[x]²` formula produces the right
answer on every casual test and catastrophically wrong answers on the specific
inputs §6 names. So the oracle's centre of gravity is the numerical-accuracy
criteria, not the API surface — a naive implementation passes everything else.
"""

from __future__ import annotations

import statistics
from typing import Any

from backend.tests.integration.oracles._base import Case, Oracle, Prohibition


def _feed(cls: Any, values: list[float]) -> Any:
    acc = cls()
    for v in values:
        acc.update(v)
    return acc


def _large_offset_variance_is_exact(cls: Any) -> bool:
    """§6.1 — the canonical catastrophic-cancellation input.

    For [1e9, 1e9+1, 1e9+2] the naive formula subtracts two nearly-equal
    quantities and can return a negative variance. Welford's recurrence never
    performs that subtraction.
    """
    acc = _feed(cls, [1e9, 1e9 + 1, 1e9 + 2])
    var = acc.variance
    return bool(var >= 0.0 and abs(var - 1.0) < 1e-6)


def _never_negative_variance(cls: Any) -> bool:
    """§6.3 / §9.6 — variance is non-negative for every input."""
    for values in (
        [1e9 + i for i in range(50)],
        [1e15, 1e15 + 1],
        [0.1] * 100,
        [-1e9, -1e9 - 1, -1e9 - 2],
    ):
        acc = _feed(cls, values)
        if acc.variance < 0.0 or acc.variance_population < 0.0:
            return False
    return True


def _constant_stream_has_exactly_zero_variance(cls: Any) -> bool:
    """§6.4 — not merely small: exactly 0.0.

    A residue here means the recurrence is accumulating float noise, which is
    what §6.4 exists to forbid.
    """
    acc = _feed(cls, [3.7] * 10_000)
    return bool(acc.variance == 0.0 and acc.stddev == 0.0)


def _agrees_with_statistics_module(cls: Any) -> bool:
    """§6.2 / §9.2 — matches the stdlib to 1e-9 relative error."""
    import random

    rng = random.Random(20260805)
    for _ in range(20):
        values = [rng.gauss(1000.0, 25.0) for _ in range(500)]
        acc = _feed(cls, values)
        want_mean = statistics.fmean(values)
        want_var = statistics.variance(values)
        if abs(acc.mean - want_mean) > abs(want_mean) * 1e-9 + 1e-12:
            return False
        if abs(acc.variance - want_var) > abs(want_var) * 1e-9 + 1e-12:
            return False
    return True


def _merge_matches_unsplit(cls: Any) -> bool:
    """§6.5 — partitioned computation equals the unsplit one, for k=2..100."""
    import random

    rng = random.Random(20260806)
    values = [rng.gauss(50.0, 7.0) for _ in range(1000)]
    whole = _feed(cls, values)

    for k in (2, 3, 7, 50, 100):
        size = len(values) // k
        parts = [values[i : i + size] for i in range(0, len(values), size)]
        accs = [_feed(cls, p) for p in parts if p]
        merged = accs[0]
        for other in accs[1:]:
            merged = merged.merge(other)
        if abs(merged.mean - whole.mean) > abs(whole.mean) * 1e-9 + 1e-12:
            return False
        if abs(merged.variance - whole.variance) > abs(whole.variance) * 1e-9 + 1e-12:
            return False
    return True


def _merge_does_not_mutate_inputs(cls: Any) -> bool:
    """§11 — merge returns a new accumulator; neither input changes."""
    a = _feed(cls, [1.0, 2.0, 3.0])
    b = _feed(cls, [10.0, 20.0])
    a_n, b_n, a_mean = a.n, b.n, a.mean
    a.merge(b)
    return bool(a.n == a_n and b.n == b_n and a.mean == a_mean)


def _order_independence(cls: Any) -> bool:
    """§9.4 — mean and variance do not depend on arrival order."""
    import random

    rng = random.Random(20260807)
    values = [rng.gauss(0.0, 1.0) for _ in range(300)]
    forward = _feed(cls, values)
    shuffled = values[:]
    rng.shuffle(shuffled)
    backward = _feed(cls, shuffled)
    return bool(
        abs(forward.mean - backward.mean) < 1e-9
        and abs(forward.variance - backward.variance) < 1e-9
    )


def _rejected_sample_leaves_state_untouched(cls: Any) -> bool:
    """§7 / §9.5 — validation happens before any mutation.

    An implementation that updates `n` and then validates leaves the accumulator
    permanently skewed by a value it claimed to reject.
    """
    acc = _feed(cls, [1.0, 2.0, 3.0])
    before = (acc.n, acc.mean, acc.variance)
    for bad in (float("nan"), float("inf"), float("-inf")):
        try:
            acc.update(bad)
        except ValueError:
            pass
        else:
            return False
    return bool((acc.n, acc.mean, acc.variance) == before)


def _extremes_are_tracked(cls: Any) -> bool:
    acc = _feed(cls, [5.0, -2.0, 9.0, 0.0])
    return bool(acc.minimum == -2.0 and acc.maximum == 9.0)


def _skewness_of_symmetric_data_is_near_zero(cls: Any) -> bool:
    """§4 — the M3 recurrence must read the *old* M2, or this drifts."""
    values = [-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0]
    return bool(abs(_feed(cls, values).skewness) < 1e-9)


def _insufficient_data_raises_rather_than_returning_nan(cls: Any) -> bool:
    """§4 — a silent nan propagates undetected; the spec demands an exception."""
    empty = cls()
    one = _feed(cls, [1.0])
    two = _feed(cls, [1.0, 2.0])
    for acc, attr in ((empty, "mean"), (one, "variance"), (two, "skewness")):
        try:
            _ = getattr(acc, attr)
        except Exception:  # noqa: BLE001 — any raise satisfies the contract
            continue
        return False
    return True


def _constant_stream_skewness_is_undefined(cls: Any) -> bool:
    """§4 — M2 == 0 makes the ratio 0/0, which must raise, not return 0."""
    acc = _feed(cls, [2.0] * 10)
    try:
        _ = acc.skewness
    except Exception:  # noqa: BLE001
        return True
    return False


def _update_many_accepts_a_generator(cls: Any) -> bool:
    """§11 — must not materialise the iterable into a list."""
    acc = cls()
    acc.update_many(float(i) for i in range(100))
    return bool(acc.n == 100)


ORACLE = Oracle(
    whitepaper="05_online_statistics.md",
    package_hint="stat",
    required_names=["RunningStats"],
    cases=[
        Case(
            target="RunningStats",
            call=False,
            check=_large_offset_variance_is_exact,
            description="§6.1 variance of [1e9, 1e9+1, 1e9+2] is 1.0 (naive formula fails)",
        ),
        Case(
            target="RunningStats",
            call=False,
            check=_never_negative_variance,
            description="§6.3 variance is never negative",
        ),
        Case(
            target="RunningStats",
            call=False,
            check=_constant_stream_has_exactly_zero_variance,
            description="§6.4 constant stream gives exactly 0.0",
        ),
        Case(
            target="RunningStats",
            call=False,
            check=_agrees_with_statistics_module,
            description="§6.2 matches the statistics module to 1e-9",
        ),
        Case(
            target="RunningStats",
            call=False,
            check=_merge_matches_unsplit,
            description="§6.5 merged partitions equal the unsplit computation",
        ),
        Case(
            target="RunningStats",
            call=False,
            check=_merge_does_not_mutate_inputs,
            description="§11 merge leaves both operands unchanged",
        ),
        Case(
            target="RunningStats",
            call=False,
            check=_order_independence,
            description="§9.4 results are independent of arrival order",
        ),
        Case(
            target="RunningStats",
            call=False,
            check=_rejected_sample_leaves_state_untouched,
            description="§7 a rejected sample does not mutate state",
        ),
        Case(
            target="RunningStats",
            call=False,
            check=_extremes_are_tracked,
            description="§2 min and max are tracked",
        ),
        Case(
            target="RunningStats",
            call=False,
            check=_skewness_of_symmetric_data_is_near_zero,
            description="§3 higher-moment update order is correct",
        ),
        Case(
            target="RunningStats",
            call=False,
            check=_insufficient_data_raises_rather_than_returning_nan,
            description="§4 too few samples raises instead of returning nan",
        ),
        Case(
            target="RunningStats",
            call=False,
            check=_constant_stream_skewness_is_undefined,
            description="§4 skewness of a constant stream raises",
        ),
        Case(
            target="RunningStats",
            call=False,
            check=_update_many_accepts_a_generator,
            description="§11 update_many consumes an iterable lazily",
        ),
    ],
    prohibitions=[
        Prohibition(
            reason=(
                "§12 forbids delegating to statistics/numpy/pandas — the "
                "recurrences are the deliverable, and the stdlib is permitted "
                "in tests as an oracle only"
            ),
            imports=("statistics", "numpy", "pandas"),
        ),
    ],
)
