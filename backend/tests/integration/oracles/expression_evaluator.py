"""Oracle for whitepapers/03_expression_evaluator.md.

Authored from the whitepaper only; never shown to any agent.

An expression evaluator is the whitepaper where a wrong build is most convincing:
`2 + 3 * 4` is 14 under almost any parser, so a left-associative `^`, a unary
minus that outranks `^`, an evaluator that floats every integer, or errors whose
offsets are off by one all survive a casual test suite intact. So the weight here
sits on the four places §3, §3.1, §6.1 and §7 make a specific and unusual
commitment:

* `^` is right-associative — `2^3^2` is 512, not 64 (§3);
* `^` outranks unary minus — `-3^2` is -9, not 9 (§3.1);
* `/` always returns a float and `+ - *` stay exactly integral (§6.1);
* every error carries the offset of the character that caused it (§7, §9.3).

Two checks are differential rather than exemplary. CPython's own expression
grammar has precisely the precedence table of §3 — `**` right-associative and
above unary minus, `%` taking the sign of its divisor — so `eval` is an exact
model of the specification and this file uses it as one, on the very shortcut
§12 forbids the generated code from taking. And §9.5's round-trip claim is
checked by evaluating `to_rpn`'s own output here, which catches a parser and an
evaluator that are each plausible but disagree.

Section references below point at the whitepaper clause each check enforces.
"""

from __future__ import annotations

import functools
import math
import operator
import random
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import MappingProxyType
from typing import Any

from backend.tests.integration.oracles._base import Case, ErrorCase, Oracle, Prohibition


def _safe(check: Callable[[Any], bool]) -> Callable[[Any], bool]:
    """Turn a raise from the generated code into a reported failure.

    ``run_oracle`` guards the *call* of a case target but not the ``check``
    itself, so an exception escaping a multi-step check aborts the whole oracle
    run instead of being collected.
    """

    @functools.wraps(check)
    def wrapper(obj: Any) -> bool:
        try:
            return check(obj)
        except Exception:  # noqa: BLE001 — any raise mid-property is a failure
            return False

    return wrapper


