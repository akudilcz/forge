"""Offline tests for the behavioural-oracle framework.

The oracle is the integration suite's only independent check on FORGE's output,
so it has to be trustworthy in both directions: it must pass a correct
implementation and, more importantly, it must **fail** a plausible-but-wrong one.
A silently permissive oracle is worse than none, because it manufactures
confidence.

These tests never call an LLM. They synthesise small "generated workspaces" on
disk — some correct, some subtly wrong in exactly the ways an agent gets things
wrong — and assert the oracle's verdict on each.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend.tests.integration.oracles import merge_sort
from backend.tests.integration.oracles._base import (
    Case,
    ErrorCase,
    Oracle,
    Prohibition,
    run_oracle,
)

# ── Synthetic "generated" implementations ────────────────────────────────────

_CORRECT_SORT = '''
"""A stable merge sort, as specified."""

MIN_RUN = 32


def _binary_insertion_sort(a, lo, hi, key):
    for i in range(lo + 1, hi):
        v = a[i]
        kv = key(v)
        left, right = lo, i
        while left < right:
            mid = left + (right - left) // 2
            if key(a[mid]) > kv:
                right = mid
            else:
                left = mid + 1
        a[left + 1 : i + 1] = a[left:i]
        a[left] = v


def _merge(a, lo, mid, hi, key):
    left = a[lo:mid]
    i, j, k = 0, mid, lo
    while i < len(left) and j < hi:
        if key(left[i]) <= key(a[j]):
            a[k] = left[i]
            i += 1
        else:
            a[k] = a[j]
            j += 1
        k += 1
    while i < len(left):
        a[k] = left[i]
        i += 1
        k += 1


def _sort_range(a, lo, hi, key):
    n = hi - lo
    if n < MIN_RUN:
        _binary_insertion_sort(a, lo, hi, key)
        return
    mid = lo + n // 2
    _sort_range(a, lo, mid, key)
    _sort_range(a, mid, hi, key)
    if key(a[mid - 1]) <= key(a[mid]):
        return
    _merge(a, lo, mid, hi, key)


def sort(data, *, key=lambda x: x, reverse=False):
    # Double reversal: reversing before and after an ascending stable sort
    # leaves equal elements in their original relative order (§3).
    if reverse:
        data.reverse()
    if len(data) >= 2:
        _sort_range(data, 0, len(data), key)
    if reverse:
        data.reverse()
    return None


def sorted_copy(data, *, key=lambda x: x, reverse=False):
    out = list(data)
    sort(out, key=key, reverse=reverse)
    return out


def is_sorted(data, *, key=lambda x: x, reverse=False):
    keys = [key(x) for x in data]
    pairs = zip(keys, keys[1:])
    if reverse:
        return all(a >= b for a, b in pairs)
    return all(a <= b for a, b in pairs)
'''

_CHEATING_SORT = '''
"""Passes every functional test while implementing nothing — §11 forbids this."""


def sort(data, *, key=lambda x: x, reverse=False):
    data.sort(key=key, reverse=reverse)
    return None


def sorted_copy(data, *, key=lambda x: x, reverse=False):
    return sorted(data, key=key, reverse=reverse)


def is_sorted(data, *, key=lambda x: x, reverse=False):
    keys = [key(x) for x in data]
    pairs = zip(keys, keys[1:])
    if reverse:
        return all(a >= b for a, b in pairs)
    return all(a <= b for a, b in pairs)
'''

_UNSTABLE_SORT = _CORRECT_SORT.replace(
    "if key(left[i]) <= key(a[j]):", "if key(left[i]) < key(a[j]):"
)
"""One character different: `<=` becomes `<`, which destroys stability (§3.1).

