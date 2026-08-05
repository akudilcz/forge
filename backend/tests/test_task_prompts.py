"""Behavioural tests for the per-gap-type prompt builders in task_prompts.py.

``build_descriptions`` is the dispatch table consulted on every agent
dispatch (via ``build_task_description``), so each entry must yield a
non-empty ``(description, expected_output)`` pair without raising — even
when the gap carries no payload in ``Gap.context``. Gap types absent from
the table must still produce a usable fallback description.
"""

from __future__ import annotations

from typing import Any

import pytest

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.crew.task_builder import build_task_description
from backend.crew.task_prompts import build_descriptions

NODE_ID = "NODE-0042"
SUITE_ID = "SUITE-0001"
CTX_SENTINEL = "CTX-SENTINEL-CONTENT"
CTX = f"\n\nContext:\n{CTX_SENTINEL}"


def _gap(
    gap_type: GapType,
    node_id: str = NODE_ID,
    context: dict[str, Any] | None = None,
) -> Gap:
    return Gap(
        type=gap_type,
        priority=GapPriority.MAINTENANCE,
        node_id=node_id,
        description=f"test gap {gap_type.value} on {node_id}",
        context=context or {},
    )


def _entry(gap: Gap, ctx: str = CTX, suite_id: str = SUITE_ID) -> tuple[str, str]:
    """Build the full dispatch table for ``gap`` and return its own entry."""
    return build_descriptions(gap.node_id, ctx, gap, suite_id=suite_id)[gap.type]


# GapTypes with a dedicated prompt builder (computed once against a payload-free gap
# — building the table must never raise regardless of the driving gap's type).
TABLED_TYPES = sorted(
    build_descriptions(NODE_ID, "", _gap(GapType.STALE_NODE)).keys(),
    key=lambda t: t.value,
)
UNTABLED_TYPES = sorted(set(GapType) - set(TABLED_TYPES), key=lambda t: t.value)


# ── Dispatch-table coverage ──────────────────────────────────────────────────


class TestDispatchTableCoverage:
    """Every GapType resolves to a non-empty (description, expected_output) pair."""

    @pytest.mark.parametrize("gap_type", TABLED_TYPES, ids=lambda t: t.value)
    def test_tabled_type_yields_nonempty_pair_naming_the_node(
        self, gap_type: GapType
    ) -> None:
        """A gap with an EMPTY context must still build — no KeyError, no blanks."""
        description, expected_output = _entry(_gap(gap_type))
        assert isinstance(description, str) and description.strip()
        assert isinstance(expected_output, str) and expected_output.strip()
        assert NODE_ID in description, (
            f"{gap_type.value} description does not name the target node"
        )

    @pytest.mark.parametrize("gap_type", list(GapType), ids=lambda t: t.value)
    def test_build_task_description_handles_every_gap_type(
        self, gap_type: GapType
    ) -> None:
        """The public seam covers ALL GapTypes (fallback included) — never empty."""
        description, expected_output = build_task_description(
            _gap(gap_type), "ancestor text", attempt=1, suite_id=SUITE_ID
        )
        assert description.strip()
        assert expected_output.strip()

    @pytest.mark.parametrize("gap_type", UNTABLED_TYPES, ids=lambda t: t.value)
    def test_untabled_type_falls_back_to_generic_description(
        self, gap_type: GapType
    ) -> None:
        gap = _gap(gap_type)
        description, expected_output = build_task_description(gap, "", attempt=1)
        assert gap_type.value in description
        assert gap.description in description
        assert gap_type.value in expected_output

    def test_retry_attempt_prefixes_tabled_description(self) -> None:
        description, _ = build_task_description(
            _gap(GapType.UNSUITED), "", attempt=2, suite_id=""
        )
        assert description.startswith("ATTEMPT 2:")


# ── Gap-payload interpolation ────────────────────────────────────────────────


