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
  implementing nothing (``list.sort``, ``bisect``, ``eval``, ``graphlib``).
  Enforced by a hybrid of a static AST scan (kept only where it is provably
  sound) and a dynamic delegation monitor that watches the generated code
  actually execute — see ``Prohibition`` and ``_DelegationMonitor``.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import os
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

    Delegation is judged by what the code *does*, not what its names look like.
    An earlier, purely static matcher flagged any ``x.sort()`` on a bare-name
    receiver, which failed a genuine merge-sort build whose own public API was
    ``engine.sort(a)`` on a local instance of its own class — and which an
    alias (``s = sorted; s(x)``) evaded entirely. Enforcement is therefore
    split into two mechanisms:

    **Static AST scan** (``_check_prohibitions``) — kept only where the verdict
    is provably sound, so a mention in a docstring or the module's own API
    never trips it:

    * a direct ``import heapq`` (or ``from heapq import ...``) of a module in
      ``imports``;
    * ``from collections import OrderedDict`` — an absolute import, from a
      module the generated package does not own, of a callable named in
      ``name_calls``/``attr_calls``. Needed because instantiating a C type
      emits no profiling event, so the dynamic monitor cannot see it;
    * a bare call ``sorted(x)`` to a ``name_calls`` name the module binds
      nowhere (no def, class, assignment, parameter, or import alias) — such a
      name can only resolve to the builtin;
    * ``[..].sort()`` / ``list(x).sort()`` — an ``attr_calls`` method on a
      receiver that is literally a list expression.

    **Dynamic delegation monitor** (``_DelegationMonitor``) — while the
    oracle's behavioural cases execute the generated module, a
    ``sys.setprofile`` hook watches every call. A C-level call (``c_call``
    event: ``list.sort``, ``sorted``, ``heapq.heappush`` — including through
    any alias or ``getattr`` trick) or a call into a non-generated pure-Python
    function (``ast.literal_eval``) whose name is prohibited is a violation
    *iff the calling frame's file lives inside the generated source tree*.
    The module's own ``def sort``/``class Sorter`` live inside that tree, so
    its own API can never be flagged, whatever it is named.
    """

    reason: str
    imports: tuple[str, ...] = ()
    name_calls: tuple[str, ...] = ()
    """Bare function calls, e.g. ``sorted(x)`` or ``eval(s)``."""
    attr_calls: tuple[str, ...] = ()
    """Builtin method calls, e.g. ``data.sort()`` — enforced dynamically."""


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


@dataclass(frozen=True)
class _ModuleScan:
    """The provably-sound facts about one generated file — see ``Prohibition``."""

    imported_roots: frozenset[str]
    stdlib_from_imports: frozenset[str]
    """Names imported absolutely from modules the generated package does not own."""
    bound_names: frozenset[str]
    name_called: frozenset[str]
    list_attr_called: frozenset[str]
    """Methods called on a receiver that is literally ``[...]`` or ``list(...)``."""


def _is_list_expression(node: ast.expr) -> bool:
    return isinstance(node, ast.List) or (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "list"
    )


def _scan_module(tree: ast.AST, own_roots: frozenset[str]) -> _ModuleScan:
    imported: set[str] = set()
    from_names: set[str] = set()
    bound: set[str] = set()
    name_called: set[str] = set()
    list_attr_called: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
            bound.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            bound.update(alias.asname or alias.name for alias in node.names)
            if node.level == 0 and node.module:
                root = node.module.split(".")[0]
                imported.add(root)
                if root not in own_roots:
                    from_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            bound.add(node.name)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                name_called.add(node.func.id)
            elif isinstance(node.func, ast.Attribute) and _is_list_expression(node.func.value):
                list_attr_called.add(node.func.attr)
    return _ModuleScan(
        imported_roots=frozenset(imported),
        stdlib_from_imports=frozenset(from_names),
        bound_names=frozenset(bound),
        name_called=frozenset(name_called),
        list_attr_called=frozenset(list_attr_called),
    )


def _rule_failures(file_name: str, rule: Prohibition, scan: _ModuleScan) -> list[str]:
    """Apply one prohibition's provably-sound static checks to one file's scan."""
    failures: list[str] = []
    for bad in rule.imports:
        if bad in scan.imported_roots:
            failures.append(f"{file_name}: imports forbidden module {bad!r} — {rule.reason}")
    for bad in dict.fromkeys(rule.name_calls + rule.attr_calls):
        if bad in scan.stdlib_from_imports:
            failures.append(f"{file_name}: imports forbidden name {bad!r} — {rule.reason}")
    for bad in rule.name_calls:
        # A name the module binds nowhere can only resolve to the builtin. Any
        # locally bound use (own def/class/assignment/import alias) is left to
        # the dynamic monitor, which judges the actual callable.
        if bad in scan.name_called and bad not in scan.bound_names:
            failures.append(f"{file_name}: calls builtin {bad}() — {rule.reason}")
    for bad in rule.attr_calls:
        if bad in scan.list_attr_called:
            failures.append(f"{file_name}: calls list.{bad}() — {rule.reason}")
    return failures


def _check_prohibitions(src_dir: Path, prohibitions: Sequence[Prohibition]) -> list[str]:
    """Statically report forbidden imports or calls — sound checks only.

    Everything style-dependent (method calls on arbitrary receivers, aliased
    builtins) is deliberately absent here; ``_DelegationMonitor`` covers it by
    watching the code run.
    """
    files = _iter_source_files(src_dir)
    own_roots = frozenset(p.stem for p in files) | {src_dir.name, "tracing"}
    failures: list[str] = []
    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            failures.append(f"{path.name}: generated source does not parse: {exc}")
            continue
        scan = _scan_module(tree, own_roots)
        for rule in prohibitions:
            failures.extend(_rule_failures(path.name, rule, scan))
    return failures


class _DelegationMonitor:
    """Catch prohibited-callable invocations made *from* generated code.

    A ``sys.setprofile`` hook is the simplest mechanism that reliably attributes
    a call to the file that made it. ``list.sort`` and friends are C methods, so
    they cannot be monkeypatched and ``sys.addaudithook`` never sees them — but
    every C call raises a ``c_call`` profile event carrying the callable and the
    calling frame. Pure-Python stdlib delegation (``ast.literal_eval``, the
    ``heapq`` fallback) instead raises a ``call`` event in the callee, whose
    caller is one frame up. In both directions the verdict is behavioural: only
    the calling frame's location matters, so the generated module's own
    ``sort``/``sorted`` API can never be flagged and no alias can evade it.
    """

    def __init__(self, src_dir: Path, prohibitions: Sequence[Prohibition]) -> None:
        self._prefixes = tuple(
            f"{prefix}{os.sep}" for prefix in dict.fromkeys([str(src_dir), str(src_dir.resolve())])
        )
        self._reasons: dict[str, str] = {}
        for rule in prohibitions:
            for name in (*rule.name_calls, *rule.attr_calls):
                if name not in self._reasons:
                    self._reasons[name] = rule.reason
        self._generated_cache: dict[str, bool] = {}
        self._violations: dict[tuple[str, str], str] = {}

    def _is_generated(self, filename: str) -> bool:
        if filename not in self._generated_cache:
            self._generated_cache[filename] = filename.startswith(self._prefixes)
        return self._generated_cache[filename]

    def _record(self, filename: str, name: str) -> None:
        key = (Path(filename).name, name)
        if key not in self._violations:
            self._violations[key] = self._reasons[name]

    def _profile(self, frame: Any, event: str, arg: Any) -> None:
        if event == "c_call":
            # C callables without __name__ exist; such a callable cannot be one
            # of the prohibited stdlib names, so None is a correct non-match.
            name = getattr(arg, "__name__", None)
            if name in self._reasons and self._is_generated(frame.f_code.co_filename):
                self._record(frame.f_code.co_filename, name)
        elif event == "call":
            code = frame.f_code
            if code.co_name in self._reasons and not self._is_generated(code.co_filename):
                caller = frame.f_back
                if caller is not None and self._is_generated(caller.f_code.co_filename):
                    self._record(caller.f_code.co_filename, code.co_name)

    @contextmanager
    def active(self) -> Iterator[None]:
        previous = sys.getprofile()
        sys.setprofile(self._profile)
        try:
            yield
        finally:
            sys.setprofile(previous)

    def failures(self) -> list[str]:
        return [
            f"{file_name}: delegates to prohibited {name}() during execution — {reason}"
            for (file_name, name), reason in sorted(self._violations.items())
        ]


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

    static_failures = _check_prohibitions(src_dir, oracle.prohibitions)
    result.failures.extend(static_failures)

    monitor = _DelegationMonitor(src_dir, oracle.prohibitions)
    with _generated_on_path(src_dir), monitor.active():
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

    dynamic_failures = monitor.failures()
    result.failures.extend(dynamic_failures)
    if not static_failures and not dynamic_failures:
        result.passed.append(f"no forbidden shortcuts ({len(oracle.prohibitions)} rules)")

    return result