This is exactly the kind of defect a self-written test suite misses — the sort is
still correct, still O(n log n), and every ordering assertion still passes.
"""


def _write_workspace(tmp_path: Path, source: str, filename: str = "sorting.py") -> Path:
    ws = tmp_path / "workspace"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / filename).write_text(source, encoding="utf-8")
    return ws


# ── The oracle accepts a correct implementation ──────────────────────────────


def test_correct_implementation_passes(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path, _CORRECT_SORT)
    result = run_oracle(merge_sort.ORACLE, ws)
    assert result.ok, result.summary()
    assert len(result.passed) > 15


# ── The oracle rejects wrong implementations ─────────────────────────────────


def test_detects_the_forbidden_stdlib_shortcut(tmp_path: Path) -> None:
    """§11 — a wrapper around list.sort must be caught.

    Every behavioural case still passes here; only the prohibition check fails,
    which is the whole reason prohibitions exist.
    """
    ws = _write_workspace(tmp_path, _CHEATING_SORT)
    result = run_oracle(merge_sort.ORACLE, ws)

    assert not result.ok, "oracle accepted a list.sort wrapper"
    joined = " ".join(result.failures)
    assert ".sort()" in joined or "sorted()" in joined


def test_detects_a_subtle_stability_violation(tmp_path: Path) -> None:
    """§3.1 — the `<=` → `<` merge defect.

    The result is still fully sorted, so only a stability-specific check catches
    it. If this test ever passes the oracle, the oracle has stopped testing the
    property the whitepaper exists to guarantee.
    """
    ws = _write_workspace(tmp_path, _UNSTABLE_SORT)
    result = run_oracle(merge_sort.ORACLE, ws)

    assert not result.ok, "oracle accepted an unstable sort"
    assert any("stability" in f for f in result.failures), result.summary()


def test_reports_missing_public_api(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path, "def sort(data, **kw):\n    data.sort()\n")
    result = run_oracle(merge_sort.ORACLE, ws)

    assert not result.ok
    assert any("sorted_copy" in f for f in result.failures)


def test_reports_empty_workspace(tmp_path: Path) -> None:
    ws = tmp_path / "workspace"
    (ws / "src").mkdir(parents=True)
    result = run_oracle(merge_sort.ORACLE, ws)

    assert not result.ok
    assert any("no generated .py files" in f for f in result.failures)


def test_reports_absent_source_directory(tmp_path: Path) -> None:
    result = run_oracle(merge_sort.ORACLE, tmp_path / "nope")
    assert not result.ok
    assert any("no generated source directory" in f for f in result.failures)


def test_reports_unparseable_source(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path, "def sort(  # truncated\n")
    result = run_oracle(merge_sort.ORACLE, ws)
    assert not result.ok
    assert any("does not parse" in f for f in result.failures)


# ── Module resolution ────────────────────────────────────────────────────────


@pytest.mark.parametrize("filename", ["sorting.py", "merge_sort.py", "algorithms.py"])
def test_finds_the_module_whatever_the_agent_named_it(tmp_path: Path, filename: str) -> None:
    """The agent chooses its own module name; the oracle must still find it."""
    ws = _write_workspace(tmp_path, _CORRECT_SORT, filename=filename)
    result = run_oracle(merge_sort.ORACLE, ws)
    assert result.ok, result.summary()


def test_does_not_leak_modules_between_runs(tmp_path: Path) -> None:
    """Two workspaces defining the same module name must not bleed together.

    Successive scenarios generate different code under the same module name. If
    the first import stayed in sys.modules, the second oracle would silently
    grade the first build's code and report a false pass.
    """
    good = _write_workspace(tmp_path / "a", _CORRECT_SORT)
    bad = _write_workspace(tmp_path / "b", _CHEATING_SORT)

    assert run_oracle(merge_sort.ORACLE, good).ok
    second = run_oracle(merge_sort.ORACLE, bad)
    assert not second.ok, "stale module served the previous build's code"


# ── Framework mechanics ──────────────────────────────────────────────────────


def test_error_case_matches_exception_by_mro_name(tmp_path: Path) -> None:
    """A generated subclass satisfies a base-class expectation (§7 of spec 03)."""
    source = (
        "class ExpressionError(Exception):\n    pass\n\n\n"
        "class LexicalError(ExpressionError):\n    pass\n\n\n"
        "def evaluate(text):\n    raise LexicalError('bad char at 3')\n"
    )
    ws = _write_workspace(tmp_path, source, filename="expr.py")
    oracle = Oracle(
        whitepaper="t",
        package_hint="expr",
        error_cases=[
            ErrorCase(target="evaluate", args=("@",), exc_name="ExpressionError", match="bad char")
        ],
    )
    assert run_oracle(oracle, ws).ok


def test_error_case_fails_when_nothing_is_raised(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path, "def f(x):\n    return 1\n", filename="m.py")
    oracle = Oracle(
        whitepaper="t",
        package_hint="m",
        error_cases=[ErrorCase(target="f", args=(0,), exc_name="ValueError")],
    )
    result = run_oracle(oracle, ws)
    assert not result.ok
    assert any("instead of raising" in f for f in result.failures)


def test_error_case_fails_on_wrong_message(tmp_path: Path) -> None:
    ws = _write_workspace(
        tmp_path, "def f(x):\n    raise ValueError('something else')\n", filename="m.py"
    )
    oracle = Oracle(
        whitepaper="t",
        package_hint="m",
        error_cases=[
            ErrorCase(target="f", args=(0,), exc_name="ValueError", match="must be positive")
        ],
    )
    result = run_oracle(oracle, ws)
    assert not result.ok
    assert any("lacks" in f for f in result.failures)


def test_case_raising_is_reported_not_propagated(tmp_path: Path) -> None:
    """A crash in generated code is a recorded failure, not a suite error."""
    ws = _write_workspace(tmp_path, "def f(x):\n    raise RuntimeError('boom')\n", filename="m.py")
    oracle = Oracle(
        whitepaper="t", package_hint="m", cases=[Case(target="f", args=(1,), expected=1)]
    )
    result = run_oracle(oracle, ws)
    assert not result.ok
    assert any("RuntimeError" in f and "boom" in f for f in result.failures)


def test_collects_every_failure_rather_than_stopping_at_the_first(tmp_path: Path) -> None:
    ws = _write_workspace(
        tmp_path, "def f(x):\n    return 0\n\n\ndef g(x):\n    return 0\n", filename="m.py"
    )
    oracle = Oracle(
        whitepaper="t",
        package_hint="m",
        cases=[
            Case(target="f", args=(1,), expected=1),
            Case(target="g", args=(2,), expected=2),
        ],
    )
    result = run_oracle(oracle, ws)
    assert len(result.failures) == 2, result.summary()


def test_mutates_arg_checks_the_argument_not_the_return(tmp_path: Path) -> None:
    ws = _write_workspace(
        tmp_path, "def fill(target):\n    target.append(9)\n    return None\n", filename="m.py"
    )
    oracle = Oracle(
        whitepaper="t",
        package_hint="m",
        cases=[Case(target="fill", args=([],), expected=[9], mutates_arg=0)],
    )
    assert run_oracle(oracle, ws).ok


def test_prohibition_allows_a_module_defined_function_of_the_same_name(
    tmp_path: Path,
) -> None:
    """A module defining its own `sorted` is not calling the builtin.

    Without this carve-out the prohibition check would false-positive on any
    module whose own helper happens to share a builtin's name.
    """
    ws = _write_workspace(
        tmp_path,
        "def sorted(x):\n    return x\n\n\ndef go(x):\n    return sorted(x)\n",
        filename="m.py",
    )
    oracle = Oracle(
        whitepaper="t",
        package_hint="m",
        prohibitions=[Prohibition(reason="no builtin sorted", name_calls=("sorted",))],
    )
    assert run_oracle(oracle, ws).ok


def test_a_raising_check_is_one_failure_not_an_aborted_run(tmp_path: Path) -> None:
    """A scenario check that raises must not take down the whole oracle.

    `check` callables drive multi-step scenarios against generated code, so a
    wrong implementation frequently raises *inside* the check rather than
    returning False. While that call was unguarded, one such case aborted
    `run_oracle` entirely — the caller got a traceback instead of a conformance
    report, and every later case went unevaluated. Two oracle authors hit this
    and worked around it locally before it was fixed here.
    """

    def explodes(_: Any) -> bool:
        raise RuntimeError("scenario blew up")

    ws = _write_workspace(tmp_path, "def f(x):\n    return x\n", filename="m.py")
    oracle = Oracle(
        whitepaper="t",
        package_hint="m",
        cases=[
            Case(target="f", args=(1,), check=explodes, description="raising check"),
            Case(target="f", args=(2,), expected=2, description="later case"),
        ],
    )

    result = run_oracle(oracle, ws)

    assert any("raised RuntimeError" in f for f in result.failures), result.summary()
    assert "later case" in result.passed, (
        "the case after the raising one was never evaluated — the run aborted"
    )


def test_a_raising_equality_comparison_is_reported(tmp_path: Path) -> None:
    """Generated `__eq__` can raise; that is a failure, not a crash."""
    source = (
        "class Hostile:\n"
        "    def __eq__(self, other):\n"
        "        raise ValueError('no comparison for you')\n"
        "\n\n"
        "def f(x):\n"
        "    return Hostile()\n"
    )
    ws = _write_workspace(tmp_path, source, filename="m.py")
    oracle = Oracle(
        whitepaper="t", package_hint="m", cases=[Case(target="f", args=(1,), expected=1)]
    )

    result = run_oracle(oracle, ws)

    assert not result.ok
    assert any("verdict raised ValueError" in f for f in result.failures), result.summary()


def test_prohibition_catches_a_forbidden_import(tmp_path: Path) -> None:
    ws = _write_workspace(tmp_path, "import bisect\n\n\ndef f(a, v):\n    return 0\n", filename="m.py")
    oracle = Oracle(
        whitepaper="t",
        package_hint="m",
        prohibitions=[Prohibition(reason="§11 forbids bisect", imports=("bisect",))],
    )
    result = run_oracle(oracle, ws)
    assert not result.ok
    assert any("bisect" in f for f in result.failures)


def test_prohibition_ignores_mentions_in_docstrings(tmp_path: Path) -> None:
    """AST parsing, not substring search — a docstring reference is not a call."""
    ws = _write_workspace(
        tmp_path,
        '"""Do not use sorted() or bisect here."""\n\n\ndef f(x):\n    return x\n',
        filename="m.py",
    )
    oracle = Oracle(
        whitepaper="t",
        package_hint="m",
        prohibitions=[
            Prohibition(reason="r", imports=("bisect",), name_calls=("sorted",)),
        ],
    )
    assert run_oracle(oracle, ws).ok


# ── Prohibition matching must judge behaviour, not style (audit rank 14) ─────

_OWN_SORT_API = '''
"""A module whose own public class exposes .sort() — this is not list.sort.