class TestGapPayloadInterpolation:
    """Builders that read Gap.context surface the payload in the prompt."""

    def test_non_atomic_lists_llm_identified_obligations(self) -> None:
        gap = _gap(
            GapType.NON_ATOMIC_REQUIREMENT,
            context={"obligations": ["Parse the header", "Validate the checksum"]},
        )
        description, _ = _entry(gap)
        assert "LLM-identified obligations" in description
        assert "1. Parse the header" in description
        assert "2. Validate the checksum" in description

    def test_non_atomic_without_obligations_omits_the_section(self) -> None:
        description, _ = _entry(_gap(GapType.NON_ATOMIC_REQUIREMENT))
        assert "LLM-identified obligations" not in description

    def test_non_ears_includes_audit_reasoning(self) -> None:
        gap = _gap(
            GapType.NON_EARS_REQUIREMENT, context={"reasoning": "uses passive voice"}
        )
        description, _ = _entry(gap)
        assert "Audit note: uses passive voice" in description

    def test_stale_title_includes_reasoning(self) -> None:
        gap = _gap(
            GapType.STALE_TITLE,
            context={"reasoning": "title covers sorting but content covers parsing"},
        )
        description, _ = _entry(gap)
        assert "title covers sorting but content covers parsing" in description

    def test_vague_title_includes_reasoning(self) -> None:
        gap = _gap(GapType.VAGUE_TITLE, context={"reasoning": "generic label"})
        description, _ = _entry(gap)
        assert "generic label" in description

    def test_sibling_title_duplicate_names_sibling_and_shared_title(self) -> None:
        gap = _gap(
            GapType.SIBLING_TITLE_DUPLICATE,
            context={"sibling_id": "HLR-0007", "shared_title": "Parse Input"},
        )
        description, expected_output = _entry(gap)
        assert "HLR-0007" in description
        assert "Parse Input" in description
        assert "HLR-0007" in expected_output

    def test_title_collision_names_parent_and_its_title(self) -> None:
        gap = _gap(
            GapType.TITLE_COLLIDES_WITH_PARENT,
            context={"parent_id": "PARA-0003", "parent_title": "Sorting Rules"},
        )
        description, expected_output = _entry(gap)
        assert "PARA-0003" in description
        assert "Sorting Rules" in description
        assert "PARA-0003" in expected_output

    def test_duplicate_node_exact_branch_orders_deletion_of_the_copy(self) -> None:
        gap = _gap(GapType.DUPLICATE_NODE, context={"duplicate_of": "HLR-0001"})
        description, expected_output = _entry(gap)
        assert "HLR-0001" in description
        assert f"graph_delete_node(node_id={NODE_ID})" in description
        assert "HLR-0001" in expected_output

    def test_duplicate_node_semantic_branch_offers_both_verdicts(self) -> None:
        description, _ = _entry(_gap(GapType.DUPLICATE_NODE))
        assert "semantic duplicate" in description
        assert "graph_delete_node" in description
        assert "semantic_check" in description

    def test_inconsistent_content_marks_context_as_primary_reference(self) -> None:
        description, _ = _entry(_gap(GapType.INCONSISTENT_CONTENT))
        assert "PRIMARY REFERENCE" in description

    def test_inconsistent_content_without_context_omits_reference_note(self) -> None:
        description, _ = _entry(_gap(GapType.INCONSISTENT_CONTENT), ctx="")
        assert "PRIMARY REFERENCE" not in description

    def test_stale_architecture_lists_newer_hlr_ids(self) -> None:
        gap = _gap(
            GapType.STALE_ARCHITECTURE,
            context={"newer_hlr_ids": ["HLR-0009", "HLR-0010"]},
        )
        description, _ = _entry(gap)
        assert "HLR-0009" in description
        assert "HLR-0010" in description

    def test_stale_suite_lists_newer_requirement_ids(self) -> None:
        gap = _gap(GapType.STALE_SUITE, context={"newer_req_ids": ["LLR-0004"]})
        description, _ = _entry(gap)
        assert "LLR-0004" in description

    def test_stale_trace_missing_trace_branch_names_expected_type(self) -> None:
        gap = _gap(
            GapType.STALE_TRACE_TO,
            context={"missing_trace": True, "expected_type": "HLR"},
        )
        description, expected_output = _entry(gap)
        assert "no trace_to" in description
        assert "HLR" in description
        assert "graph_update_trace" in description
        assert "HLR" in expected_output

    def test_stale_trace_wrong_type_branch_lists_offending_refs(self) -> None:
        gap = _gap(
            GapType.STALE_TRACE_TO,
            context={
                "wrong_type_refs": ["SUITE-0001"],
                "expected_type": "LLR",
                "stale_refs": [],
            },
        )
        description, _ = _entry(gap)
        assert "WRONG type" in description
        assert "SUITE-0001" in description
        assert "LLR" in description

    def test_stale_trace_dead_refs_branch_lists_refs_to_remove(self) -> None:
        gap = _gap(GapType.STALE_TRACE_TO, context={"stale_refs": ["HLR-0099"]})
        description, _ = _entry(gap)
        assert "no\nlonger exist" in description or "no longer exist" in description
        assert "HLR-0099" in description
        assert "graph_remove_traces" in description


