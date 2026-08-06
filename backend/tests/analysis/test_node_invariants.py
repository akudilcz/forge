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
    check_title_distinct_from_parent,
    classify_ears,
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


# ── classify_ears (Mavin et al. — five patterns + Complex) ───────────────────


def test_classify_ubiquitous() -> None:
    assert classify_ears("The system shall parse CSV.") == "ubiquitous"


def test_classify_ubiquitous_with_named_system() -> None:
    assert classify_ears("The parser shall tokenise the input.") == "ubiquitous"


def test_classify_state_driven() -> None:
    assert (
        classify_ears("While the queue is non-empty, the system shall dispatch tasks.")
        == "state_driven"
    )


def test_classify_event_driven() -> None:
    assert (
        classify_ears("When the user submits a file, the system shall validate it.")
        == "event_driven"
    )


def test_classify_optional_feature() -> None:
    assert (
        classify_ears("Where logging is enabled, the system shall record each request.")
        == "optional_feature"
    )


def test_classify_unwanted_behaviour() -> None:
    assert (
        classify_ears("If the input is malformed, then the system shall raise ValueError.")
        == "unwanted_behaviour"
    )


def test_classify_complex_while_when() -> None:
    assert (
        classify_ears(
            "While the build is running, when a phase fails, "
            "the system shall halt the pipeline."
        )
        == "complex"
    )


def test_classify_complex_when_if_then() -> None:
    assert (
        classify_ears(
            "When a file is uploaded, if the checksum mismatches, "
            "then the system shall reject the upload."
        )
        == "complex"
    )


def test_classify_if_without_then_is_rejected() -> None:
    # Near-miss: Unwanted-behaviour requires the explicit 'then'.
    assert classify_ears("If the input is malformed, the system shall raise ValueError.") is None


def test_classify_missing_shall_is_rejected() -> None:
    assert classify_ears("When the user clicks, the system validates the input.") is None


def test_classify_prose_is_rejected() -> None:
    assert classify_ears("Parses CSV files.") is None


def test_classify_while_after_shall_is_rejected() -> None:
    # Clause ORDER matters: conditions come BEFORE the shall-clause.
    assert classify_ears("The system shall dispatch tasks while the queue is non-empty.") is None


def test_classify_empty_is_rejected() -> None:
    assert classify_ears("   ") is None


# ── check_requirement_wording ────────────────────────────────────────────────


def test_wording_ok() -> None:
    assert check_requirement_wording("HLR", "The system shall parse CSV.") is None


def test_wording_accepts_every_ears_pattern() -> None:
    # Regression: the old invariant forced a 'The system shall' prefix,
    # forbidding four of the five real EARS patterns.
    for text in (
        "The system shall parse CSV.",
        "While the queue is non-empty, the system shall dispatch tasks.",
        "When the user submits a file, the system shall validate it.",
        "Where logging is enabled, the system shall record each request.",
        "If the input is malformed, then the system shall raise ValueError.",
        "While the build is running, when a phase fails, the system shall halt.",
    ):
        assert check_requirement_wording("HLR", text) is None, text


def test_wording_bad_prefix_rejected() -> None:
    msg = check_requirement_wording("LLR", "Parses CSV files.")
    assert msg is not None
    assert "The system shall" in msg


def test_wording_if_without_then_rejected_names_nearest_template() -> None:
    msg = check_requirement_wording(
        "HLR", "If the input is malformed, the system shall raise ValueError."
    )
    assert msg is not None
    assert "If <condition>, then the system shall <response>" in msg


def test_wording_missing_shall_rejected_with_template() -> None:
    msg = check_requirement_wording(
        "HLR", "When the user clicks, the system validates the input."
    )
    assert msg is not None
    assert "When <trigger>, the system shall <response>" in msg


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


# ── CONTRACT public_api shape (specs/13 Structured Public API Surface) ──────


def _api_entry() -> dict[str, str]:
    return {
        "module": "merge_sort",
        "symbol": "sort",
        "kind": "function",
        "signature": "def sort(items: list) -> None",
    }


def test_contract_public_api_valid_passes() -> None:
    from backend.analysis.node_invariants import check_contract_public_api

    assert check_contract_public_api("CONTRACT", {"public_api": [_api_entry()]}) is None


