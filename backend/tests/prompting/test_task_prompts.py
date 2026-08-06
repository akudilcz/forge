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
from backend.prompting.builder import build_task_description
from backend.prompting.task_prompts import build_descriptions

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
        assert isinstance(description, str)
        assert description.strip()
        assert isinstance(expected_output, str)
        assert expected_output.strip()
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
        assert "PARA" in expected_output
        assert "DOC-0001" in expected_output

    def test_architect_creates_architecture_and_modules(self) -> None:
        gap = _gap(GapType.UNARCHITECTED, node_id="PROJECT-0001")
        description, expected_output = _entry(gap)
        assert "ARCHITECTURE" in description
        assert "MODULE" in description
        assert "HLR" in description
        assert "PROJECT-0001" in description
        assert CTX_SENTINEL in description
        assert "ARCHITECTURE" in expected_output
        assert "MODULE" in expected_output

    def test_architect_emits_module_allocations_at_creation(self) -> None:
        """U7: allocation is an output of architecture authoring — each MODULE
        is written WITH trace_to of the HLRs it covers, every HLR lands in
        exactly one MODULE's trace_to, and phase 5 only verifies."""
        gap = _gap(GapType.UNARCHITECTED, node_id="PROJECT-0001")
        description, _ = _entry(gap)
        assert "trace_to: list the HLR node_ids this module covers" in description
        assert "exactly ONE module" in description
        assert "no overlap, no omissions" in description
        # Phase 5 is verification + residual-only; authoring must not defer
        # allocation to it (Twin Peaks: req/arch co-evolve here, in phase 4).
        assert "Phase 5 does NOT author" in description
        assert "only VERIFIES" in description

    def test_contract_creates_contract_under_the_module(self) -> None:
        gap = _gap(GapType.UNCONTRACTED, node_id="MODULE-0002")
        description, expected_output = _entry(gap)
        assert "CONTRACT" in description
        assert "MODULE-0002" in description
        assert CTX_SENTINEL in description
        assert "CONTRACT" in expected_output
        assert "MODULE-0002" in expected_output

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
        assert "SUITE" in expected_output
        assert "PROJECT-0001" in expected_output

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


class TestStaleNodePrompt:
    """STALE_NODE repairs must need zero graph_read round-trips (specs/13):
    the prompt carries the staleness reason, points at the node + parent
    content already in the packed context, and names graph_refresh_provenance
    for the "still valid" outcome."""

    def _stale_gap(self) -> Gap:
        return _gap(GapType.STALE_NODE, context={"parent_id": "PARA-0007"})

    def test_prompt_states_the_staleness_reason(self) -> None:
        gap = self._stale_gap()
        description, _ = _entry(gap)
        assert gap.description in description

    def test_prompt_names_the_parent_node(self) -> None:
        description, _ = _entry(self._stale_gap())
        assert "PARA-0007" in description

    def test_prompt_mentions_refresh_provenance_tool(self) -> None:
        description, _ = _entry(self._stale_gap())
        assert f"graph_refresh_provenance(node_id={NODE_ID})" in description

    def test_with_context_prompt_forbids_redundant_reads(self) -> None:
        """Context carries node + parent content — the agent must not re-read."""
        description, _ = _entry(self._stale_gap())
        assert CTX_SENTINEL in description
        assert "do NOT call graph_read" in description

    def test_without_context_prompt_instructs_reads(self) -> None:
        """No packed context is stated loudly: explicit read steps, no silence."""
        description, _ = _entry(self._stale_gap(), ctx="")
        assert "graph_read" in description
        assert "do NOT call graph_read" not in description


# ── Spec-fidelity prompt pins ────────────────────────────────────────────────
#
# Live trace (topological_sort e2e, oracle 47/54): §-level normative facts
# carried only in an API-signature code block never reached HLR/LLR/CASE/TEST
# (exception base class, `find_cycle -> ... | None`, tie_breaker arity), and
# ordering/tie-break CASEs used degenerate edge-free graphs a wrong
# implementation passes. These tests pin the prompt text that closes each
# fidelity hole; drop a pin only when the design material changes with it.


