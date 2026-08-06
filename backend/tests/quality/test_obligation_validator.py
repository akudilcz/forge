"""Tests for backend.quality.obligation_validator — misplaced obligations.

A DESIGN line that asserts observable behaviour ("raises X" / "returns
None") for a public symbol is contract material. When the CONTRACT's
``public_api`` record for that symbol lacks the obligation, the detector
flags it so the obligation is moved into the contract (or the DESIGN
aligned). Matching is deliberately conservative — only confident
patterns, never prose guesses.
"""

from __future__ import annotations

from backend.quality.obligation_validator import find_misplaced_obligations


def _entry(**extra: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "module": "toposort",
        "symbol": "find_cycle",
        "kind": "function",
        "signature": "def find_cycle(graph: Mapping) -> list",
    }
    entry.update(extra)
    return entry


# ── Confident hits ──────────────────────────────────────────────────────────


def test_design_raises_missing_from_contract_flagged() -> None:
    design = (
        "find_cycle(graph) raises CyclicGraphError when the graph is cyclic."
    )
    hits = find_misplaced_obligations([_entry()], design)
    assert len(hits) == 1
    assert hits[0].symbol == "find_cycle"
    assert hits[0].obligation == "raises CyclicGraphError"


def test_design_returns_none_missing_from_contract_flagged() -> None:
    design = "find_cycle(graph) returns None when the graph is acyclic."
    hits = find_misplaced_obligations([_entry()], design)
    assert len(hits) == 1
    assert hits[0].obligation == "returns None"


# ── Contract already carries the obligation → no hit ────────────────────────


def test_raises_present_in_contract_record_not_flagged() -> None:
    entry = _entry(raises=[{
        "cls": "CyclicGraphError", "base": "ValueError",
        "when": "the graph is cyclic",
    }])
    design = "find_cycle(graph) raises CyclicGraphError when cyclic."
    assert find_misplaced_obligations([entry], design) == []


def test_returns_none_in_signature_not_flagged() -> None:
    entry = _entry(signature="def find_cycle(graph) -> list | None")
    design = "find_cycle(graph) returns None when the graph is acyclic."
    assert find_misplaced_obligations([entry], design) == []


def test_returns_none_in_postcondition_not_flagged() -> None:
    entry = _entry(postconditions=["returns None when the graph is acyclic"])
    design = "find_cycle(graph) returns None when the graph is acyclic."
    assert find_misplaced_obligations([entry], design) == []


# ── Conservative non-hits ───────────────────────────────────────────────────


def test_prose_raises_without_identifier_not_flagged() -> None:
    design = (
        "The choice of find_cycle(graph) raises the question of performance, "
        "and the recursion raises stack depth concerns."
    )
    assert find_misplaced_obligations([_entry()], design) == []


def test_line_without_public_symbol_not_flagged() -> None:
    design = "Internal helper _walk(node) raises CyclicGraphError on cycles."
    assert find_misplaced_obligations([_entry()], design) == []


def test_line_with_two_public_symbols_not_flagged() -> None:
    """Ambiguous attribution: two public symbols on one line — skip."""
    entries = [
        _entry(),
        _entry(symbol="is_acyclic", signature="def is_acyclic(graph) -> bool"),
    ]
    design = (
        "Both find_cycle(graph) and is_acyclic(graph) raises "
        "CyclicGraphError on malformed input."
    )
    assert find_misplaced_obligations(entries, design) == []


def test_non_callable_entries_ignored() -> None:
    """Class entries carry no callable obligations to compare against."""
    entry = _entry(symbol="Graph", kind="class", signature="class Graph")
    design = "Graph(nodes) raises CyclicGraphError on cycles."
    assert find_misplaced_obligations([entry], design) == []
