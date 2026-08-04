"""Behavioural oracles for FORGE-generated code.

Every other quality signal in the integration suite is self-referential: the
generated tests pass, and coverage is high, but both the code and the tests were
written by the same agent from the same context. An agent that misreads the
specification writes code and tests that agree with each other and disagree with
the specification, and nothing currently catches that.

An oracle closes that hole. It is authored **from the whitepaper, not from the
generated code**, and it is never shown to any agent. After Phase 12 the suite
imports the generated module and runs the oracle against it. A failure means
FORGE built something that works but is not what was asked for.

Oracles check three things:

* **Cases** — concrete input/output pairs taken from the whitepaper's worked
  examples and its "Correctness Properties" section.
* **Errors** — inputs the whitepaper says must raise, and what they must raise.
  These catch the very common failure of implementing only the happy path.
* **Prohibitions** — the "Implementation Notes" clause of each whitepaper names a
  stdlib shortcut that would trivially satisfy every functional test while
  implementing nothing (``list.sort``, ``bisect``, ``eval``, ``graphlib``). The
  oracle greps the generated source to confirm the shortcut was not taken.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Case:
    """One concrete input/output expectation drawn from the whitepaper.

    ``target`` is a dotted path relative to the generated package, e.g.
    ``"sorting.sort"``. ``expected`` is compared with ``==`` unless ``check`` is
    given, in which case ``check(result)`` must return True.
    """

    target: str
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    expected: Any = None
    check: Callable[[Any], bool] | None = None
    mutates_arg: int | None = None
    """If set, compare ``expected`` against ``args[mutates_arg]`` after the call
    instead of against the return value — for in-place APIs such as ``sort``."""
    call: bool = True
    """When False, ``check`` receives the resolved object itself rather than the
    result of calling it. Used for multi-step scenarios against a class: the
    check constructs instances and drives a sequence of operations, which is the
    only way to express a property like "eviction order" that no single call
    exhibits."""
    description: str = ""

    def label(self) -> str:
        return self.description or f"{self.target}{self.args!r}"


@dataclass(frozen=True)
class ErrorCase:
    """An input the whitepaper says must raise."""

    target: str
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    exc_name: str = "ValueError"
    """Matched by class name up the MRO, so a generated subclass such as
    ``LexicalError(ExpressionError)`` satisfies ``exc_name="ExpressionError"``
    without the oracle needing to import the generated exception class."""
    match: str | None = None
    description: str = ""

    def label(self) -> str:
        return self.description or f"{self.target}{self.args!r} -> {self.exc_name}"


@dataclass(frozen=True)
class Prohibition:
    """A stdlib shortcut the whitepaper forbids.

    Checked by parsing the AST rather than by substring search, so a mention in a
    docstring or comment does not trip the check.

    Bare-name calls and method calls are kept separate because conflating them
    produces false positives. A merge-sort module legitimately defines and calls
    its own ``sort(...)``; what is forbidden is the *builtin* ``list.sort``,
    which only ever appears as the method call ``x.sort()``. So ``sort`` belongs
    in ``attr_calls`` and ``sorted`` — which the module never defines — belongs
    in ``name_calls``.
    """

    reason: str
    imports: tuple[str, ...] = ()
    name_calls: tuple[str, ...] = ()
    """Bare function calls, e.g. ``sorted(x)`` or ``eval(s)``."""
    attr_calls: tuple[str, ...] = ()
    """Builtin method calls on a plain local, e.g. ``data.sort()``.

    Matched only when the receiver is a bare Name. Real FORGE output is often
    object-oriented, so ``self._engine.sort(a, lo, hi)`` and ``API().sort(...)``
    are calls into the module's *own* classes, not ``list.sort`` — flagging
    those was a false positive that failed a build for using a perfectly good
    design. A receiver that is an attribute chain or a call result is therefore
    never matched; the cheat this catches is ``data.sort()`` on a local list.
    """


@dataclass
class Oracle:
    """The full independently-authored expectation for one whitepaper."""

    whitepaper: str
    package_hint: str
    """Substring used to locate the generated module when the agent chose its own
    module name — e.g. ``"sort"`` matches ``sorting.py`` or ``merge_sort.py``."""
    cases: Sequence[Case] = ()
    error_cases: Sequence[ErrorCase] = ()
    prohibitions: Sequence[Prohibition] = ()
    required_names: Sequence[str] = ()
    """Public API names from the whitepaper's API section that must exist."""