def test_contract_public_api_missing_rejected() -> None:
    from backend.analysis.node_invariants import check_contract_public_api

    msg = check_contract_public_api("CONTRACT", {})
    assert msg is not None
    assert "public_api" in msg


def test_contract_public_api_empty_list_rejected() -> None:
    from backend.analysis.node_invariants import check_contract_public_api

    msg = check_contract_public_api("CONTRACT", {"public_api": []})
    assert msg is not None
    assert "public_api" in msg


def test_contract_public_api_bad_kind_rejected() -> None:
    from backend.analysis.node_invariants import check_contract_public_api

    entry = _api_entry()
    entry["kind"] = "coroutine"
    msg = check_contract_public_api("CONTRACT", {"public_api": [entry]})
    assert msg is not None
    assert "kind" in msg


def test_contract_public_api_missing_key_rejected() -> None:
    from backend.analysis.node_invariants import check_contract_public_api

    entry = _api_entry()
    del entry["signature"]
    msg = check_contract_public_api("CONTRACT", {"public_api": [entry]})
    assert msg is not None
    assert "signature" in msg


def test_contract_public_api_empty_value_rejected() -> None:
    from backend.analysis.node_invariants import check_contract_public_api

    entry = _api_entry()
    entry["symbol"] = "  "
    msg = check_contract_public_api("CONTRACT", {"public_api": [entry]})
    assert msg is not None
    assert "symbol" in msg


def test_contract_public_api_non_contract_skipped() -> None:
    from backend.analysis.node_invariants import check_contract_public_api

    assert check_contract_public_api("MODULE", {}) is None


# ── CONTRACT prohibited_constructs shape (specs/13, optional) ───────────────


def test_prohibited_constructs_absent_is_valid() -> None:
    from backend.analysis.node_invariants import check_contract_prohibited

    assert check_contract_prohibited("CONTRACT", {}) is None


def test_prohibited_constructs_valid_passes() -> None:
    from backend.analysis.node_invariants import check_contract_prohibited

    props = {
        "prohibited_constructs": [
            {"construct": "eval", "rationale": "§12 forbids delegation"},
        ],
    }
    assert check_contract_prohibited("CONTRACT", props) is None


def test_prohibited_constructs_missing_rationale_rejected() -> None:
    from backend.analysis.node_invariants import check_contract_prohibited

    props = {"prohibited_constructs": [{"construct": "eval"}]}
    msg = check_contract_prohibited("CONTRACT", props)
    assert msg is not None
    assert "rationale" in msg


def test_prohibited_constructs_empty_construct_rejected() -> None:
    from backend.analysis.node_invariants import check_contract_prohibited

    props = {"prohibited_constructs": [{"construct": " ", "rationale": "x"}]}
    msg = check_contract_prohibited("CONTRACT", props)
    assert msg is not None
    assert "construct" in msg


def test_prohibited_constructs_non_list_rejected() -> None:
    from backend.analysis.node_invariants import check_contract_prohibited

    msg = check_contract_prohibited("CONTRACT", {"prohibited_constructs": "eval"})
    assert msg is not None
    assert "prohibited_constructs" in msg


def test_prohibited_constructs_non_contract_skipped() -> None:
    from backend.analysis.node_invariants import check_contract_prohibited

    assert check_contract_prohibited("MODULE", {"prohibited_constructs": "x"}) is None


# ── PARA exemption from byte-identical sibling content ───────────────────────


def test_para_siblings_may_share_identical_content() -> None:
    """PARAs mirror the document: sections may repeat identical text and
    heading PARAs are empty by design (live gap: topological_sort r3,
    PARA-0010/0011/0013 vs PARA-0008 — empty section headings)."""
    sib = _n("PARA-0008", "PARA", parent_id="DOC-1", title="Derived Queries",
             content="Repeated sentence from the whitepaper.")
    msg = check_sibling_content_unique(
        "PARA", "Repeated sentence from the whitepaper.", "PARA-0010", [sib],
    )
    assert msg is None


