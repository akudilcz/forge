# Arithmetic Expression Tokenizer and Shunting-Yard Evaluator

Python Library Specification

## Abstract

This document specifies a Python library that parses and evaluates infix
arithmetic expressions supplied as text. Parsing proceeds in two stages: a
hand-written tokenizer converts the input string into a token stream, and
Dijkstra's shunting-yard algorithm converts infix tokens into reverse Polish
notation (RPN), which is then evaluated on a stack. The library reports precise,
positioned errors for malformed input rather than raising bare exceptions.

## 1. Overview and Design Rationale

Evaluating arithmetic text has three failure surfaces: lexical (an illegal
character), syntactic (unbalanced parentheses, a missing operand), and semantic
(division by zero). A single-pass recursive-descent parser tends to conflate
these. Separating tokenization from parsing, and parsing from evaluation, lets
each stage report exactly the class of error it is responsible for, with the
character offset at which it occurred.

The shunting-yard algorithm is chosen over recursive descent because operator
precedence and associativity become table-driven data rather than control flow,
which makes adding an operator a one-line change.

## 2. Lexical Grammar

The tokenizer recognises these token kinds:

| Kind | Pattern | Notes |
|---|---|---|
| `NUMBER` | integer or decimal, optional exponent | `12`, `3.5`, `1e-3`, `.5` |
| `OPERATOR` | one of `+ - * / % ^` | `^` is exponentiation |
| `LPAREN` | `(` | |
| `RPAREN` | `)` | |
| `IDENT` | letter followed by letters/digits/underscore | function or constant name |
| `COMMA` | `,` | argument separator |

Whitespace between tokens is skipped and is not significant. Any other character
is a lexical error.

Every token carries its kind, its literal text, and its start offset in the input
string. The offset is required for error reporting and must be the index of the
token's first character.

### 2.1 Number Parsing

A `NUMBER` matches: optional digits, optional `.` followed by digits, optional
exponent marker `e`/`E` with optional sign and one or more digits. At least one
digit must be present overall — a bare `.` is a lexical error. Numbers are
converted with `float`, except that a token with no `.` and no exponent is
converted with `int` so that integer arithmetic stays exact.

## 3. Operators

| Operator | Precedence | Associativity | Arity |
|---|---|---|---|
| `+` `-` (binary) | 1 | left | 2 |
| `*` `/` `%` | 2 | left | 2 |
| unary `-`, unary `+` | 3 | right | 1 |
| `^` | 4 | right | 2 |

Right associativity of `^` means `2^3^2` evaluates as `2^(3^2)` = 512, not
`(2^3)^2` = 64. This is a required test case.

### 3.1 Unary Minus Disambiguation

A `-` is unary when it appears at the start of the expression, or immediately
after another operator, a `(`, or a `,`. Otherwise it is binary. The tokenizer
does not decide this; the parser does, based on the previous significant token.
`-3^2` must evaluate to `-9`, because `^` binds tighter than unary minus.

## 4. Functions and Constants

Built-in functions: `abs`, `min`, `max`, `sqrt`, `floor`, `ceil`, `round`.
Built-in constants: `pi`, `e`.

- `min` and `max` accept two or more arguments; the others accept exactly one,
  except `round` which accepts one or two.
- Calling a function with the wrong number of arguments is a syntax error naming
  the function and both the expected and actual counts.
- An unknown identifier is a name error carrying the offending name and offset.

Callers may supply additional variables through an environment mapping passed to
`evaluate`. Variables shadow constants but not function names.

## 5. Shunting-Yard Algorithm

Maintain an output queue and an operator stack.

For each token:

1. `NUMBER` or variable → append to output.
2. `IDENT` naming a function → push to operator stack.
3. `COMMA` → pop operators to output until `LPAREN` is at the stack top. A
   missing `LPAREN` means a comma outside any argument list: syntax error.
4. Operator o1 → while an operator o2 is on top with either greater precedence,
   or equal precedence and o1 left-associative, pop o2 to output. Then push o1.