@dataclass
class OracleResult:
    """Outcome of running an oracle. Falsy ``failures`` means conformant."""

    passed: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures

    def summary(self) -> str:
        total = len(self.passed) + len(self.failures)
        lines = [f"oracle: {len(self.passed)}/{total} checks passed"]
        lines.extend(f"  FAIL  {f}" for f in self.failures)
        return "\n".join(lines)


@contextmanager
def _generated_on_path(src_dir: Path) -> Iterator[None]:
    """Put the generated code on sys.path and remove its modules after.

    Both ``src/`` and the workspace root go on the path. The root matters:
    phase 12 seeds a ``tracing/`` package beside ``src/`` and every generated
    file opens with ``from tracing import traces``. With only ``src/`` on the
    path, importing any real FORGE output died with
    ``ModuleNotFoundError: No module named 'tracing'`` and every behavioural
    check was reported as unresolvable — the oracle looked like it was failing
    the build when it had not managed to evaluate it at all. Offline reference
    implementations do not import ``tracing``, so the conformance suite could
    not surface this; only a real build did.

    Successive scenarios generate different code under the same module names, so
    stale entries in ``sys.modules`` would silently serve the previous build's
    code to the next oracle. Every module imported inside the block is dropped on
    exit.
    """
    before = set(sys.modules)
    added = [str(src_dir), str(src_dir.parent)]
    for entry in added:
        sys.path.insert(0, entry)
    try:
        yield
    finally:
        for entry in added:
            try:
                sys.path.remove(entry)
            except ValueError:  # pragma: no cover — defensive
                pass
        for name in set(sys.modules) - before:
            sys.modules.pop(name, None)


def _iter_source_files(src_dir: Path) -> list[Path]:
    return [p for p in sorted(src_dir.rglob("*.py")) if p.name != "__init__.py"]


def _resolve(src_dir: Path, target: str, package_hint: str) -> Any:
    """Resolve a dotted ``module.attr`` target against the generated package.

    The agent picks its own module name, so an exact match is tried first and
    then any generated module whose name contains ``package_hint`` and that
    exposes the attribute.
    """
    module_name, _, attr = target.rpartition(".")

    # Generated packages routinely use relative imports between their own
    # modules (``from .key_provider import ...``), which only resolve when the
    # module is imported as part of its package — so every candidate is tried
    # both bare and qualified as ``src.<name>``. Bare-only resolution failed
    # real output with "attempted relative import with no known parent package".
    stems = [p.stem for p in _iter_source_files(src_dir)]
    ranked = [s for s in stems if package_hint in s.lower()] + stems

    candidates: list[str] = []
    if module_name:
        candidates += [module_name, f"{src_dir.name}.{module_name}"]
    for stem in ranked:
        candidates += [stem, f"{src_dir.name}.{stem}"]

    tried: list[str] = []
    for name in dict.fromkeys(candidates):
        try:
            mod = importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 — any import problem is a miss
            tried.append(f"{name} ({type(exc).__name__}: {exc})")
            continue
        if hasattr(mod, attr):
            return getattr(mod, attr)
        tried.append(f"{name} (no attribute {attr!r})")

    raise LookupError(f"could not resolve {target!r}; tried: {'; '.join(tried) or 'nothing'}")


def _exc_matches(exc: BaseException, exc_name: str) -> bool:
    return any(klass.__name__ == exc_name for klass in type(exc).__mro__)


