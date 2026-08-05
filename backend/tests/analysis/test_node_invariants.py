"""Behavioural tests for the shared authoring-invariant checks.

``backend/analysis/node_invariants.py`` is the single source of truth used
by BOTH the graph-write tools (write-time rejection) and the Gap Analyser
(backstop detection). Each check is a pure function returning ``None`` when
the invariant holds, or an actionable message when it does not.
"""

from __future__ import annotations

from backend.analysis.node_invariants import (
    MIN_CONTENT_LENGTH,
    check_case_trace_targets,
    check_min_content_length,
    check_requirement_wording,
    check_sibling_content_unique,
    check_sibling_title_unique,
    check_title,
    normalise_content,
    normalise_title,
)
from backend.graph.models import GraphNode


def _n(nid: str, ntype: str, **kw: object) -> GraphNode:
    return GraphNode(node_id=nid, node_type=ntype, **kw)  # type: ignore[arg-type]


# ── check_title ───────────────────────────────────────────────────────────────


def test_title_ok() -> None:
    assert check_title("HLR", "Parse CSV Rows") is None


def test_title_missing_rejected_with_actionable_message() -> None:
    msg = check_title("HLR", "  ")
    assert msg is not None
    assert "title" in msg
    assert "3-5 words" in msg


def test_title_too_long_rejected() -> None:
    msg = check_title("MODULE", "one two three four five six seven eight")
    assert msg is not None
    assert "too long" in msg


def test_title_exempt_types_skip() -> None:
    for ntype in ("PROJECT", "DOCUMENT", "RESULT", "RECORD"):
        assert check_title(ntype, "") is None


# ── check_requirement_wording ────────────────────────────────────────────────


def test_wording_ok() -> None:
    assert check_requirement_wording("HLR", "The system shall parse CSV.") is None


def test_wording_bad_prefix_rejected() -> None:
    msg = check_requirement_wording("LLR", "Parses CSV files.")
    assert msg is not None
    assert "The system shall" in msg


def test_wording_para_placeholder_rejected() -> None:
    msg = check_requirement_wording("HLR", "The system shall PARA-0012.")
    assert msg is not None
    assert "PARA-0012" in msg


def test_wording_non_requirement_types_skip() -> None:
    assert check_requirement_wording("MODULE", "whatever") is None


def test_wording_empty_content_skips() -> None:
    # EMPTY_CONTENT is a separate concern; wording only fires on real text.
    assert check_requirement_wording("HLR", "   ") is None


# ── check_min_content_length ─────────────────────────────────────────────────


def test_min_content_length_ok() -> None:
    assert check_min_content_length("DESIGN", "x" * MIN_CONTENT_LENGTH) is None


def test_min_content_length_rejected() -> None:
    msg = check_min_content_length("CONTRACT", "too short")
    assert msg is not None
    assert str(MIN_CONTENT_LENGTH) in msg


def test_min_content_length_empty_skips() -> None:
    assert check_min_content_length("DESIGN", "") is None


def test_min_content_length_untracked_type_skips() -> None:
    assert check_min_content_length("HLR", "short") is None


# ── sibling uniqueness ───────────────────────────────────────────────────────


def test_sibling_title_unique_ok() -> None:
    sibs = [_n("HLR-0001", "HLR", title="Parse Rows")]
    assert check_sibling_title_unique("HLR", "Emit Totals", "HLR-0002", sibs) is None


def test_sibling_title_duplicate_rejected_case_insensitive() -> None:
    sibs = [_n("HLR-0001", "HLR", title="Parse Rows")]
    msg = check_sibling_title_unique("HLR", "  parse rows ", "HLR-0002", sibs)
    assert msg is not None
    assert "HLR-0001" in msg
    assert "distinct" in msg


def test_sibling_title_ignores_self() -> None:
    sibs = [_n("HLR-0002", "HLR", title="Parse Rows")]
    assert check_sibling_title_unique("HLR", "Parse Rows", "HLR-0002", sibs) is None


def test_sibling_content_duplicate_rejected() -> None:
    sibs = [_n("LLR-0001", "LLR", content="The system shall X.")]
    msg = check_sibling_content_unique(
        "LLR", " the system shall x. ", "LLR-0002", sibs
    )
    assert msg is not None
    assert "LLR-0001" in msg


def test_sibling_content_different_type_not_compared() -> None:
    sibs = [_n("HLR-0001", "HLR", content="Same words here.")]
    assert (
        check_sibling_content_unique("LLR", "Same words here.", "LLR-0002", sibs)
        is None
    )


def test_sibling_content_empty_skips() -> None:
    sibs = [_n("LLR-0001", "LLR", content="")]
    assert check_sibling_content_unique("LLR", "", "LLR-0002", sibs) is None


# ── CASE trace_to membership ─────────────────────────────────────────────────


def _resolver(nodes: dict[str, GraphNode]) -> object:
    return lambda nid: nodes.get(nid)


def test_case_trace_ok() -> None:
    nodes = {"HLR-0001": _n("HLR-0001", "HLR")}
    assert (
        check_case_trace_targets("CASE_HLR", ["HLR-0001"], _resolver(nodes))  # type: ignore[arg-type]
        is None
    )


def test_case_trace_empty_rejected() -> None:
    msg = check_case_trace_targets("CASE_HLR", [], _resolver({}))  # type: ignore[arg-type]
    assert msg is not None
    assert "HLR" in msg


def test_case_trace_wrong_type_rejected() -> None:
    nodes = {"SUITE-0001": _n("SUITE-0001", "SUITE")}
    msg = check_case_trace_targets("CASE_LLR", ["SUITE-0001"], _resolver(nodes))  # type: ignore[arg-type]
    assert msg is not None
    assert "SUITE-0001" in msg
    assert "LLR" in msg


def test_case_trace_unresolvable_refs_not_type_checked() -> None:
    # Missing targets are STALE_TRACE_TO territory, not a type violation.
    assert (
        check_case_trace_targets("CASE_HLR", ["HLR-9999"], _resolver({}))  # type: ignore[arg-type]
        is None
    )


def test_case_trace_non_case_types_skip() -> None:
    assert check_case_trace_targets("MODULE", [], _resolver({})) is None  # type: ignore[arg-type]


# ── normalisation helpers shared with the analyser ───────────────────────────


def test_normalisers() -> None:
    assert normalise_title("  Foo Bar ") == "foo bar"
    assert normalise_content(" X\n") == "x"