The live failure this pins down: a genuine merge-sort build was rejected with
"calls .sort()" because its public API happened to be `engine.sort(a)` on a
local instance of its own class. The prohibition must judge what the code
*does*, not what its methods are named.
'''.rstrip() + '"""\n' + '''

class InsertionSorter:
    def sort(self, data):
        for i in range(1, len(data)):
            value = data[i]
            j = i - 1
            while j >= 0 and data[j] > value:
                data[j + 1] = data[j]
                j -= 1
            data[j + 1] = value
        return None


def sort(data):
    sorter = InsertionSorter()
    sorter.sort(data)
    return None
'''

_HIDDEN_LIST_SORT = '''
"""Delegates to list.sort in a way no style-based AST check can see."""


def sort(data):
    method = getattr(data, "so" + "rt")
    method()
    return None
'''

_ALIASED_SORTED = '''
"""Delegates to the sorted builtin through a module-level alias."""

_s = sorted


def sorted_copy(data):
    return _s(list(data))
'''

_NO_DELEGATION = Prohibition(
    reason="§11 forbids delegating to the built-in sort",
    name_calls=("sorted",),
    attr_calls=("sort",),
)


def test_own_sort_api_on_a_local_instance_is_not_flagged(tmp_path: Path) -> None:
    """`sorter = InsertionSorter(); sorter.sort(a)` is the module's own API."""
    ws = _write_workspace(tmp_path, _OWN_SORT_API, filename="m.py")
    oracle = Oracle(
        whitepaper="t",
        package_hint="m",
        cases=[Case(target="sort", args=([3, 1, 2],), expected=[1, 2, 3], mutates_arg=0)],
        prohibitions=[_NO_DELEGATION],
    )
    result = run_oracle(oracle, ws)
    assert result.ok, result.summary()


def test_hidden_list_sort_delegation_is_flagged_at_runtime(tmp_path: Path) -> None:
    """`getattr(data, "so" + "rt")()` defeats any static matcher; the dynamic
    monitor catches the actual list.sort invocation from the generated frame."""
    ws = _write_workspace(tmp_path, _HIDDEN_LIST_SORT, filename="m.py")
    oracle = Oracle(
        whitepaper="t",
        package_hint="m",
        cases=[Case(target="sort", args=([3, 1, 2],), expected=[1, 2, 3], mutates_arg=0)],
        prohibitions=[_NO_DELEGATION],
    )
    result = run_oracle(oracle, ws)
    assert not result.ok, "oracle accepted a hidden list.sort delegation"
    assert any("sort()" in f for f in result.failures), result.summary()


def test_aliased_builtin_sorted_is_flagged_at_runtime(tmp_path: Path) -> None:
    """`_s = sorted; _s(x)` never names `sorted` at a call site — only the
    dynamic monitor can attribute the call to the builtin."""
    ws = _write_workspace(tmp_path, _ALIASED_SORTED, filename="m.py")
    oracle = Oracle(
        whitepaper="t",
        package_hint="m",
        cases=[Case(target="sorted_copy", args=((3, 1, 2),), expected=[1, 2, 3])],
        prohibitions=[_NO_DELEGATION],
    )
    result = run_oracle(oracle, ws)
    assert not result.ok, "oracle accepted an aliased sorted() delegation"
    assert any("sorted()" in f for f in result.failures), result.summary()


def test_importing_a_prohibited_name_from_the_stdlib_is_flagged_statically(
    tmp_path: Path,
) -> None:
    """`from collections import OrderedDict` is provably the stdlib callable.

    Instantiating a C type emits no profiling event, so this delegation is
    invisible to the dynamic monitor — but the absolute import names its origin,
    which makes the static verdict sound.
    """
    source = "from collections import OrderedDict\n\n\ndef make():\n    return OrderedDict()\n"
    ws = _write_workspace(tmp_path, source, filename="m.py")
    oracle = Oracle(
        whitepaper="t",
        package_hint="m",
        prohibitions=[Prohibition(reason="no OrderedDict", name_calls=("OrderedDict",))],
    )
    result = run_oracle(oracle, ws)
    assert not result.ok
    assert any("OrderedDict" in f for f in result.failures), result.summary()


