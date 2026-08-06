"""Misplaced-obligation detection — DESIGN text vs CONTRACT records.

The dividing rule (specs/13): anything expressible as a precondition,
postcondition, ``raises`` obligation, or invariant is CONTRACT material;
DESIGN holds only private structure and algorithm choice. A DESIGN line
that asserts observable behaviour for a *public* symbol — behaviour the
CONTRACT's ``public_api`` record does not carry — is therefore a
contract violation: the obligation must move into the contract record,
or the DESIGN must be aligned.

Matching is deliberately conservative. Only two confident patterns are
recognised — ``raises <ExceptionIdentifier>`` and ``returns None`` — on
a line that mentions exactly one public callable as a ``name(...)``
token. Prose ("raises the question of…", lowercase identifiers,
ambiguous multi-symbol lines) never matches.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

#: A public callable mentioned as ``name(`` — same style rule as the
#: signature validator (no space between name and paren).
_CALLABLE_MENTION_RE = re.compile(r"(?<![.\w])([A-Za-z_][A-Za-z0-9_]*)\(")

#: ``raises CamelCaseException`` — optional article and backticks; the
#: identifier is further filtered by ``_is_exception_name`` so only real
#: exception class names match, never capitalised prose words.
_RAISES_RE = re.compile(r"\braises\s+(?:an?\s+)?`?([A-Z][A-Za-z0-9_]*)`?")

#: Builtin exception names that do not end in Error/Exception.
_NON_SUFFIX_EXCEPTIONS: frozenset[str] = frozenset({
    "StopIteration", "StopAsyncIteration", "KeyboardInterrupt",
    "SystemExit", "GeneratorExit", "Warning", "DeprecationWarning",
})


def _is_exception_name(name: str) -> bool:
    """Confident exception-class identifier — Error/Exception suffix or a
    known builtin. Anything else is treated as capitalised prose."""
    return name.endswith(("Error", "Exception")) or name in _NON_SUFFIX_EXCEPTIONS

#: ``returns None`` — exact-value return contract.
_RETURNS_NONE_RE = re.compile(r"\breturns\s+`?None`?\b")

#: public_api kinds that carry callable obligations.
_CALLABLE_KINDS: frozenset[str] = frozenset({"function", "method"})


@dataclass(frozen=True)
class MisplacedObligation:
    """One DESIGN-asserted obligation absent from the CONTRACT record."""

    symbol: str      # public_api symbol the line attributes behaviour to
    obligation: str  # e.g. "raises CyclicGraphError" / "returns None"
    excerpt: str     # the DESIGN line carrying the assertion


def find_misplaced_obligations(
    public_api: Sequence[object],
    design_content: str,
) -> list[MisplacedObligation]:
    """Confidently-attributable DESIGN obligations missing from the contract.

    Entry shape is guaranteed by the write-time invariant
    (``check_contract_public_api``) — malformed entries raise loudly here
    rather than being skipped silently.
    """
    callables = _callable_entries(public_api)
    if not callables:
        return []
    hits: list[MisplacedObligation] = []
    for raw_line in design_content.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        mentioned = {
            name for name in _CALLABLE_MENTION_RE.findall(line)
            if name.lower() in callables
        }
        if len(mentioned) != 1:
            continue  # zero: not about the public surface; >1: ambiguous
        symbol_key = mentioned.pop().lower()
        entry = callables[symbol_key]
        hits.extend(_line_obligation_hits(line, symbol_key, entry))
    return hits


def _callable_entries(public_api: Sequence[object]) -> dict[str, dict[str, object]]:
    """Map short lower-cased symbol name → its function/method entry."""
    callables: dict[str, dict[str, object]] = {}
    for entry in public_api:
        if not isinstance(entry, dict) or entry["kind"] not in _CALLABLE_KINDS:
            continue
        short = str(entry["symbol"]).rsplit(".", 1)[-1].lower()
        callables[short] = entry
    return callables


def _line_obligation_hits(
    line: str,
    symbol_key: str,
    entry: dict[str, object],
) -> list[MisplacedObligation]:
    """Confident obligations this line asserts that ``entry`` lacks."""
    symbol = str(entry["symbol"])
    hits: list[MisplacedObligation] = []
    for m in _RAISES_RE.finditer(line):
        cls = m.group(1)
        if _is_exception_name(cls) and not _contract_declares_raise(entry, cls):
            hits.append(MisplacedObligation(symbol, f"raises {cls}", line))
    if _RETURNS_NONE_RE.search(line) and not _contract_allows_none_return(entry):
        hits.append(MisplacedObligation(symbol, "returns None", line))
    return hits


def _contract_declares_raise(entry: dict[str, object], cls: str) -> bool:
    """True when the entry's ``raises`` list names exception class ``cls``."""
    if "raises" not in entry:
        return False
    raises = entry["raises"]
    if not isinstance(raises, list):
        return False
    return any(isinstance(rec, dict) and rec.get("cls") == cls for rec in raises)


def _contract_allows_none_return(entry: dict[str, object]) -> bool:
    """True when the signature or a postcondition already states None."""
    if "None" in str(entry["signature"]):
        return True
    if "postconditions" not in entry:
        return False
    posts = entry["postconditions"]
    if not isinstance(posts, list):
        return False
    return any("None" in str(post) for post in posts)