def _get(obj: Any, *names: str) -> dict[str, Any] | None:
    """Resolve sibling public names from the module that defines ``obj``.

    §9.5 and §5 are properties of the tokenizer *and* the parser together, but a
    check only receives one resolved object. The agent may have split the API
    across modules, so the search widens to any generated module in the same
    directory. Returns None rather than raising.
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


def _exc_named(exc: BaseException, name: str) -> bool:
    """§7 — match by class name up the MRO, as ``_base.ErrorCase`` does."""
    return any(klass.__name__ == name for klass in type(exc).__mro__)


def _raised(fn: Any, *args: Any) -> BaseException | None:
    try:
        fn(*args)
    except Exception as exc:  # noqa: BLE001 — the raise is the subject of the check
        return exc
    return None


def _is_number(value: Any) -> bool:
    return bool(isinstance(value, int | float) and not isinstance(value, bool))


def _kind_of(token: Any) -> str:
    """Normalise ``Token.kind`` so a str, an Enum or a str-Enum all compare.

    §11 types it as ``str`` and §2 names the six kinds in upper case; an Enum
    member is accepted because its *name* still carries the specified kind.
    """
    kind = getattr(token, "kind", None)
    text = kind if isinstance(kind, str) else str(getattr(kind, "name", kind))
    return text.rsplit(".", 1)[-1].upper()


# ── §2 lexical grammar ───────────────────────────────────────────────────────

_TOKEN_CASES: list[tuple[str, list[tuple[str, str, int]]]] = [
    ("12 + 3.5", [("NUMBER", "12", 0), ("OPERATOR", "+", 3), ("NUMBER", "3.5", 5)]),
    (
        "  x*(1)",
        [
            ("IDENT", "x", 2),
            ("OPERATOR", "*", 3),
            ("LPAREN", "(", 4),
            ("NUMBER", "1", 5),
            ("RPAREN", ")", 6),
        ],
    ),
    (
        "min(1, 2)",
        [
            ("IDENT", "min", 0),
            ("LPAREN", "(", 3),
            ("NUMBER", "1", 4),
            ("COMMA", ",", 5),
            ("NUMBER", "2", 7),
            ("RPAREN", ")", 8),
        ],
    ),
    ("1e-3", [("NUMBER", "1e-3", 0)]),
    (".5", [("NUMBER", ".5", 0)]),
    ("2.5E2", [("NUMBER", "2.5E2", 0)]),
    # §3.1 — "The tokenizer does not decide this; the parser does." A tokenizer
    # that folds the sign into the literal produces one token, not two, and then
    # cannot distinguish `1-2` from `1 -2`.
    ("-1", [("OPERATOR", "-", 0), ("NUMBER", "1", 1)]),
]


@_safe
def _tokens_carry_kind_text_and_offset(tokenize: Any) -> bool:
    """§2 — every token carries its kind, its literal text and its start index."""
    for expression, expected in _TOKEN_CASES:
        tokens = list(tokenize(expression))
        if len(tokens) != len(expected):
            return False
        for token, (kind, text, offset) in zip(tokens, expected, strict=True):
            if _kind_of(token) != kind or token.text != text or token.offset != offset:
                return False
    return True


# ── §5 shunting-yard output order ────────────────────────────────────────────

# The RPN token texts are fully determined by §3 and §5, and they expose the two
# choices a value-only test cannot separate: `2^3^2` becomes `2 3 2 ^ ^` under a
# right-associative `^` and `2 3 ^ 2 ^` under a left-associative one.
_RPN_SHAPES: list[tuple[str, list[str]]] = [
    ("2 + 3 * 4", ["2", "3", "4", "*", "+"]),
    ("(2 + 3) * 4", ["2", "3", "+", "4", "*"]),
    ("2^3^2", ["2", "3", "2", "^", "^"]),
    ("1 - 2 - 3", ["1", "2", "-", "3", "-"]),
    ("2 * 3 + 4 / 5", ["2", "3", "*", "4", "5", "/", "+"]),
    ("2 ^ 3 * 4", ["2", "3", "^", "4", "*"]),
    ("max(1, 2 + 3)", ["1", "2", "3", "+", "max"]),
]

_BINARY: dict[str, Callable[[Any, Any], Any]] = {
    "+": operator.add,
    "-": operator.sub,
    "*": operator.mul,
    "/": operator.truediv,
    "%": operator.mod,
    "^": operator.pow,
}

_ROUND_TRIP = [
    "2 + 3 * 4",
    "(2 + 3) * 4",
    "2^3^2",
    "1 - 2 - 3",
    "100 / 10 / 2",
    "7 % 4 + 1",
    "2 * (3 + 4) - 5",
    "2 ^ 2 ^ 3 - 1",
]


@_safe
def _rpn_order_encodes_precedence(to_rpn: Any) -> bool:
    """§5 + §3 — infix becomes RPN in exactly the order the tables dictate."""
    found = _get(to_rpn, "tokenize")
    if found is None:
        return False
    tokenize = found["tokenize"]
    for expression, expected in _RPN_SHAPES:
        if [token.text for token in to_rpn(tokenize(expression))] != expected:
            return False
    return True


@_safe
def _to_rpn_leaves_its_input_alone(to_rpn: Any) -> bool:
    """§11 — ``to_rpn`` takes a Sequence and returns a list; it consumes nothing.

    An implementation that pops from the token list it was handed answers the
    first call correctly and every later one with garbage.
    """
    found = _get(to_rpn, "tokenize")
    if found is None:
        return False
    tokens = found["tokenize"]("2 + 3 * (4 - 1)")
    before = [(_kind_of(t), t.text, t.offset) for t in tokens]
    first = [t.text for t in to_rpn(tokens)]
    second = [t.text for t in to_rpn(tokens)]
    after = [(_kind_of(t), t.text, t.offset) for t in tokens]
    return bool(first == second and before == after and len(first) == 7)


@_safe
def _rpn_round_trips_through_evaluate(to_rpn: Any) -> bool:
    """§9.5 — evaluating ``to_rpn`` output reproduces ``evaluate``.

    Evaluated *here*, on a stack the oracle owns, so a parser and an evaluator
    that are individually plausible but disagree about precedence are caught.
    """
    found = _get(to_rpn, "tokenize", "evaluate")
    if found is None:
        return False
    tokenize, evaluate = found["tokenize"], found["evaluate"]
    for expression in _ROUND_TRIP:
        stack: list[float] = []
        for token in to_rpn(tokenize(expression)):
            if token.text in _BINARY:
                right = stack.pop()
                left = stack.pop()
                stack.append(_BINARY[token.text](left, right))
            else:
                stack.append(float(token.text))
        if len(stack) != 1:
            return False
        if not math.isclose(stack[0], float(evaluate(expression)), rel_tol=1e-12):
            return False
    return True


# ── §6.1 arithmetic semantics ────────────────────────────────────────────────


@_safe
def _numeric_types_follow_the_spec(evaluate: Any) -> bool:
    """§6.1 — `+ - *` stay integral, `/` is always float, floats infect.

    An implementation that coerces everything to float passes every value test
    and silently loses exactness above 2^53; one that keeps `/` integral gets
    `4 / 2` right and `7 / 2` wrong.
    """
    for expression, want in (("1 + 2", 3), ("7 - 9", -2), ("6 * 7", 42), ("2 + 3 * 4", 14)):
        value = evaluate(expression)
        if type(value) is not int or value != want:
            return False
    for expression, want_float in (("4 / 2", 2.0), ("7 / 2", 3.5), ("1 + 2.0", 3.0)):
        value = evaluate(expression)
        if type(value) is not float or value != want_float:
            return False
    # Exactness is the point of keeping integers integral (§2.1).
    big = evaluate("999999999999999999 + 1")
    return bool(type(big) is int and big == 1000000000000000000)


@_safe
def _modulo_takes_the_sign_of_the_divisor(evaluate: Any) -> bool:
    """§6.1 — Python's convention, not C's: `-7 % 3` is 2, not -1."""
    return bool(
        evaluate("7 % 3") == 1
        and evaluate("-7 % 3") == 2
        and evaluate("7 % -3") == -2
        and evaluate("-7 % -3") == -1
    )


_FUNCTION_CASES: list[tuple[str, float]] = [
    ("abs(-5)", 5),
    ("abs(3 - 10)", 7),
    ("min(4, 2, 8)", 2),
    ("max(1, 2, 3)", 3),
    ("min(3, 1)", 1),
    ("max(-1, -9)", -1),
    ("sqrt(16)", 4.0),
    ("sqrt(2) * sqrt(2)", 2.0),
    ("floor(3.7)", 3),
    ("floor(-3.2)", -4),
    ("ceil(3.2)", 4),
    ("ceil(-3.7)", -3),
    ("round(2.4)", 2),
    ("round(3.14159, 2)", 3.14),
    ("max(1, 2) + min(3, 4)", 5),
    ("max(2 * 3, 4 + 1)", 6),
]


@_safe
def _builtin_functions(evaluate: Any) -> bool:
    """§4 — the seven built-ins, including variadic min/max and 2-arg round."""
    for expression, want in _FUNCTION_CASES:
        got = evaluate(expression)
        if not _is_number(got) or not math.isclose(float(got), float(want), abs_tol=1e-12):
            return False
    return True


@_safe
def _builtin_constants(evaluate: Any) -> bool:
    """§4 — `pi` and `e` are the mathematical constants, not names to look up."""
    return bool(
        math.isclose(evaluate("pi"), math.pi, rel_tol=1e-12)
        and math.isclose(evaluate("e"), math.e, rel_tol=1e-12)
        and math.isclose(evaluate("2 * pi"), 2 * math.pi, rel_tol=1e-12)
    )


@_safe
def _variables_shadow_constants_but_not_functions(evaluate: Any) -> bool:
    """§4 — the shadowing rule, in both directions."""
    return bool(
        evaluate("pi", {"pi": 3}) == 3
        and evaluate("e + 1", {"e": 1}) == 2
        and evaluate("abs(-2)", {"abs": 99}) == 2
        and evaluate("max(1, 4)", {"max": 0}) == 4
    )


@_safe
def _environments_are_read_only_and_do_not_leak(evaluate: Any) -> bool:
    """§11 — ``variables`` is a Mapping: read, never written, never retained.

    An implementation that merges the constants into the caller's dict, or that
    stashes the environment in module state, is wrong in a way no single call
    reveals.
    """
    env = {"x": 5, "y": 2}
    if evaluate("x * y + 1", env) != 11:
        return False
    if env != {"x": 5, "y": 2}:
        return False
    if evaluate("x * y + 1", {"x": 1, "y": 1}) != 2:
        return False
    if evaluate("x", MappingProxyType({"x": 4})) != 4:
        return False
    leaked = _raised(evaluate, "x + 1")
    return bool(leaked is not None and _exc_named(leaked, "ExpressionError"))


# ── §7 / §9.3 error model ────────────────────────────────────────────────────

# Each entry is exactly the offset the whitepaper pins down: §7's rendered
# example puts the caret under the `*` of `1 + * 2`; §5.6 reports an unmatched
# `)` at the `)`; §5's final drain reports an unclosed `(` at the `(`; §6.1
# reports a division by zero at the operator; §4 reports an unknown name at the
# name.
_OFFSET_CASES: list[tuple[str, int, str]] = [
    ("1 + * 2", 4, "SyntaxError_"),
    ("1 $ 2", 2, "LexicalError"),
    ("1 + 2)", 5, "SyntaxError_"),
    ("(1 + 2", 0, "SyntaxError_"),
    ("((1)", 0, "SyntaxError_"),
    ("1/0", 1, "MathError"),
    ("5 % 0", 2, "MathError"),
    ("2 + foo", 4, "NameError_"),
    ("1 @ 2", 2, "LexicalError"),
]


@_safe
def _error_offsets_are_exact(evaluate: Any) -> bool:
    """§9.3 — the reported offset indexes the character that caused the error."""
    for expression, offset, exc_name in _OFFSET_CASES:
        exc = _raised(evaluate, expression)
        if exc is None or not _exc_named(exc, exc_name):
            return False
        if not _exc_named(exc, "ExpressionError"):
            return False
        if getattr(exc, "offset", None) != offset:
            return False
        if getattr(exc, "expression", None) != expression:
            return False
    return True


@_safe
def _render_points_a_caret_at_the_offset(evaluate: Any) -> bool:
    """§7 — the rendered form shows the expression and a caret under `offset`."""
    for expression, offset, _ in _OFFSET_CASES:
        exc = _raised(evaluate, expression)
        if exc is None:
            return False
        message = getattr(exc, "message", None)
        if not isinstance(message, str) or not message.strip():
            return False
        render = getattr(exc, "render", None)
        if not callable(render):
            return False
        rendered = render()
        if not isinstance(rendered, str) or expression not in rendered:
            return False
        lines = rendered.splitlines()
        for index, line in enumerate(lines[:-1]):
            if expression in line and "^" in lines[index + 1]:
                if lines[index + 1].index("^") - line.index(expression) != offset:
                    return False
                break
        else:
            return False
    return True


# ── §9.4 totality and the stdlib differential ────────────────────────────────

# `^` is deliberately absent: a fuzzer that can write `9^9^9` asks for an integer
# with half a billion digits, which is a hang rather than a test.
_FUZZ_ALPHABET = "0123456789+-*/%(), .xepi$"

_ATOMS = ["0", "1", "2", "3", "7", "9", "10", "2.5", "0.5", ".5"]
_BINOPS = ["+", "-", "*", "/", "%"]


def _random_expression(rng: random.Random, depth: int) -> str:
    """A well-formed expression per §2 and §3.1.

    Unary minus is only ever attached to an atom, and an atom never directly
    follows another prefix, so no `--x` is generated — §3.1 permits it but says
    nothing about it, and the oracle only asserts what the whitepaper states.
    """
    if depth <= 0 or rng.random() < 0.35:
        atom = rng.choice(_ATOMS)
        return "-" + atom if rng.random() < 0.25 else atom
    body = (
        _random_expression(rng, depth - 1)
        + " "
        + rng.choice(_BINOPS)
        + " "
        + _random_expression(rng, depth - 1)
    )
    return "(" + body + ")" if rng.random() < 0.5 else body


@_safe
def _agrees_with_python_arithmetic(evaluate: Any) -> bool:
    """§3 + §6.1 — differential against CPython's own expression grammar.

    CPython implements precisely the table of §3 for these operators, so any
    divergence over 400 generated expressions is a defect in the generated code.
    The generated module may not reach for `eval` (§12); this oracle may, and it
    is the sharpest available check that the precedence table was read right.
    """
    rng = random.Random(20260803)
    for _ in range(400):
        expression = _random_expression(rng, 3)
        try:
            # The oracle may take the shortcut §12 denies the generated code.
            want = eval(expression, {"__builtins__": {}}, {})
        except ZeroDivisionError:
            # §6.1 — the same input must be a MathError, not a bare raise.
            failure = _raised(evaluate, expression)
            if failure is None or not _exc_named(failure, "MathError"):
                return False
            continue
        got = evaluate(expression)
        if not _is_number(got):
            return False
        if not math.isclose(float(got), float(want), rel_tol=1e-9, abs_tol=1e-12):
            return False
    return True


@_safe
def _is_total_over_arbitrary_input(evaluate: Any) -> bool:
    """§9.4 — every string yields a number or an ExpressionError. Nothing else.

    500 random strings over an alphabet of operators, parens, digits, spaces,
    dots, identifier letters and one illegal character. A bare ValueError from
    `float()`, an IndexError from popping an empty stack, or a None return are
    all failures.
    """
    rng = random.Random(20260804)
    for _ in range(500):
        text = "".join(rng.choice(_FUZZ_ALPHABET) for _ in range(rng.randint(0, 7)))
        try:
            value = evaluate(text)
        except Exception as exc:  # noqa: BLE001 — the class is what is asserted
            if not _exc_named(exc, "ExpressionError"):
                return False
            continue
        if not _is_number(value):
            return False
    return True


# ── §8 / §10 / §12 shape of the algorithm ────────────────────────────────────


@_safe
def _deep_nesting_does_not_recurse(evaluate: Any) -> bool:
    """§10 + §12 — 500 nested parens must not raise RecursionError.

    A recursive-descent parser burns several frames per nesting level and blows
    the default 1000-frame limit long before it reaches 500.
    """
    return bool(
        evaluate("(" * 500 + "1" + ")" * 500) == 1
        and evaluate("(" * 500 + "2 + 3 * 4" + ")" * 500) == 14
        and evaluate("(" * 500 + "-3^2" + ")" * 500) == -9
    )


def _seconds(fn: Any, argument: Any) -> float:
    start = time.perf_counter()
    fn(argument)
    return time.perf_counter() - start


@_safe
def _scales_linearly_with_token_count(evaluate: Any) -> bool:
    """§8 — tokenizing, shunting and evaluation are each O(t).

    Eight times the input for at most twenty times the work: linear costs about
    8x, and the quadratic shapes that pass every other functional check here —
    re-slicing the source at each character, popping from the front of the token
    list, rebuilding the output queue per append — cost about 64x. The ratio is
    retried because a scheduler hiccup can inflate one measurement; a quadratic
    implementation fails all three attempts.
    """
    small = " + ".join(["1"] * 6000)
    big = " + ".join(["1"] * 48000)
    if evaluate(small) != 6000 or evaluate(big) != 48000:
        return False
    for _ in range(3):
        small_seconds = _seconds(evaluate, small)
        big_seconds = _seconds(evaluate, big)
        if big_seconds <= max(0.05, small_seconds * 20.0):
            return True
    return False


ORACLE = Oracle(
    whitepaper="03_expression_evaluator.md",
    package_hint="expr",
    required_names=[
        "tokenize",
        "to_rpn",
        "evaluate",
        "Token",
        "ExpressionError",
        "LexicalError",
        "SyntaxError_",
        "NameError_",
        "MathError",
    ],
    cases=[
        # §3 — precedence, worked through the table one row at a time.
        Case(
            target="evaluate",
            args=("1 + 2",),
            expected=3,
            description="§3 addition of two integers",
        ),
        Case(
            target="evaluate",
            args=("2 + 3 * 4",),
            expected=14,
            description="§3 multiplication binds tighter than addition",
        ),
        Case(
            target="evaluate",
            args=("(2 + 3) * 4",),
            expected=20,
            description="§5 parentheses override precedence",
        ),
        Case(
            target="evaluate",
            args=("2 - 3 - 4",),
            expected=-5,
            description="§3 `-` is left-associative (right-assoc gives 3)",
        ),
        Case(
            target="evaluate",
            args=("100 / 10 / 2",),
            expected=5.0,
            description="§3 `/` is left-associative (right-assoc gives 20)",
        ),
        # §3 — the headline case: `^` is right-associative.
        Case(
            target="evaluate",
            args=("2^3^2",),
            expected=512,
            description="§3 `^` is right-associative: 2^(3^2) = 512, not 64",
        ),
        Case(
            target="evaluate",
            args=("(2^3)^2",),
            expected=64,
            description="§5 explicit parens force the left grouping to 64",
        ),
        # §3.1 — unary minus disambiguation.
        Case(
            target="evaluate",
            args=("-3^2",),
            expected=-9,
            description="§3.1 `^` binds tighter than unary minus: -(3^2) = -9",
        ),
        Case(
            target="evaluate",
            args=("(-3)^2",),
            expected=9,
            description="§3.1 parenthesised negation is the base: (-3)^2 = 9",
        ),
        Case(
            target="evaluate",
            args=("-2^2",),
            expected=-4,
            description="§3.1 unary minus outside the power, second witness",
        ),
        Case(
            target="evaluate",
            args=("2 * -3",),
            expected=-6,
            description="§3.1 `-` after an operator is unary, not a syntax error",
        ),
        Case(
            target="evaluate",
            args=("2 * (3 - 5)",),
            expected=-4,
            description="§3 `-` after a value is binary",
        ),
        # §2.1 — number lexing.
        Case(
            target="evaluate",
            args=(".5 + 1",),
            expected=1.5,
            description="§2.1 a leading-dot literal is a number",
        ),
        Case(
            target="evaluate",
            args=("1e-3 * 1000",),
            expected=1.0,
            description="§2.1 signed exponents are part of the literal",
        ),
        Case(
            target="evaluate",
            args=("1 +\t2\n+ 3",),
            expected=6,
            description="§2 whitespace between tokens is not significant",
        ),
        # §4 / §11 — environment.
        Case(
            target="evaluate",
            args=("x * 2 + y",),
            kwargs={"variables": {"x": 5, "y": 1}},
            expected=11,
            description="§11 caller variables arrive through `variables=`",
        ),
        # Properties no single call reveals.
        Case(
            target="evaluate",
            call=False,
            check=_numeric_types_follow_the_spec,
            description="§6.1 `+ - *` stay integral and `/` always returns a float",
        ),
        Case(
            target="evaluate",
            call=False,
            check=_modulo_takes_the_sign_of_the_divisor,
            description="§6.1 `%` takes the sign of the divisor",
        ),
        Case(
            target="evaluate",
            call=False,
            check=_builtin_functions,
            description="§4 the seven built-in functions, variadic and 2-arg forms",
        ),
        Case(
            target="evaluate",
            call=False,
            check=_builtin_constants,
            description="§4 built-in constants pi and e",
        ),
        Case(
            target="evaluate",
            call=False,
            check=_variables_shadow_constants_but_not_functions,
            description="§4 variables shadow constants but not function names",
        ),
        Case(
            target="evaluate",
            call=False,
            check=_environments_are_read_only_and_do_not_leak,
            description="§11 the variables Mapping is read-only and not retained",
        ),
        Case(
            target="tokenize",
            call=False,
            check=_tokens_carry_kind_text_and_offset,
            description="§2 token kind, literal text and start offset",
        ),
        Case(
            target="to_rpn",
            call=False,
            check=_rpn_order_encodes_precedence,
            description="§5 RPN order encodes the §3 precedence and associativity",
        ),
        Case(
            target="to_rpn",
            call=False,
            check=_to_rpn_leaves_its_input_alone,
            description="§11 to_rpn does not consume the token sequence it is given",
        ),
        Case(
            target="to_rpn",
            call=False,
            check=_rpn_round_trips_through_evaluate,
            description="§9.5 evaluating to_rpn output reproduces evaluate",
        ),
        Case(
            target="evaluate",
            call=False,
            check=_error_offsets_are_exact,
            description="§9.3 every error offset indexes the offending character",
        ),
        Case(
            target="evaluate",
            call=False,
            check=_render_points_a_caret_at_the_offset,
            description="§7 render() shows the expression and a caret at the offset",
        ),
        Case(
            target="evaluate",
            call=False,
            check=_agrees_with_python_arithmetic,
            description="§3 differential against CPython over 400 expressions",
        ),
        Case(
            target="evaluate",
            call=False,
            check=_is_total_over_arbitrary_input,
            description="§9.4 totality: a number or an ExpressionError, never else",
        ),
        Case(
            target="evaluate",
            call=False,
            check=_deep_nesting_does_not_recurse,
            description="§10 500 nested parens evaluate without RecursionError",
        ),
        Case(
            target="evaluate",
            call=False,
            check=_scales_linearly_with_token_count,
            description="§8 cost is linear in token count, not quadratic",
        ),
    ],
    error_cases=[
        # §2 / §7 — lexical.
        ErrorCase(
            target="tokenize",
            args=("1 # 2",),
            exc_name="LexicalError",
            description="§2 an illegal character is a lexical error",
        ),
        ErrorCase(
            target="tokenize",
            args=("1 . 2",),
            exc_name="LexicalError",
            description="§2.1 a bare '.' is a lexical error",
        ),
        ErrorCase(
            target="evaluate",
            args=("1 + $",),
            exc_name="LexicalError",
            description="§2 evaluate propagates the lexical error",
        ),
        # §10 — empty input.
        ErrorCase(
            target="evaluate",
            args=("",),
            exc_name="SyntaxError_",
            match="empty",
            description="§10 the empty string is a syntax error",
        ),
        ErrorCase(
            target="evaluate",
            args=("   ",),
            exc_name="SyntaxError_",
            match="empty",
            description="§10 whitespace only is a syntax error",
        ),
        # §5 / §10 — structure.
        ErrorCase(
            target="evaluate",
            args=("1 + 2)",),
            exc_name="SyntaxError_",
            description="§5.6 an unmatched ')' is a syntax error",
        ),
        ErrorCase(
            target="evaluate",
            args=("(1 + 2",),
            exc_name="SyntaxError_",
            description="§5 an unclosed '(' is a syntax error",
        ),
        ErrorCase(
            target="evaluate",
            args=("()",),
            exc_name="SyntaxError_",
            description="§6 empty parens leave no value on the stack",
        ),
        ErrorCase(
            target="evaluate",
            args=("1 * / 2",),
            exc_name="SyntaxError_",
            description="§10 two consecutive operators",
        ),
        ErrorCase(
            target="evaluate",
            args=("1 +",),
            exc_name="SyntaxError_",
            description="§10 a trailing operator has no right operand",
        ),
        ErrorCase(
            target="evaluate",
            args=("1 2",),
            exc_name="SyntaxError_",
            description="§10 a missing operator between two operands",
        ),
        ErrorCase(
            target="evaluate",
            args=("2(3)",),
            exc_name="SyntaxError_",
            description="§10 juxtaposition is not implicit multiplication",
        ),
        ErrorCase(
            target="evaluate",
            args=("2pi",),
            exc_name="SyntaxError_",
            description="§10 a number adjacent to an identifier is a syntax error",
        ),
        ErrorCase(
            target="evaluate",
            args=("1, 2",),
            exc_name="SyntaxError_",
            description="§5.3 a comma outside an argument list",
        ),
        # §4 — arity.
        ErrorCase(
            target="evaluate",
            args=("min(1)",),
            exc_name="SyntaxError_",
            match="min",
            description="§4 min needs two or more arguments, and is named",
        ),
        ErrorCase(
            target="evaluate",
            args=("sqrt(1, 2)",),
            exc_name="SyntaxError_",
            description="§4 sqrt takes exactly one argument",
        ),
        ErrorCase(
            target="evaluate",
            args=("round(1, 2, 3)",),
            exc_name="SyntaxError_",
            description="§4 round takes one or two arguments",
        ),
        # §4 — names.
        ErrorCase(
            target="evaluate",
            args=("2 + foo",),
            exc_name="NameError_",
            description="§4 an unknown identifier is a name error",
        ),
        ErrorCase(
            target="evaluate",
            args=("x + 1",),
            exc_name="NameError_",
            description="§4 a variable absent from the environment is a name error",
        ),
        ErrorCase(
            target="evaluate",
            args=("bogus(1)",),
            exc_name="NameError_",
            description="§4 an unknown function name is a name error",
        ),
        # §6.1 — arithmetic domain.
        ErrorCase(
            target="evaluate",
            args=("1/0",),
            exc_name="MathError",
            description="§6.1 division by zero",
        ),
        ErrorCase(
            target="evaluate",
            args=("0/0",),
            exc_name="MathError",
            description="§10 0/0 is a division-by-zero error, not a nan",
        ),
        ErrorCase(
            target="evaluate",
            args=("5 % 0",),
            exc_name="MathError",
            description="§6.1 modulo by zero",
        ),
        ErrorCase(
            target="evaluate",
            args=("(-8)^(1/3)",),
            exc_name="MathError",
            description="§6.1 a negative base with a fractional exponent is a domain error",
        ),
    ],
    prohibitions=[
        Prohibition(
            reason=(
                "§12 forbids eval, ast.literal_eval and the ast module — the "
                "explicit tokenizer and shunting-yard parser are the deliverable, "
                "and handing the string to Python would pass every functional "
                "check while implementing nothing"
            ),
            imports=("ast",),
            name_calls=("eval", "exec", "compile", "literal_eval"),
            attr_calls=("literal_eval",),
        ),
    ],
)
