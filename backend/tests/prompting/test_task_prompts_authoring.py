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


def test_batch_phase7_prompt_includes_ears_patterns() -> None:
    prompt = build_batch_phase7_prompt(
        [{"node_id": "HLR-0001", "title": "Sort", "content": "The system shall sort."}],
        [], [],
    )
    for template in _TEMPLATES:
        assert template in prompt, template


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
