"""Per-gap-type prompt templates for agent task descriptions.

Extracted from task_builder.py to keep that file focused on context
building. Each helper returns a ``(description, expected_output)`` tuple.

The helpers live in ``task_prompts_authoring`` (create new artefacts) and
``task_prompts_repair`` (fix existing nodes); this module re-exports them
all so import sites and patch targets remain stable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.analysis.gaps import GapType

if TYPE_CHECKING:
    from backend.analysis.gaps import Gap


from backend.prompting.task_prompts_authoring import _architect as _architect
from backend.prompting.task_prompts_authoring import _contract as _contract
from backend.prompting.task_prompts_authoring import _design as _design
from backend.prompting.task_prompts_authoring import _doc_chunk as _doc_chunk
from backend.prompting.task_prompts_authoring import _llr as _llr
from backend.prompting.task_prompts_authoring import _modularise as _modularise
from backend.prompting.task_prompts_authoring import _para_hlr as _para_hlr
from backend.prompting.task_prompts_authoring import _suite as _suite
from backend.prompting.task_prompts_authoring import _test_hlr as _test_hlr
from backend.prompting.task_prompts_authoring import _test_llr as _test_llr
from backend.prompting.task_prompts_repair import (
    _contract_violation as _contract_violation,
)
from backend.prompting.task_prompts_repair import (
    _contradictory_requirements as _contradictory_requirements,
)
from backend.prompting.task_prompts_repair import (
    _cross_module_coupling as _cross_module_coupling,
)
from backend.prompting.task_prompts_repair import _duplicate_node as _duplicate_node
from backend.prompting.task_prompts_repair import _empty_content as _empty_content
from backend.prompting.task_prompts_repair import (
    _inadequate_content as _inadequate_content,
)
from backend.prompting.task_prompts_repair import (
    _incomplete_decomposition as _incomplete_decomposition,
)
from backend.prompting.task_prompts_repair import (
    _inconsistent_content as _inconsistent_content,
)
from backend.prompting.task_prompts_repair import (
    _malformed_requirement as _malformed_requirement,
)
from backend.prompting.task_prompts_repair import (
    _non_atomic_requirement as _non_atomic_requirement,
)
from backend.prompting.task_prompts_repair import (
    _non_ears_requirement as _non_ears_requirement,
)
from backend.prompting.task_prompts_repair import _orphan_node as _orphan_node
from backend.prompting.task_prompts_repair import (
    _sibling_title_duplicate as _sibling_title_duplicate,
)
from backend.prompting.task_prompts_repair import (
    _stale_architecture as _stale_architecture,
)
from backend.prompting.task_prompts_repair import _stale_node as _stale_node
from backend.prompting.task_prompts_repair import _stale_suite as _stale_suite
from backend.prompting.task_prompts_repair import _stale_title as _stale_title
from backend.prompting.task_prompts_repair import _stale_trace as _stale_trace
from backend.prompting.task_prompts_repair import (
    _title_collides_with_parent as _title_collides_with_parent,
)
from backend.prompting.task_prompts_repair import _untestable_requirement as _untestable_requirement
from backend.prompting.task_prompts_repair import _untitled_node as _untitled_node
from backend.prompting.task_prompts_repair import _vague_requirement as _vague_requirement
from backend.prompting.task_prompts_repair import _vague_title as _vague_title


def build_descriptions(
    nid: str,
    ctx: str,
    gap: Gap,
    *,
    suite_id: str = "",
) -> dict[GapType, tuple[str, str]]:
    """Build the full dispatch-table mapping GapType -> (description, output)."""
    return {
        GapType.UNCHUNKED_DOCUMENT: _doc_chunk(nid, ctx),
        GapType.UNCOVERED_PARA: _para_hlr(nid, ctx),
        GapType.UNARCHITECTED: _architect(nid, ctx),
        GapType.UNMODULARISED: _modularise(nid, ctx),
        GapType.UNCONTRACTED: _contract(nid, ctx),
        GapType.UNREFINED_HLR: _llr(nid, ctx),
        GapType.UNDESIGNED: _design(nid, ctx),
        GapType.UNSUITED: _suite(nid, ctx),
        GapType.UNTESTED_HLR: _test_hlr(nid, ctx, suite_id=suite_id),
        GapType.UNTESTED_LLR: _test_llr(nid, ctx, suite_id=suite_id),
        # UNSYNCED_DESIGN / UNSYNCED_TEST: handled by workspace_sync step (no agent)
        GapType.STALE_NODE: _stale_node(nid, ctx, gap),
        GapType.ORPHAN_NODE: _orphan_node(nid, ctx),
        GapType.EMPTY_CONTENT: _empty_content(nid, ctx),
        GapType.STALE_TRACE_TO: _stale_trace(nid, gap, ctx),
        GapType.INCONSISTENT_CONTENT: _inconsistent_content(nid, ctx, gap),
        GapType.NON_ATOMIC_REQUIREMENT: _non_atomic_requirement(nid, ctx, gap),
        GapType.NON_EARS_REQUIREMENT: _non_ears_requirement(nid, ctx, gap),
        GapType.MALFORMED_REQUIREMENT: _malformed_requirement(nid, ctx),
        GapType.UNTITLED_NODE: _untitled_node(nid, ctx),
        GapType.TITLE_COLLIDES_WITH_PARENT: _title_collides_with_parent(nid, ctx, gap),
        GapType.SIBLING_TITLE_DUPLICATE: _sibling_title_duplicate(nid, ctx, gap),
        GapType.STALE_TITLE: _stale_title(nid, ctx, gap),
        GapType.VAGUE_TITLE: _vague_title(nid, ctx, gap),
        GapType.DUPLICATE_NODE: _duplicate_node(nid, ctx, gap),
        GapType.VAGUE_REQUIREMENT: _vague_requirement(nid, ctx),
        GapType.UNTESTABLE_REQUIREMENT: _untestable_requirement(nid, ctx),
        GapType.CONTRADICTORY_REQUIREMENTS: _contradictory_requirements(nid, ctx, gap),
        GapType.INCOMPLETE_DECOMPOSITION: _incomplete_decomposition(nid, ctx),
        GapType.INADEQUATE_CONTENT: _inadequate_content(nid, ctx),
        GapType.CONTRACT_VIOLATION: _contract_violation(nid, ctx),
        GapType.CROSS_MODULE_COUPLING: _cross_module_coupling(nid, ctx),
        GapType.STALE_ARCHITECTURE: _stale_architecture(nid, ctx, gap),
        GapType.STALE_SUITE: _stale_suite(nid, ctx, gap),
    }

