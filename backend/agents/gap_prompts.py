"""Built-in targeted system prompts for each gap type.

These are the default prompts used when no user override exists.
They are narrowly scoped to the specific task each gap represents,
mentioning only the tools that are available for that gap.

All templates live in ``templates/gaps/`` and are rendered via Jinja2.

Hierarchy (highest → lowest priority):
  user gap override  (_GAP_PROMPTS in factory.py)
  gap built-in default  (this module)
  user role override  (_ROLE_PROMPTS in factory.py)
  role built-in default  (_build_default_prompt in factory.py)
"""

from __future__ import annotations

from backend.prompt_loader import render

# Mapping from gap type value → template file name
_GAP_TEMPLATE_MAP: dict[str, str] = {
    "UNCHUNKED_DOCUMENT": "gaps/unchunked_document.j2",
    "UNCOVERED_PARA": "gaps/uncovered_para.j2",
    "UNARCHITECTED": "gaps/unarchitected.j2",
    "UNMODULARISED": "gaps/unmodularised.j2",
    "UNCONTRACTED": "gaps/uncontracted.j2",
    "UNREFINED_HLR": "gaps/unrefined_hlr.j2",
    "UNDESIGNED": "gaps/undesigned.j2",
    "UNSUITED": "gaps/unsuited.j2",
    "UNTESTED_HLR": "gaps/untested_hlr.j2",
    "UNTESTED_LLR": "gaps/untested_llr.j2",
    "STALE_NODE": "gaps/stale_node.j2",
    "ORPHAN_NODE": "gaps/orphan_node.j2",
    "EMPTY_CONTENT": "gaps/empty_content.j2",
    "STALE_TRACE_TO": "gaps/stale_trace_to.j2",
    "INCONSISTENT_CONTENT": "gaps/inconsistent_content.j2",
    "DUPLICATE_NODE": "gaps/duplicate_node.j2",
    "NON_ATOMIC_REQUIREMENT": "gaps/non_atomic_requirement.j2",
    "NON_EARS_REQUIREMENT": "gaps/non_ears_requirement.j2",
    "MALFORMED_REQUIREMENT": "gaps/malformed_requirement.j2",
    "UNTITLED_NODE": "gaps/untitled_node.j2",
    "TITLE_COLLIDES_WITH_PARENT": "gaps/title_collides_with_parent.j2",
    "SIBLING_TITLE_DUPLICATE": "gaps/sibling_title_duplicate.j2",
    "STALE_TITLE": "gaps/stale_title.j2",
    "VAGUE_TITLE": "gaps/vague_title.j2",
    "INADEQUATE_CONTENT": "gaps/inadequate_content.j2",
    "VAGUE_REQUIREMENT": "gaps/vague_requirement.j2",
    "UNTESTABLE_REQUIREMENT": "gaps/untestable_requirement.j2",
    "CONTRADICTORY_REQUIREMENTS": "gaps/contradictory_requirements.j2",
    "INCOMPLETE_DECOMPOSITION": "gaps/incomplete_decomposition.j2",
    "CONTRACT_VIOLATION": "gaps/contract_violation.j2",
    "CROSS_MODULE_COUPLING": "gaps/cross_module_coupling.j2",
}


def get_default_gap_prompt(gap_type_value: str) -> str | None:
    """Return the built-in default prompt for a gap type, or None if not defined."""
    template = _GAP_TEMPLATE_MAP.get(gap_type_value)
    if template is None:
        return None
    return render(template)


def has_default_gap_prompt(gap_type_value: str) -> bool:
    """Return True if a built-in gap-specific default prompt exists."""
    return gap_type_value in _GAP_TEMPLATE_MAP