class TestSpecFidelityPromptPins:
    """Phase 2/3 and 9/10 prompts name the must-capture contract categories."""

    MUST_CAPTURE_PHRASES = (
        "Exception contracts",
        "base class",
        "Return-value contracts",
        "None",
        "tie-break",
        "callable",
        "arity",
    )

    CASE_ENCODING_PHRASES = (
        "base class",
        "is None",
        "DISCRIMINATING",
        "exact output sequence",
        "arity",
    )

    def test_doc_chunk_marks_signature_code_blocks_normative(self) -> None:
        description, _ = _entry(_gap(GapType.UNCHUNKED_DOCUMENT, node_id="DOC-0001"))
        assert "code block" in description
        assert "NORMATIVE" in description

    def test_para_hlr_lists_must_capture_categories(self) -> None:
        description, _ = _entry(_gap(GapType.UNCOVERED_PARA, node_id="PARA-0027"))
        for phrase in self.MUST_CAPTURE_PHRASES:
            assert phrase in description, phrase

    def test_batch_phase3_lists_must_capture_categories(self) -> None:
        from backend.prompting.batch_prompts import build_batch_phase3_prompt

        prompt = build_batch_phase3_prompt(
            [{"node_id": "PARA-0027", "title": "Public API", "content": "class E(ValueError)"}],
            [],
            [],
        )
        for phrase in self.MUST_CAPTURE_PHRASES:
            assert phrase in prompt, phrase

    def test_batch_phase10_requires_discriminating_cases(self) -> None:
        from backend.prompting.batch_prompts import build_batch_phase10_prompt

        prompt = build_batch_phase10_prompt(
            [{"node_id": "HLR-0007", "title": "Tie-break order", "content": "shall order"}],
            [],
            {"node_id": "SUITE-0001", "content": "strategy"},
            [],
            [],
        )
        for phrase in self.CASE_ENCODING_PHRASES:
            assert phrase in prompt, phrase

    def test_test_hlr_requires_contract_encoding(self) -> None:
        description, _ = _entry(_gap(GapType.UNTESTED_HLR, node_id="HLR-0005"))
        for phrase in self.CASE_ENCODING_PHRASES:
            assert phrase in description, phrase

    def test_test_llr_requires_contract_encoding(self) -> None:
        description, _ = _entry(_gap(GapType.UNTESTED_LLR, node_id="LLR-0003"))
        for phrase in self.CASE_ENCODING_PHRASES:
            assert phrase in description, phrase

    def test_contract_prompt_requires_structured_public_api(self) -> None:
        """Phase 6 contracts carry properties.public_api (specs/13).

        Live trace (merge_sort, oracle 1/24): the whitepaper API never
        reached the workspace — nothing machine-checkable pinned the
        required module/symbol names, so codegen invented its own layout.
        """
        description, _ = _entry(_gap(GapType.UNCONTRACTED, node_id="MODULE-0001"))
        for phrase in (
            "public_api",
            '"module"',
            '"symbol"',
            '"kind"',
            '"signature"',
            "transcribe",
            "exactly",
        ):
            assert phrase in description, phrase

    def test_contract_prompt_requires_prohibited_constructs(self) -> None:
        """Whitepaper implementation bans become prohibited_constructs.

        Live trace (expression_evaluator): generated code delegated to
        compile() despite the whitepaper's explicit §12 ban.
        """
        description, _ = _entry(_gap(GapType.UNCONTRACTED, node_id="MODULE-0001"))
        for phrase in (
            "prohibited_constructs",
            '"construct"',
            '"rationale"',
            "must not use",
            "omit",
        ):
            assert phrase in description, phrase


# ── U2 CONTRACT records — phase 6 transcription + phase 10 consumption ──────