def test_non_para_siblings_still_rejected_for_identical_content() -> None:
    sib = _n("LLR-0001", "LLR", parent_id="HLR-1", title="A",
             content="The system shall do X.")
    msg = check_sibling_content_unique(
        "LLR", "the system shall do x.", "LLR-0002", [sib],
    )
    assert msg is not None
    assert "LLR-0001" in msg


# ── check_title_distinct_from_parent ─────────────────────────────────────────


def test_title_distinct_from_parent_ok() -> None:
    parent = _n("HLR-0077", "HLR", title="Return Descendant Set")
    assert check_title_distinct_from_parent(
        "LLR", "Compute Descendant Closure", parent,
    ) is None


def test_title_colliding_with_parent_rejected() -> None:
    """Live gap (topological_sort r3): LLR-0073 titled identically to its
    parent HLR-0077 — write path must reject, matching the analyser's
    TITLE_COLLIDES_WITH_PARENT check."""
    parent = _n("HLR-0077", "HLR", title="Return Descendant Set")
    msg = check_title_distinct_from_parent(
        "LLR", "  return descendant set ", parent,
    )
    assert msg is not None
    assert "HLR-0077" in msg
    assert "narrower" in msg or "distinct" in msg


def test_title_parent_collision_exempt_types_skip() -> None:
    parent = _n("PROJ-0001", "PROJECT", title="Same Title")
    for ntype in ("PROJECT", "DOCUMENT", "RESULT", "RECORD"):
        assert check_title_distinct_from_parent(ntype, "Same Title", parent) is None


def test_title_parent_collision_no_parent_skips() -> None:
    assert check_title_distinct_from_parent("HLR", "Any Title", None) is None


# ── CONTRACT public_api obligation fields (specs/13 U2 — CONTRACT records) ──


def _api_entry_with(**extra: object) -> dict[str, object]:
    entry: dict[str, object] = dict(_api_entry())
    entry.update(extra)
    return entry


def _raises_rec() -> dict[str, str]:
    return {
        "cls": "CyclicGraphError",
        "base": "ValueError",
        "when": "the input graph contains a cycle",
    }


def test_contract_obligations_valid_passes() -> None:
    from backend.analysis.node_invariants import check_contract_public_api

    entry = _api_entry_with(
        raises=[_raises_rec()],
        preconditions=["items is a list"],
        postconditions=["the returned list is sorted ascending"],
        invariants=["input list is never mutated"],
    )
    assert check_contract_public_api("CONTRACT", {"public_api": [entry]}) is None


def test_contract_raises_non_list_rejected() -> None:
    from backend.analysis.node_invariants import check_contract_public_api

    entry = _api_entry_with(raises="CyclicGraphError")
    msg = check_contract_public_api("CONTRACT", {"public_api": [entry]})
    assert msg is not None
    assert "raises" in msg
    assert "list" in msg


def test_contract_raises_entry_not_object_rejected() -> None:
    from backend.analysis.node_invariants import check_contract_public_api

    entry = _api_entry_with(raises=["CyclicGraphError"])
    msg = check_contract_public_api("CONTRACT", {"public_api": [entry]})
    assert msg is not None
    assert "raises" in msg


def test_contract_raises_missing_when_rejected() -> None:
    from backend.analysis.node_invariants import check_contract_public_api

    rec = _raises_rec()
    del rec["when"]
    entry = _api_entry_with(raises=[rec])
    msg = check_contract_public_api("CONTRACT", {"public_api": [entry]})
    assert msg is not None
    assert "when" in msg


def test_contract_raises_empty_cls_rejected() -> None:
    from backend.analysis.node_invariants import check_contract_public_api

    rec = _raises_rec()
    rec["cls"] = "   "
    entry = _api_entry_with(raises=[rec])
    msg = check_contract_public_api("CONTRACT", {"public_api": [entry]})
    assert msg is not None
    assert "cls" in msg


def test_contract_preconditions_non_list_rejected() -> None:
    from backend.analysis.node_invariants import check_contract_public_api

    entry = _api_entry_with(preconditions="items is a list")
    msg = check_contract_public_api("CONTRACT", {"public_api": [entry]})
    assert msg is not None
    assert "preconditions" in msg


