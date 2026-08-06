"""U4 — derived-requirement and verification-method markings (specs/13).

Shape checks for ``properties.derived`` / ``derived_rationale`` (DO-178C
derived requirements) and ``properties.verification_method`` (IEEE 29148
four standard methods) on HLR/LLR nodes.
"""

from __future__ import annotations

from backend.analysis.requirement_marking import (
    VERIFICATION_METHODS,
    check_derived_marking,
    check_verification_method,
    is_marked_derived,
)

# ── check_derived_marking ────────────────────────────────────────────────────


def test_derived_marking_absent_passes() -> None:
    assert check_derived_marking("HLR", {}) is None
    assert check_derived_marking("LLR", {"other": 1}) is None


def test_derived_true_with_rationale_passes_on_hlr_and_llr() -> None:
    props = {"derived": True, "derived_rationale": "Needed to bound queue growth."}
    assert check_derived_marking("HLR", props) is None
    assert check_derived_marking("LLR", props) is None


def test_derived_true_without_rationale_rejected() -> None:
    msg = check_derived_marking("HLR", {"derived": True})
    assert msg is not None
    assert "derived_rationale" in msg


def test_derived_true_empty_rationale_rejected() -> None:
    props = {"derived": True, "derived_rationale": "   "}
    msg = check_derived_marking("LLR", props)
    assert msg is not None
    assert "derived_rationale" in msg


def test_derived_true_non_string_rationale_rejected() -> None:
    props = {"derived": True, "derived_rationale": 42}
    assert check_derived_marking("HLR", props) is not None


def test_derived_on_non_requirement_rejected() -> None:
    props = {"derived": True, "derived_rationale": "Design necessity."}
    for ntype in ("PARA", "MODULE", "CASE_HLR", "DESIGN"):
        msg = check_derived_marking(ntype, props)
        assert msg is not None, ntype
        assert "HLR" in msg
        assert "LLR" in msg


def test_derived_flag_must_be_boolean() -> None:
    props = {"derived": "yes", "derived_rationale": "Design necessity."}
    msg = check_derived_marking("HLR", props)
    assert msg is not None
    assert "boolean" in msg


def test_derived_rationale_without_flag_rejected() -> None:
    msg = check_derived_marking("HLR", {"derived_rationale": "Design necessity."})
    assert msg is not None
    assert "derived" in msg


def test_derived_false_without_rationale_passes() -> None:
    assert check_derived_marking("HLR", {"derived": False}) is None


def test_derived_false_with_rationale_rejected() -> None:
    props = {"derived": False, "derived_rationale": "Design necessity."}
    assert check_derived_marking("HLR", props) is not None


def test_is_marked_derived_true_only_for_boolean_true() -> None:
    assert is_marked_derived({"derived": True}) is True
    assert is_marked_derived({"derived": False}) is False
    assert is_marked_derived({"derived": "true"}) is False
    assert is_marked_derived({}) is False


# ── check_verification_method ────────────────────────────────────────────────


def test_verification_method_absent_passes_legacy_graphs() -> None:
    assert check_verification_method("HLR", {}) is None
    assert check_verification_method("LLR", {"derived": False}) is None


def test_verification_method_standard_four_pass() -> None:
    assert VERIFICATION_METHODS == ("test", "analysis", "inspection", "demonstration")
    for method in VERIFICATION_METHODS:
        props = {"verification_method": method}
        assert check_verification_method("HLR", props) is None, method
        assert check_verification_method("LLR", props) is None, method


def test_verification_method_case_insensitive() -> None:
    for method in ("Test", "ANALYSIS", "Inspection", "Demonstration"):
        assert check_verification_method("HLR", {"verification_method": method}) is None


def test_verification_method_unknown_value_rejected_names_four() -> None:
    msg = check_verification_method("HLR", {"verification_method": "review"})
    assert msg is not None
    for method in VERIFICATION_METHODS:
        assert method in msg


def test_verification_method_non_string_rejected() -> None:
    assert check_verification_method("HLR", {"verification_method": 3}) is not None
    assert check_verification_method("LLR", {"verification_method": ""}) is not None


def test_verification_method_on_non_requirement_rejected() -> None:
    props = {"verification_method": "test"}
    for ntype in ("PARA", "CONTRACT", "CASE_LLR"):
        msg = check_verification_method(ntype, props)
        assert msg is not None, ntype
        assert "HLR" in msg
        assert "LLR" in msg