# ── Key phase-prompt spot checks ─────────────────────────────────────────────


class TestPhasePromptSpotChecks:
    """Each structural phase prompt names its target node type and embeds
    the provided node ID and context."""

    def test_doc_chunk_builds_para_tree_under_the_document(self) -> None:
        gap = _gap(GapType.UNCHUNKED_DOCUMENT, node_id="DOC-0001")
        description, expected_output = _entry(gap)
        assert "PARA" in description
        assert "graph_add_node" in description
        assert "DOC-0001" in description
        assert "para_type" in description
        assert CTX_SENTINEL in description
        assert "PARA" in expected_output and "DOC-0001" in expected_output

    def test_architect_creates_architecture_and_modules(self) -> None:
        gap = _gap(GapType.UNARCHITECTED, node_id="PROJECT-0001")
        description, expected_output = _entry(gap)
        assert "ARCHITECTURE" in description
        assert "MODULE" in description
        assert "HLR" in description
        assert "PROJECT-0001" in description
        assert CTX_SENTINEL in description
        assert "ARCHITECTURE" in expected_output and "MODULE" in expected_output

    def test_contract_creates_contract_under_the_module(self) -> None:
        gap = _gap(GapType.UNCONTRACTED, node_id="MODULE-0002")
        description, expected_output = _entry(gap)
        assert "CONTRACT" in description
        assert "MODULE-0002" in description
        assert CTX_SENTINEL in description
        assert "CONTRACT" in expected_output and "MODULE-0002" in expected_output

    def test_suite_prompt_demands_a_strategy_document(self) -> None:
        gap = _gap(GapType.UNSUITED, node_id="PROJECT-0001")
        description, expected_output = _entry(gap)
        assert "SUITE" in description
        assert "PROJECT-0001" in description
        assert "## Scope" in description
        assert "## Approach" in description
        assert "## Tools" in description
        assert "## Entry / Exit Criteria" in description
        assert CTX_SENTINEL in description
        assert "SUITE" in expected_output and "PROJECT-0001" in expected_output

    def test_test_hlr_embeds_suite_id_and_traces_the_requirement(self) -> None:
        gap = _gap(GapType.UNTESTED_HLR, node_id="HLR-0005")
        description, expected_output = _entry(gap, suite_id="SUITE-0009")
        assert "CASE_HLR" in description
        assert "SUITE ID = 'SUITE-0009'" in description
        assert "parent_id = 'SUITE-0009'" in description
        assert "trace_to = ['HLR-0005']" in description
        assert CTX_SENTINEL in description
        assert "HLR-0005" in expected_output

    def test_test_hlr_without_suite_omits_the_suite_id_line(self) -> None:
        description, _ = _entry(_gap(GapType.UNTESTED_HLR, node_id="HLR-0005"), suite_id="")
        assert "SUITE ID" not in description

    def test_test_llr_embeds_suite_id_and_traces_the_requirement(self) -> None:
        gap = _gap(GapType.UNTESTED_LLR, node_id="LLR-0003")
        description, expected_output = _entry(gap, suite_id="SUITE-0009")
        assert "CASE_LLR" in description
        assert "SUITE ID = 'SUITE-0009'" in description
        assert "parent_id = 'SUITE-0009'" in description
        assert "trace_to = ['LLR-0003']" in description
        assert CTX_SENTINEL in description
        assert "LLR-0003" in expected_output
