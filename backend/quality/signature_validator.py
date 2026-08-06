"""CONTRACT↔DESIGN signature alignment.

Two checking regimes, selected by what the CONTRACT provides:

**Structured** (``find_public_api_conflicts``): when the CONTRACT carries
``properties.public_api`` (specs/13), a DESIGN violates the contract
**only** when it declares an annotated signature reusing a public
function's name and none of its declarations for that name agrees with
the ``public_api`` signature. Agreement is deliberately forgiving —
parameter-NAME sequence must match, and return types are compared only
when both sides state one — because type paraphrase in prose designs is
routine and type drift is owned by the phase-12 API-surface gate.
Internal helpers (any name ``public_api`` does not list) are NEVER
violations: DESIGNs legitimately specify private classes and methods the
CONTRACT never mentions (live evidence: topological_sort r3, six false
CONTRACT_VIOLATION gaps on internal accessors like ``in_degree`` /
``decompose``).

**Legacy fallback** (``find_design_contract_mismatches``): contracts
authored before specs/13 have prose only, so we extract function-like
identifier tokens (word followed by an opening paren) from both sides
and flag DESIGN tokens the CONTRACT text never mentions.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

# Matches a Python-identifier-like token immediately followed by an open
# paren — no whitespace between name and paren, per PEP 8 signature style.
# A spaced paren ("thereafter (using …)") is an English parenthetical, not a
# declaration; live builds flagged such prose words as contract mismatches.
# Excludes leading dot (method calls). Dunders (__init__ etc.) are excluded
# below: CONTRACTs describe the public surface and never list constructors.
_FN_TOKEN_RE = re.compile(r"(?<![.\w])([a-z_][a-z0-9_]{1,})\(")

_STOPWORDS: frozenset[str] = frozenset({
    # Python keywords / builtins that take parens and appear in prose
    "if", "for", "while", "return", "yield", "print",
    "len", "range", "isinstance", "issubclass", "type", "str", "int",
    "float", "bool", "list", "dict", "set", "tuple", "bytes",
    "open", "super", "enumerate", "zip", "map", "filter", "any", "all",
    "min", "max", "sum", "sorted", "reversed", "repr", "hash",
    # Our own graph / agent-tool names that may appear as examples
    "graph_read", "graph_add_node", "graph_add_traces", "graph_reparent_node",
    "graph_update_node", "graph_delete_node", "graph_update_trace",
    "derive_requirement", "check_consistency", "file_write", "file_read",
    "run_tests", "read_file", "write_file",
    # Misc words that end with "("-like pattern in docstrings
    "e_g", "i_e",
})


def extract_function_names(markdown: str) -> set[str]:
    """Return the set of function-like identifier tokens found in ``markdown``.

    The tokens are returned in lower-case. Stopwords (Python builtins, graph
    tools) are excluded so the signal is dominated by domain-specific
    function names declared by the author.
    """
    if not markdown:
        return set()
    return {
        token.lower()
        for token in _FN_TOKEN_RE.findall(markdown)
        if token.lower() not in _STOPWORDS
        and not token.startswith("_")
    }


def find_design_contract_mismatches(
    contract_content: str,
    design_content: str,
) -> list[str]:
    """Return DESIGN function names missing from the CONTRACT.

    Empty inputs produce an empty list (nothing to validate yet).
    """
    design_fns = extract_function_names(design_content)
    if not design_fns:
        return []
    contract_fns = extract_function_names(contract_content)
    if not contract_fns:
        # CONTRACT empty or prose-only — cannot validate; surface the whole set
        # so the auditor can decide. Callers may treat an empty contract as
        # a separate EMPTY_CONTENT gap.
        return sorted(design_fns)
    return sorted(design_fns - contract_fns)


# ── Structured public_api checking (specs/13) ───────────────────────────────

#: A declared signature: parameter names in order, return type or None.
SignatureShape = tuple[list[str], str | None]

_ARROW_RE = re.compile(r"\s*->\s*")
#: Characters that may appear in a return-type expression at bracket depth 0.
_RET_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_[]|. "
)


def _scan_close_paren(text: str, open_paren: int) -> int | None:
    """Index of the paren closing ``text[open_paren]``, or None."""
    depth = 0
    for i in range(open_paren, min(len(text), open_paren + 2000)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    return None


def _scan_return_type(text: str, pos: int) -> str | None:
    """Return-type expression after ``-> `` at ``pos``, or None.

    Bracket-aware so ``dict[str, int]`` survives, while a top-level comma
    or any prose character (em-dash, colon, ``(``) terminates the type.
    """
    m = _ARROW_RE.match(text, pos)
    if not m:
        return None
    out: list[str] = []
    depth = 0
    for i in range(m.end(), min(len(text), m.end() + 300)):
        ch = text[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        elif depth == 0:
            if ch == "," or ch not in _RET_CHARS:
                break
            # A depth-0 space continues the type only inside a `X | Y`
            # union — otherwise it is the start of trailing prose
            # ("-> set[Any] walks adjacency").
            if ch == " " and not _continues_union(text, out, i):
                break
        out.append(ch)
    ret = "".join(out).strip().rstrip(".,;")
    return ret or None


def _continues_union(text: str, out: list[str], i: int) -> bool:
    """True when the space at ``text[i]`` sits inside a ``X | Y`` union."""
    prev = "".join(out).rstrip()
    if prev.endswith("|"):
        return True
    rest = text[i:i + 40].lstrip()
    return rest.startswith("|")


def _split_top_level(params: str) -> list[str]:
    """Split a parameter list on commas outside brackets/parens."""
    parts: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in params:
        if ch in "[(":
            depth += 1
        elif ch in "])":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _param_names(params: str) -> list[str]:
    """Ordered parameter names, dropping self/cls and bare ``*`` / ``/``."""
    names: list[str] = []
    for part in _split_top_level(params):
        name = re.split(r"[:=]", part, maxsplit=1)[0].strip().lstrip("*").strip()
        if name in ("", "self", "cls", "/"):
            continue
        names.append(name)
    return names


def _canonical_return(ret: str) -> str:
    return re.sub(r"\s+", "", ret)


def parse_signature(signature: str) -> tuple[str, list[str], str | None] | None:
    """Parse ``[def ]name(params)[ -> ret]`` into (name, param names, ret).

    Returns None for non-callable signatures (e.g. an attribute entry like
    ``cycle: list[Any]``).
    """
    text = signature.strip()
    if text.startswith("def "):
        text = text[4:]
    m = _FN_TOKEN_RE.search(text)
    if not m:
        return None
    close = _scan_close_paren(text, m.end() - 1)
    if close is None:
        return None
    return (
        m.group(1).lower(),
        _param_names(text[m.end():close]),
        _scan_return_type(text, close + 1),
    )


def extract_signature_declarations(markdown: str) -> dict[str, list[SignatureShape]]:
    """Map declared function name → its declared signature shapes.

    A *declaration* is a ``name(`` token whose parameter list carries at
    least one annotation (``:``). Shorthand mentions like
    ``is_acyclic(graph) -> bool`` are prose references, not declarations,
    and are ignored — live designs restate public wrappers this way.
    """
    decls: dict[str, list[SignatureShape]] = {}
    for m in _FN_TOKEN_RE.finditer(markdown):
        name = m.group(1).lower()
        close = _scan_close_paren(markdown, m.end() - 1)
        if close is None:
            continue
        params = markdown[m.end():close]
        if ":" not in params:
            continue
        decls.setdefault(name, []).append(
            (_param_names(params), _scan_return_type(markdown, close + 1))
        )
    return decls


def _shapes_agree(declared: SignatureShape, public: SignatureShape) -> bool:
    """Forgiving agreement: same param names; returns compared when both given."""
    d_params, d_ret = declared
    p_params, p_ret = public
    if d_params != p_params:
        return False
    if d_ret is None or p_ret is None:
        return True
    return _canonical_return(d_ret) == _canonical_return(p_ret)


def find_public_api_conflicts(
    public_api: Sequence[object],
    design_content: str,
) -> list[str]:
    """Public function names whose DESIGN declarations contradict public_api.

    A name conflicts iff the DESIGN declares one or more annotated
    signatures for a ``kind == "function"`` public_api symbol and NONE of
    them agrees with the contract shape (an internal method may shadow a
    public name as long as the DESIGN also states the public form —
    live case: Reachability.descendants vs the descendants() wrapper).
    Names public_api does not list are internal helpers, never conflicts.
    """
    functions: dict[str, tuple[list[str], str | None]] = {}
    for entry in public_api:
        # Entry shape is guaranteed by the write-time invariant
        # (check_contract_public_api) — a malformed entry raises loudly here
        # rather than being skipped silently.
        if not isinstance(entry, dict) or entry["kind"] != "function":
            continue
        parsed = parse_signature(str(entry["signature"]))
        if parsed is None:
            continue
        _name, params, ret = parsed
        functions[str(entry["symbol"]).lower().rsplit(".", 1)[-1]] = (params, ret)
    conflicts: list[str] = []
    for name, declared in extract_signature_declarations(design_content).items():
        if name not in functions:
            continue
        if not any(_shapes_agree(shape, functions[name]) for shape in declared):
            conflicts.append(name)
    return sorted(conflicts)
