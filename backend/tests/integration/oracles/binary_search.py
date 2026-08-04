"""Oracle for whitepapers/07_binary_search_family.md.

Authored from the whitepaper only; never shown to any agent.

Binary search is the whitepaper where an oracle earns its keep. Every routine is
five lines long and looks obviously right; the difficulty is entirely in the
boundaries. So this oracle leans on two things a hand-written suite rarely does:

* **A differential check against `bisect`.** §8.4 names the stdlib as the oracle
  for `lower_bound`/`upper_bound`, so hundreds of random cases are compared
  against it. The generated code may not call `bisect` (§11) — but this file may.
* **Termination.** §8.5 requires every routine to terminate on *any* input,
  including unsorted. A wrong midpoint plus a wrong shrink rule is an infinite
  loop, so those checks run under a watchdog thread rather than hanging the run.
"""

from __future__ import annotations

import bisect
import random
from typing import Any

from backend.tests.integration.oracles._base import Case, ErrorCase, Oracle, Prohibition

_TIMEOUT_SECONDS = 5.0


def _completes(fn: Any, *args: Any, **kwargs: Any) -> tuple[bool, Any]:
    """Run ``fn`` under a timeout so a non-terminating routine fails, not hangs.

    §8.5 makes termination an explicit correctness property, which means the
    oracle has to be able to *observe* non-termination. A daemon thread lets the
    check report a failure and move on; the stuck thread dies with the process.
    """
    import threading

    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["value"] = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 — recorded as the outcome
            box["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(_TIMEOUT_SECONDS)
    if thread.is_alive():
        return False, None
    if "error" in box:
        raise box["error"]
    return True, box.get("value")


def _matches_bisect_left(fn: Any) -> bool:
    """§8.4 — lower_bound must agree with bisect.bisect_left everywhere."""
    rng = random.Random(20260803)
    for _ in range(300):
        n = rng.randint(0, 40)
        # Small value range guarantees plenty of duplicates and out-of-range probes.
        data = sorted(rng.randint(0, 12) for _ in range(n))
        value = rng.randint(-2, 14)
        if fn(data, value) != bisect.bisect_left(data, value):
            return False
    return True


def _matches_bisect_right(fn: Any) -> bool:
    """§8.4 — upper_bound must agree with bisect.bisect_right everywhere."""
    rng = random.Random(20260804)
    for _ in range(300):
        n = rng.randint(0, 40)
        data = sorted(rng.randint(0, 12) for _ in range(n))
        value = rng.randint(-2, 14)
        if fn(data, value) != bisect.bisect_right(data, value):
            return False
    return True


def _equal_range_counts_occurrences(fn: Any) -> bool:
    """§8.3 — the width of the range is exactly the number of occurrences."""
    rng = random.Random(20260805)
    for _ in range(200):
        data = sorted(rng.randint(0, 8) for _ in range(rng.randint(0, 30)))
        value = rng.randint(-1, 9)
        lo, hi = fn(data, value)
        if lo > hi or hi - lo != data.count(value):
            return False
    return True


def _insertion_keeps_sorted(fn: Any) -> bool:
    """§8.2 — inserting at the returned index preserves sortedness."""
    rng = random.Random(20260806)
    for _ in range(200):
        data = sorted(rng.randint(0, 20) for _ in range(rng.randint(0, 25)))
        value = rng.randint(-3, 23)
        idx = fn(data, value)
        probe = data[:idx] + [value] + data[idx:]
        if probe != sorted(probe):
            return False
    return True


def _terminates_on_unsorted_input(fn: Any) -> bool:
    """§8.5 — undefined result is acceptable; hanging is not."""
    rng = random.Random(20260807)
    for _ in range(50):
        data = [rng.randint(0, 50) for _ in range(rng.randint(2, 30))]
        completed, _ = _completes(fn, data, rng.randint(0, 50))
        if not completed:
            return False
    return True


def _peak_result_is_actually_a_peak(fn: Any) -> bool:
    """§5 — any index may be returned, but it must satisfy the peak property."""
    rng = random.Random(20260808)
    for _ in range(200):
        data = [rng.randint(0, 30) for _ in range(rng.randint(1, 25))]
        completed, idx = _completes(fn, data)
        if not completed or not isinstance(idx, int) or not 0 <= idx < len(data):
            return False
        left_ok = idx == 0 or data[idx] >= data[idx - 1]
        right_ok = idx == len(data) - 1 or data[idx] >= data[idx + 1]
        if not (left_ok and right_ok):
            return False
    return True


def _rotated_search_finds_present_values(fn: Any) -> bool:
    """§4 — correct across every rotation, including 0 and len(seq)."""
    base = [1, 3, 5, 7, 9, 11, 13]
    for shift in range(len(base) + 1):
        rotated = base[shift:] + base[:shift]
        for value in base:
            completed, idx = _completes(fn, rotated, value)
            if not completed or idx is None or rotated[idx] != value:
                return False
        completed, idx = _completes(fn, rotated, 4)
        if not completed or idx is not None:
            return False
    return True


def _comparison_count_is_logarithmic(fn: Any) -> bool:
    """§8.6 — at most ceil(log2(n+1)) comparisons.

    A linear scan satisfies every functional case above, so this is what
    distinguishes an actual binary search from a loop that happens to work.
    """
    import math

    counter = {"n": 0}

    class Counting(int):
        def __lt__(self, other: Any) -> bool:
            counter["n"] += 1
            return int(self) < int(other)

        def __gt__(self, other: Any) -> bool:
            counter["n"] += 1
            return int(self) > int(other)

        def __le__(self, other: Any) -> bool:
            counter["n"] += 1
            return int(self) <= int(other)

        def __ge__(self, other: Any) -> bool:
            counter["n"] += 1
            return int(self) >= int(other)

    for size in (1000, 5000):
        data = [Counting(i) for i in range(size)]
        counter["n"] = 0
        fn(data, size // 3)
        if counter["n"] > math.ceil(math.log2(size + 1)) + 2:
            return False
    return True


def _bisect_predicate_finds_boundary(fn: Any) -> bool:
    """§6 — locate the monotone boundary within tolerance."""
    result = fn(lambda x: x >= 2.5, 0.0, 10.0, tolerance=1e-6)
    return bool(abs(float(result) - 2.5) < 1e-4)


ORACLE = Oracle(
    whitepaper="07_binary_search_family.md",
    package_hint="search",
    required_names=[
        "lower_bound",
        "upper_bound",
        "equal_range",
        "search",
        "contains",
        "search_rotated",
        "find_peak",
        "bisect_predicate",
    ],
    cases=[
        # §9 — the boundaries that a wrong `hi` initialiser gets wrong in one
        # direction only, which is why both extremes are probed.
        Case(target="lower_bound", args=([], 5), expected=0, description="§9 empty sequence"),
        Case(
            target="lower_bound",
            args=([1, 2, 3], 0),
            expected=0,
            description="§9 value below every element",
        ),
        Case(
            target="lower_bound",
            args=([1, 2, 3], 9),
            expected=3,
            description="§9 value above every element",
        ),
        Case(
            target="lower_bound",
            args=([2, 2, 2], 2),
            expected=0,
            description="§9 all elements equal — leftmost",
        ),
        Case(
            target="upper_bound",
            args=([2, 2, 2], 2),
            expected=3,
            description="§9 all elements equal — rightmost",
        ),
        Case(
            target="lower_bound",
            args=([1, 3], 2),
            expected=1,
            description="§9 two adjacent elements (midpoint rounding)",
        ),
        Case(target="search", args=([1, 3, 5], 9), expected=None, description="§3.4 absent"),
        Case(target="contains", args=([1, 3, 5], 3), expected=True, description="§3.5 present"),
        Case(target="contains", args=([1, 3, 5], 4), expected=False, description="§3.5 absent"),
        # Differential checks against the stdlib oracle
        Case(
            target="lower_bound",
            call=False,
            check=_matches_bisect_left,
            description="§8.4 agrees with bisect_left over 300 random cases",
        ),
        Case(
            target="upper_bound",
            call=False,
            check=_matches_bisect_right,
            description="§8.4 agrees with bisect_right over 300 random cases",
        ),
        Case(
            target="equal_range",
            call=False,
            check=_equal_range_counts_occurrences,
            description="§8.3 range width equals the occurrence count",
        ),
        Case(
            target="lower_bound",
            call=False,
            check=_insertion_keeps_sorted,
            description="§8.2 inserting at the bound preserves sortedness",
        ),
        # Properties no functional case can express
        Case(
            target="lower_bound",
            call=False,
            check=_comparison_count_is_logarithmic,
            description="§8.6 uses O(log n) comparisons, not a linear scan",
        ),
        Case(
            target="lower_bound",
            call=False,
            check=_terminates_on_unsorted_input,
            description="§8.5 terminates on unsorted input",
        ),
        Case(
            target="find_peak",
            call=False,
            check=_peak_result_is_actually_a_peak,
            description="§5 returned index satisfies the peak property",
        ),
        Case(
            target="search_rotated",
            call=False,
            check=_rotated_search_finds_present_values,
            description="§4 correct across every rotation, including none",
        ),
        Case(
            target="bisect_predicate",
            call=False,
            check=_bisect_predicate_finds_boundary,
            description="§6 locates a monotone boundary within tolerance",
        ),
    ],
    error_cases=[
        ErrorCase(
            target="find_peak",
            args=([],),
            exc_name="ValueError",
            description="§9 find_peak on an empty sequence raises",
        ),
        ErrorCase(
            target="bisect_predicate",
            args=(lambda x: x >= 5.0, 6.0, 10.0),
            exc_name="ValueError",
            description="§6 predicate already True at lo must raise",
        ),
    ],
    prohibitions=[
        Prohibition(
            reason=(
                "§11 forbids calling the bisect module from library code — it is "
                "permitted in tests as an oracle only, and importing it in src/ "
                "means the algorithms were never written"
            ),
            imports=("bisect",),
        ),
    ],
)
