"""CONTRACT↔DESIGN signature alignment.

A DESIGN is a class spec written by an LLM in markdown — method signatures
may appear as ``def foo(x: int) -> User`` or in prose as ``foo(x) -> User``.
The same is true of CONTRACTs. We normalise by extracting **function-like
identifier tokens** (word followed by an opening paren) and comparing sets.

This is a deliberately forgiving check:
  * It won't flag signature style differences (return type annotations,
    default args, keyword-only markers).
  * It WILL flag a DESIGN that declares a function name the CONTRACT has
    never mentioned — the most common source of Phase-13 CONTRACT_VIOLATION.

False positives are preferable to false negatives here: the Quality Auditor
can always reconcile, but drift that slips past the validator ends up in
generated code where it causes real test failures.
"""

from __future__ import annotations

import re

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