class TestContractRecordPromptPins:
    """Phase 6 transcribes obligations; phase 10 enumerates them as cases."""

    OBLIGATION_FIELD_PHRASES = (
        '"raises"',
        '"cls"',
        '"base"',
        '"when"',
        '"preconditions"',
        '"postconditions"',
        '"invariants"',
    )

    ENUMERATION_PHRASES = (
        "per raises entry",
        "per stated postcondition",
        "If",
    )

    def test_contract_prompt_requires_obligation_fields(self) -> None:
        description, _ = _entry(_gap(GapType.UNCONTRACTED, node_id="MODULE-0001"))
        for phrase in self.OBLIGATION_FIELD_PHRASES:
            assert phrase in description, phrase
        assert "verbatim" in description

    def test_contract_prompt_lists_must_capture_categories(self) -> None:
        description, _ = _entry(_gap(GapType.UNCONTRACTED, node_id="MODULE-0001"))
        for phrase in TestSpecFidelityPromptPins.MUST_CAPTURE_PHRASES:
            assert phrase in description, phrase

    def test_contract_prompt_states_dividing_rule(self) -> None:
        """Anything expressible as pre/post/raises/invariant is contract
        material; DESIGN holds only private structure + algorithm choice."""
        description, _ = _entry(_gap(GapType.UNCONTRACTED, node_id="MODULE-0001"))
        assert "DIVIDING RULE" in description
        assert "private structure" in description
        assert "algorithm choice" in description

    def test_test_hlr_requires_obligation_enumeration(self) -> None:
        description, _ = _entry(_gap(GapType.UNTESTED_HLR, node_id="HLR-0005"))
        for phrase in self.ENUMERATION_PHRASES:
            assert phrase in description, phrase

    def test_test_llr_requires_obligation_enumeration(self) -> None:
        description, _ = _entry(_gap(GapType.UNTESTED_LLR, node_id="LLR-0003"))
        for phrase in self.ENUMERATION_PHRASES:
            assert phrase in description, phrase

    def test_batch_phase10_receives_contract_records(self) -> None:
        from backend.prompting.batch_prompts import build_batch_phase10_prompt

        prompt = build_batch_phase10_prompt(
            [{"node_id": "HLR-0007", "title": "Cycle error", "content": "shall raise"}],
            [],
            {"node_id": "SUITE-0001", "content": "strategy"},
            [],
            [{
                "node_id": "CONTRACT-0001",
                "module_id": "MODULE-0001",
                "public_api": [{
                    "module": "toposort", "symbol": "find_cycle",
                    "kind": "function",
                    "signature": "def find_cycle(graph) -> list",
                    "raises": [{
                        "cls": "CyclicGraphError", "base": "ValueError",
                        "when": "the graph is cyclic",
                    }],
                }],
            }],
        )
        assert "CONTRACT RECORDS" in prompt
        assert "CyclicGraphError" in prompt
        for phrase in self.ENUMERATION_PHRASES:
            assert phrase in prompt, phrase

    def test_batch_phase10_without_contract_records_omits_block(self) -> None:
        from backend.prompting.batch_prompts import build_batch_phase10_prompt

        prompt = build_batch_phase10_prompt(
            [{"node_id": "HLR-0007", "title": "Cycle error", "content": "shall raise"}],
            [], None, [], [],
        )
        # The encoding rules always mention the block by name; the block
        # itself (with its header) must be absent when no records exist.
        assert "CONTRACT RECORDS — for the requirement's module" not in prompt


# ── U6: _para_hlr becomes cover-or-classify ──────────────────────────────────


def test_para_hlr_offers_classify_route_with_reason_kinds() -> None:
    description, _ = _entry(_gap(GapType.UNCOVERED_PARA, node_id="PARA-0027"))
    assert "CLASSIFY" in description
    assert "graph_update_node" in description
    assert '"non_normative": true' in description
    assert "non_normative_rationale" in description
    for kind in (
        "background/context", "duplicate-of-", "example/illustration",
        "meta/document-structure",
    ):
        assert kind in description, kind


def test_para_hlr_warns_against_near_duplicate_hlrs() -> None:
    description, _ = _entry(_gap(GapType.UNCOVERED_PARA, node_id="PARA-0027"))
    assert "near-duplicate" in description
    assert "defect" in description.lower()
    assert "duplicate-of-<PARA-id>" in description