def _check_prohibitions(src_dir: Path, prohibitions: Sequence[Prohibition]) -> list[str]:
    """Parse each generated file and report forbidden imports or calls."""
    failures: list[str] = []
    for path in _iter_source_files(src_dir):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            failures.append(f"{path.name}: generated source does not parse: {exc}")
            continue

        imported: set[str] = set()
        name_called: set[str] = set()
        attr_called: set[str] = set()
        defined: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                defined.add(node.name)
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    name_called.add(func.id)
                elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                    # Only a bare receiver counts — see Prohibition.attr_calls.
                    # `self._engine.sort(...)` and `API().sort(...)` are the
                    # module's own methods, not the builtin.
                    if func.value.id != "self":
                        attr_called.add(func.attr)

        for rule in prohibitions:
            for bad in rule.imports:
                if bad in imported:
                    failures.append(f"{path.name}: imports forbidden module {bad!r} — {rule.reason}")
            for bad in rule.name_calls:
                # A module that defines its own function of this name is calling
                # that, not the builtin.
                if bad in name_called and bad not in defined:
                    failures.append(f"{path.name}: calls builtin {bad}() — {rule.reason}")
            for bad in rule.attr_calls:
                if bad in attr_called:
                    failures.append(f"{path.name}: calls .{bad}() — {rule.reason}")
    return failures


def run_oracle(oracle: Oracle, workspace: Path) -> OracleResult:
    """Run ``oracle`` against the code generated into ``workspace/src``.

    Never raises for a conformance failure — failures are collected so the caller
    sees every problem at once rather than only the first.
    """
    result = OracleResult()
    src_dir = workspace / "src"

    if not src_dir.is_dir():
        result.failures.append(f"no generated source directory at {src_dir}")
        return result
    if not _iter_source_files(src_dir):
        result.failures.append(f"no generated .py files under {src_dir}")
        return result

    result.failures.extend(_check_prohibitions(src_dir, oracle.prohibitions))
    if not _check_prohibitions(src_dir, oracle.prohibitions):
        result.passed.append(f"no forbidden shortcuts ({len(oracle.prohibitions)} rules)")

    with _generated_on_path(src_dir):
        for name in oracle.required_names:
            try:
                _resolve(src_dir, name, oracle.package_hint)
                result.passed.append(f"API present: {name}")
            except LookupError as exc:
                result.failures.append(f"missing public API {name!r}: {exc}")

        for case in oracle.cases:
            try:
                fn = _resolve(src_dir, case.target, oracle.package_hint)
            except LookupError as exc:
                result.failures.append(f"{case.label()}: {exc}")
                continue
            try:
                returned = fn if not case.call else fn(*case.args, **case.kwargs)
            except Exception as exc:  # noqa: BLE001 — any raise is a case failure
                result.failures.append(
                    f"{case.label()}: raised {type(exc).__name__}: {exc}"
                )
                continue

            actual = case.args[case.mutates_arg] if case.mutates_arg is not None else returned

            # Verdict evaluation must be guarded too, not just the call. A
            # `check` routinely drives a whole scenario against generated code,
            # so a wrong implementation can raise *inside* the check — and an
            # unguarded raise here aborted the entire oracle run, turning one
            # bad case into a traceback with no conformance report at all.
            # `actual == case.expected` is equally exposed: a generated
            # `__eq__` may raise.
            try:
                if case.check is not None:
                    verdict = case.check(actual)
                    detail = f"check rejected {actual!r}"
                else:
                    verdict = bool(actual == case.expected)
                    detail = f"expected {case.expected!r}, got {actual!r}"
            except Exception as exc:  # noqa: BLE001 — a raising verdict is a failure
                result.failures.append(
                    f"{case.label()}: verdict raised {type(exc).__name__}: {exc}"
                )
                continue

            if verdict:
                result.passed.append(case.label())
            else:
                result.failures.append(f"{case.label()}: {detail}")

        for err in oracle.error_cases:
            try:
                fn = _resolve(src_dir, err.target, oracle.package_hint)
            except LookupError as exc:
                result.failures.append(f"{err.label()}: {exc}")
                continue
            try:
                returned = fn(*err.args, **err.kwargs)
            except Exception as exc:  # noqa: BLE001 — we are asserting on the raise
                if not _exc_matches(exc, err.exc_name):
                    result.failures.append(
                        f"{err.label()}: raised {type(exc).__name__}, expected {err.exc_name}"
                    )
                elif err.match is not None and err.match.lower() not in str(exc).lower():
                    result.failures.append(
                        f"{err.label()}: message {str(exc)!r} lacks {err.match!r}"
                    )
                else:
                    result.passed.append(err.label())
            else:
                result.failures.append(
                    f"{err.label()}: returned {returned!r} instead of raising {err.exc_name}"
                )

    return result