def test_contract_postconditions_non_string_item_rejected() -> None:
    from backend.analysis.node_invariants import check_contract_public_api

    entry = _api_entry_with(postconditions=["result is sorted", 42])
    msg = check_contract_public_api("CONTRACT", {"public_api": [entry]})
    assert msg is not None
    assert "postconditions" in msg


def test_contract_invariants_empty_string_rejected() -> None:
    from backend.analysis.node_invariants import check_contract_public_api

    entry = _api_entry_with(invariants=["  "])
    msg = check_contract_public_api("CONTRACT", {"public_api": [entry]})
    assert msg is not None
    assert "invariants" in msg


def test_contract_entry_without_obligation_fields_still_passes() -> None:
    """Obligation fields are optional — a bare entry stays valid."""
    from backend.analysis.node_invariants import check_contract_public_api

    assert check_contract_public_api("CONTRACT", {"public_api": [_api_entry()]}) is None


# ── check_non_normative_marking (U6 — cover or classify) ─────────────────────


def test_non_normative_marking_absent_passes() -> None:
    from backend.analysis.node_invariants import check_non_normative_marking

    assert check_non_normative_marking("PARA", {}) is None
    assert check_non_normative_marking("HLR", {"other": 1}) is None


def test_non_normative_valid_reason_kinds_pass() -> None:
    from backend.analysis.node_invariants import check_non_normative_marking

    for reason in (
        "background/context",
        "duplicate-of-PARA-0012",
        "example/illustration",
        "meta/document-structure",
    ):
        props = {"non_normative": True, "non_normative_rationale": reason}
        assert check_non_normative_marking("PARA", props) is None, reason


def test_non_normative_missing_rationale_rejected_names_kinds() -> None:
    from backend.analysis.node_invariants import check_non_normative_marking

    msg = check_non_normative_marking("PARA", {"non_normative": True})
    assert msg is not None
    assert "non_normative_rationale" in msg
    assert "background/context" in msg
    assert "duplicate-of-" in msg
    assert "example/illustration" in msg
    assert "meta/document-structure" in msg


def test_non_normative_empty_rationale_rejected() -> None:
    from backend.analysis.node_invariants import check_non_normative_marking

    props = {"non_normative": True, "non_normative_rationale": "   "}
    assert check_non_normative_marking("PARA", props) is not None


def test_non_normative_unknown_reason_kind_rejected() -> None:
    from backend.analysis.node_invariants import check_non_normative_marking

    props = {"non_normative": True, "non_normative_rationale": "seems boring"}
    msg = check_non_normative_marking("PARA", props)
    assert msg is not None
    assert "background/context" in msg


def test_non_normative_duplicate_kind_requires_para_id_suffix() -> None:
    from backend.analysis.node_invariants import check_non_normative_marking

    props = {"non_normative": True, "non_normative_rationale": "duplicate-of-"}
    assert check_non_normative_marking("PARA", props) is not None


def test_non_normative_on_non_para_rejected() -> None:
    from backend.analysis.node_invariants import check_non_normative_marking

    props = {"non_normative": True, "non_normative_rationale": "background/context"}
    msg = check_non_normative_marking("HLR", props)
    assert msg is not None
    assert "PARA" in msg


def test_non_normative_flag_must_be_boolean() -> None:
    from backend.analysis.node_invariants import check_non_normative_marking

    props = {"non_normative": "yes", "non_normative_rationale": "background/context"}
    assert check_non_normative_marking("PARA", props) is not None


def test_non_normative_rationale_without_flag_rejected() -> None:
    from backend.analysis.node_invariants import check_non_normative_marking

    props = {"non_normative_rationale": "background/context"}
    msg = check_non_normative_marking("PARA", props)
    assert msg is not None
    assert "non_normative" in msg


def test_non_normative_false_without_rationale_passes() -> None:
    from backend.analysis.node_invariants import check_non_normative_marking

    assert check_non_normative_marking("PARA", {"non_normative": False}) is None


def test_is_marked_non_normative_true_only_for_boolean_true() -> None:
    from backend.analysis.node_invariants import is_marked_non_normative

    assert is_marked_non_normative({"non_normative": True}) is True
    assert is_marked_non_normative({"non_normative": False}) is False
    assert is_marked_non_normative({}) is False