5. `LPAREN` → push.
6. `RPAREN` → pop to output until `LPAREN`; discard the `LPAREN`; if the new top
   is a function, pop it to output. A stack exhausted before finding `LPAREN`
   means unbalanced parentheses: syntax error at the `RPAREN` offset.

At end of input, pop the remaining stack to output. Encountering a `LPAREN`
during this final drain means an unclosed parenthesis: syntax error at that
paren's offset.

## 6. RPN Evaluation

Walk the output queue pushing operands onto a value stack. On an operator, pop
its arity, apply, push the result. At the end the stack must contain exactly one
value; anything else is a syntax error ("malformed expression").

### 6.1 Arithmetic Semantics

- `/` performs true division and returns a float.
- `%` follows Python's sign convention: the result takes the sign of the divisor.
- `^` with a negative base and fractional exponent raises a domain error rather
  than returning a complex number.
- Division or modulo by zero raises a division-by-zero error carrying the offset
  of the operator token.
- Integer inputs to `+ - *` produce integer results; any float operand makes the
  result a float.

## 7. Error Model

All errors derive from a common `ExpressionError` base carrying `message`,
`offset`, and the original `expression`. Subclasses:

- `LexicalError` — illegal character.
- `SyntaxError_` — structural problem (unbalanced parens, missing operand,
  stray comma, wrong argument count).
- `NameError_` — unknown identifier.
- `MathError` — division by zero, domain error.

Every error must render a human-readable string that includes the expression and
a caret line pointing at `offset`:

```
1 + * 2
    ^ unexpected operator '*'
```

## 8. Complexity

Tokenizing is O(n) in input length. Shunting-yard is O(t) in token count, since
each token is pushed and popped at most once. Evaluation is O(t). Total space is
O(t) for the output queue and stacks.

## 9. Correctness Properties

1. **Precedence** — evaluation respects the table in §3 for all operator pairs.
2. **Associativity** — `^` is right-associative; the four arithmetic operators
   are left-associative.
3. **Offset accuracy** — every reported error offset indexes the exact character
   that caused it.
4. **Totality** — for any input string, `evaluate` either returns a number or
   raises an `ExpressionError`; it never raises an unwrapped built-in exception
   and never returns None.
5. **Round-trip** — `to_rpn` output, when evaluated, equals `evaluate` output.

## 10. Failure Modes and Edge Cases

- Empty string, or whitespace only: syntax error "empty expression".
- Unbalanced `(` and unbalanced `)` — both directions, each reported at the
  correct offset.
- Two consecutive operators, e.g. `1 * / 2`.
- Trailing operator, e.g. `1 +`.
- Missing operator between operands, e.g. `1 2` or `2(3)`.
- Very deep nesting, e.g. 500 nested parens, must not raise `RecursionError` —
  the algorithm is iterative and this must remain true.
- `0/0`, `1/0`, `5 % 0` — all division-by-zero errors.
- `(-8)^(1/3)` — domain error, not a complex number.
- Numbers immediately adjacent to identifiers, e.g. `2pi`, is a syntax error.

## 11. Public API

```python
def tokenize(expression: str) -> list[Token]:
    """Convert text to tokens. Raises LexicalError."""

def to_rpn(tokens: Sequence[Token]) -> list[Token]:
    """Convert infix tokens to RPN. Raises SyntaxError_."""

def evaluate(
    expression: str,
    variables: Mapping[str, float] | None = None,
) -> int | float:
    """Tokenize, parse, and evaluate. Raises ExpressionError subclasses."""

class Token:
    kind: str
    text: str
    offset: int

class ExpressionError(Exception):
    message: str
    offset: int
    expression: str
    def render(self) -> str: ...
```

## 12. Implementation Notes

- Do not use `eval`, `ast.literal_eval`, or the `ast` module. The point of the
  library is the explicit tokenizer and parser.
- The operator table should be a module-level mapping so that precedence is data,
  not branching.
- Evaluation must be iterative, not recursive, to satisfy the deep-nesting case.
