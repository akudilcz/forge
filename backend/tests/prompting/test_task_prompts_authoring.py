"""U3: real EARS (Mavin et al.) in the requirement-authoring material.

The five EARS pattern templates are published verbatim as a shared
``EARS_PATTERNS`` constant and embedded in every prompt that authors or
repairs requirement wording, replacing the old (wrong) rule that forced a
'The system shall' prefix and told agents to put conditions AFTER the
shall-clause.
"""

from __future__ import annotations

from backend.prompting.batch_prompts import (
    build_batch_phase3_prompt,
    build_batch_phase7_prompt,
)
from backend.prompting.repair_batch import WORDING_REPAIR_SYSTEM_PROMPT
from backend.prompting.task_prompts_authoring import EARS_PATTERNS, _llr, _para_hlr
from backend.prompting.task_prompts_repair import (
    _malformed_requirement,
    _non_ears_requirement,
)

_TEMPLATES = (
    "The <system> shall <response>.",
    "While <state>, the <system> shall <response>.",
    "When <trigger>, the <system> shall <response>.",
    "Where <feature>, the <system> shall <response>.",
    "If <condition>, then the <system> shall <response>.",
)


def test_ears_patterns_constant_contains_all_five_templates_verbatim() -> None:
    for template in _TEMPLATES:
        assert template in EARS_PATTERNS, template


def test_para_hlr_prompt_teaches_ears_patterns() -> None:
    description, _ = _para_hlr("PARA-0001", "")
    for template in _TEMPLATES:
        assert template in description, template


def test_llr_prompt_teaches_ears_patterns() -> None:
    description, _ = _llr("HLR-0001", "")
    for template in _TEMPLATES:
        assert template in description, template


def test_batch_phase3_prompt_includes_ears_patterns() -> None:
    prompt = build_batch_phase3_prompt(
        [{"node_id": "PARA-0001", "content": "The system must sort."}], [], [],
    )
    for template in _TEMPLATES:
        assert template in prompt, template


_MODULE = {
    "node_id": "MODULE-0001",
    "title": "Sorting engine",
    "content": "Responsibilities: sorting. Class plan: Sorter.",
}


def test_batch_phase7_prompt_includes_ears_patterns() -> None:
    prompt = build_batch_phase7_prompt(
        [{"node_id": "HLR-0001", "title": "Sort", "content": "The system shall sort."}],
        _MODULE, None, [], [],
    )
    for template in _TEMPLATES:
        assert template in prompt, template


def test_batch_phase7_prompt_carries_implementable_spec_litmus() -> None:
    """U8: the fused authoring prompt embeds the DO-178C litmus — LLR is
    directly implementable from its text + CONTRACT alone; DESIGN is private
    structure and algorithm choice only (the U2 dividing rule)."""
    from backend.prompting.task_prompts_authoring import IMPLEMENTABLE_SPEC_LITMUS

    prompt = build_batch_phase7_prompt(
        [{"node_id": "HLR-0001", "title": "Sort", "content": "The system shall sort."}],
        _MODULE, None, [], [],
    )
    assert IMPLEMENTABLE_SPEC_LITMUS in prompt
    assert "CONTRACT alone" in IMPLEMENTABLE_SPEC_LITMUS
    assert "private structure" in IMPLEMENTABLE_SPEC_LITMUS
    assert "algorithm choice" in IMPLEMENTABLE_SPEC_LITMUS


def test_batch_phase7_prompt_renders_contract_record_obligations() -> None:
    """U8: the module's structured CONTRACT record (public_api incl.
    obligation fields) is rendered so LLRs align to real signatures."""
    contract = {
        "node_id": "CONTRACT-0001",
        "content": "Interface prose.",
        "properties": {
            "public_api": [
                {
                    "module": "sorter", "symbol": "sort", "kind": "function",
                    "signature": "def sort(items: list[int]) -> list[int]",
                    "raises": [{"cls": "SortError", "base": "ValueError",
                                "when": "items is not comparable"}],
                    "postconditions": ["output is a permutation of input"],
                }
            ]
        },
    }
    prompt = build_batch_phase7_prompt(
        [{"node_id": "HLR-0001", "title": "Sort", "content": "The system shall sort."}],
        _MODULE, contract, [], [],
    )
    assert "def sort(items: list[int]) -> list[int]" in prompt
    assert "SortError" in prompt
    assert "output is a permutation of input" in prompt


