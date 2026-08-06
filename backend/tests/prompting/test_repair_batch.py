"""Pins for the batched micro-repair prompt builder (specs/12 §7.4).

One structured LLM call repairs N same-family title/wording gaps. The
payload must carry, per node: the node id, its full (capped) content, and
the violated invariant message — that is what lets a single call replace N
per-gap dispatches. The parser must never invent a fix: a dropped or
garbled node line is reported as missing so its gap stays open for the
normal per-gap dispatch path.
"""

from __future__ import annotations

from backend.analysis.gaps import GapType
from backend.prompting.repair_batch import (
    BATCHABLE_REPAIR_TYPES,
    MIN_BATCH_SIZE,
    TITLE_FAMILY,
    TITLE_REPAIR_SYSTEM_PROMPT,
    WORDING_FAMILY,
    WORDING_REPAIR_SYSTEM_PROMPT,
    RepairEntry,
    build_title_repair_payload,
    build_wording_repair_payload,
    parse_repair_response,
)


def _entry(node_id: str, content: str, violation: str) -> RepairEntry:
    return RepairEntry(
        node_id=node_id,
        node_type="HLR",
        title="Handle Cases",
        content=content,
        violation=violation,
        sibling_titles=("Sort Input List", "Reject Boolean Values"),
        parent_title="List Processing Module",
    )


# ── Family / threshold pins ──────────────────────────────────────────────────


class TestFamilyPins:
    def test_title_family_members(self) -> None:
        assert TITLE_FAMILY == frozenset(
            {
                GapType.VAGUE_TITLE,
                GapType.STALE_TITLE,
                GapType.SIBLING_TITLE_DUPLICATE,
                GapType.TITLE_COLLIDES_WITH_PARENT,
            }
        )

    def test_title_collides_with_parent_is_batchable(self) -> None:
        """1,684 per-gap calls across builds were pure title rewrites already
        gated by write-time invariants — they join the batch family."""
        assert GapType.TITLE_COLLIDES_WITH_PARENT in BATCHABLE_REPAIR_TYPES

    def test_wording_family_members(self) -> None:
        assert WORDING_FAMILY == frozenset(
            {GapType.MALFORMED_REQUIREMENT, GapType.NON_EARS_REQUIREMENT}
        )

    def test_families_are_disjoint_and_union_is_batchable(self) -> None:
        assert not (TITLE_FAMILY & WORDING_FAMILY)
        assert BATCHABLE_REPAIR_TYPES == TITLE_FAMILY | WORDING_FAMILY

    def test_min_batch_size_is_three(self) -> None:
        assert MIN_BATCH_SIZE == 3


# ── Payload builders ─────────────────────────────────────────────────────────


class TestTitlePayload:
    def test_payload_carries_id_content_violation_and_siblings(self) -> None:
        payload = build_title_repair_payload(
            [_entry("HLR-0001", "The system shall sort the list.", "title is vague")]
        )
        assert "HLR-0001" in payload
        assert "The system shall sort the list." in payload
        assert "title is vague" in payload
        assert "Handle Cases" in payload  # current title
        assert "Sort Input List" in payload  # sibling titles to stay distinct from

    def test_payload_carries_parent_title_to_avoid_recollision(self) -> None:
        """The rewrite must know the parent title — otherwise a 'fix' can
        re-trigger TITLE_COLLIDES_WITH_PARENT."""
        payload = build_title_repair_payload(
            [_entry("HLR-0001", "The system shall sort the list.", "title duplicates parent")]
        )
        assert "List Processing Module" in payload
        assert "parent_title (must stay distinct from)" in payload

    def test_payload_without_parent_says_none(self) -> None:
        entry = RepairEntry(
            node_id="PROJ-0001",
            node_type="PROJECT",
            title="Root",
            content="c",
            violation="v",
            sibling_titles=(),
            parent_title="",
        )
        payload = build_title_repair_payload([entry])
        assert "parent_title (must stay distinct from): (none)" in payload

    def test_system_prompt_demands_distinct_from_parent(self) -> None:
        assert "parent title" in TITLE_REPAIR_SYSTEM_PROMPT

    def test_one_block_per_entry(self) -> None:
        payload = build_title_repair_payload(
            [
                _entry("HLR-0001", "content one", "v1"),
                _entry("HLR-0002", "content two", "v2"),
            ]
        )
        assert payload.index("HLR-0001") < payload.index("HLR-0002")
        assert "content two" in payload

    def test_system_prompt_pins_output_format(self) -> None:
        assert "one line per node" in TITLE_REPAIR_SYSTEM_PROMPT
        assert "<NODE_ID>: " in TITLE_REPAIR_SYSTEM_PROMPT


class TestWordingPayload:
    def test_payload_carries_id_content_and_violation(self) -> None:
        payload = build_wording_repair_payload(
            [_entry("LLR-0009", "Sorting must work somehow.", "must start with 'The system shall '")]
        )
        assert "LLR-0009" in payload
        assert "Sorting must work somehow." in payload
        assert "must start with 'The system shall '" in payload

    def test_system_prompt_demands_ears_form(self) -> None:
        assert "The <system> shall <response>." in WORDING_REPAIR_SYSTEM_PROMPT
        assert "If <condition>, then the <system> shall <response>." in WORDING_REPAIR_SYSTEM_PROMPT
        assert "one line per node" in WORDING_REPAIR_SYSTEM_PROMPT

    def test_long_content_is_capped_not_unbounded(self) -> None:
        long_content = "x" * 50_000
        payload = build_wording_repair_payload([_entry("HLR-0001", long_content, "v")])
        assert len(payload) < 10_000


# ── Response parser ──────────────────────────────────────────────────────────


class TestParseRepairResponse:
    def test_happy_path_parses_every_expected_node(self) -> None:
        fixes, missing = parse_repair_response(
            "HLR-0001: The system shall sort the input list.\n"
            "HLR-0002: The system shall reject boolean values.",
            ["HLR-0001", "HLR-0002"],
        )
        assert fixes == {
            "HLR-0001": "The system shall sort the input list.",
            "HLR-0002": "The system shall reject boolean values.",
        }
        assert missing == []

    def test_dropped_node_is_reported_missing_never_defaulted(self) -> None:
        fixes, missing = parse_repair_response(
            "HLR-0001: The system shall sort the input list.",
            ["HLR-0001", "HLR-0002", "HLR-0003"],
        )
        assert set(fixes) == {"HLR-0001"}
        assert missing == ["HLR-0002", "HLR-0003"]

    def test_unknown_ids_and_preamble_lines_are_ignored(self) -> None:
        fixes, missing = parse_repair_response(
            "Here are the fixes:\n"
            "HLR-9999: not asked for\n"
            "HLR-0001: Sort Input List",
            ["HLR-0001"],
        )
        assert fixes == {"HLR-0001": "Sort Input List"}
        assert missing == []

    def test_empty_value_counts_as_missing(self) -> None:
        fixes, missing = parse_repair_response("HLR-0001:   ", ["HLR-0001"])
        assert fixes == {}
        assert missing == ["HLR-0001"]

    def test_empty_response_reports_all_missing(self) -> None:
        fixes, missing = parse_repair_response("", ["HLR-0001", "HLR-0002"])
        assert fixes == {}
        assert missing == ["HLR-0001", "HLR-0002"]