def test_non_ears_repair_prompt_teaches_real_patterns() -> None:
    description, _ = _non_ears_requirement("HLR-0001", "")
    for template in _TEMPLATES:
        assert template in description, template
    # The old rule inverted Mavin's clause order.
    assert "AFTER the shall-clause" not in description


def test_malformed_repair_prompt_no_longer_inverts_clause_order() -> None:
    description, _ = _malformed_requirement("HLR-0001", "")
    assert "AFTER the shall-clause" not in description
    for template in _TEMPLATES:
        assert template in description, template


def test_wording_repair_system_prompt_teaches_real_patterns() -> None:
    for template in _TEMPLATES:
        assert template in WORDING_REPAIR_SYSTEM_PROMPT, template
    assert "AFTER the shall-clause" not in WORDING_REPAIR_SYSTEM_PROMPT


# ── U4: derived-requirement + verification-method prompt pins (specs/13) ─────


def _assert_provenance_instructions(text: str) -> None:
    from backend.prompting.task_prompts_authoring import (
        REQUIREMENT_PROVENANCE_FIELDS,
    )

    assert REQUIREMENT_PROVENANCE_FIELDS in text
    # The four standard methods (IEEE 29148) are all named.
    for method in ("Test", "Analysis", "Inspection", "Demonstration"):
        assert method in REQUIREMENT_PROVENANCE_FIELDS, method
    # Derivation instruction: derived + rationale for design-necessity reqs.
    assert "derived_rationale" in REQUIREMENT_PROVENANCE_FIELDS
    assert "design necessity" in REQUIREMENT_PROVENANCE_FIELDS


def test_para_hlr_prompt_instructs_provenance_properties() -> None:
    description, _ = _para_hlr("PARA-0001", "")
    _assert_provenance_instructions(description)


def test_llr_prompt_instructs_provenance_properties() -> None:
    description, _ = _llr("HLR-0001", "")
    _assert_provenance_instructions(description)


def test_batch_phase3_prompt_instructs_provenance_properties() -> None:
    prompt = build_batch_phase3_prompt(
        [{"node_id": "PARA-0001", "content": "The system must sort."}], [], [],
    )
    _assert_provenance_instructions(prompt)
    # The derive tool's outputs must be persisted, not discarded.
    assert "verification_method" in prompt
    assert "derived" in prompt


def test_batch_phase7_prompt_instructs_provenance_properties() -> None:
    prompt = build_batch_phase7_prompt(
        [{"node_id": "HLR-0001", "title": "Sort", "content": "The system shall sort."}],
        _MODULE, None, [], [],
    )
    _assert_provenance_instructions(prompt)


def test_case_contract_encoding_states_verification_method_rule() -> None:
    from backend.prompting.task_prompts_authoring import CASE_CONTRACT_ENCODING

    assert "verification_method" in CASE_CONTRACT_ENCODING
    assert "executable" in CASE_CONTRACT_ENCODING.lower()
    for method in ("analysis", "inspection", "demonstration"):
        assert method in CASE_CONTRACT_ENCODING, method


def test_batch_phase10_prompt_renders_requirement_marking() -> None:
    from backend.prompting.batch_prompts import build_batch_phase10_prompt

    prompt = build_batch_phase10_prompt(
        untested_hlrs=[{
            "node_id": "HLR-0001", "title": "Sort", "content": "The system shall sort.",
            "properties": {"verification_method": "analysis", "derived": True,
                           "derived_rationale": "Design necessity."},
        }],
        untested_llrs=[{
            "node_id": "LLR-0001", "title": "Merge", "content": "The system shall merge.",
            "properties": {"verification_method": "test"},
        }],
        suite={"node_id": "SUITE-0001", "content": "strategy"},
        existing_cases=[],
        contract_records=[],
    )
    assert "verification_method=analysis" in prompt
    assert "derived=True" in prompt
    assert "verification_method=test" in prompt


def test_batch_phase10_prompt_without_properties_still_renders() -> None:
    from backend.prompting.batch_prompts import build_batch_phase10_prompt

    prompt = build_batch_phase10_prompt(
        untested_hlrs=[{"node_id": "HLR-0001", "title": "Sort",
                        "content": "The system shall sort."}],
        untested_llrs=[],
        suite=None,
        existing_cases=[],
        contract_records=[],
    )
    assert "HLR-0001" in prompt
