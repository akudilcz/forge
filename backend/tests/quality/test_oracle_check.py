"""U9 — independent CASE-oracle validation (specs/13 §Oracle validation).

An independent LLM judge validates each CASE against its traced requirement
and the owning module's CONTRACT record on three axes:

  OUTCOME       — the expected outcome actually follows from the requirement
                  (not a plausible-but-wrong oracle);
  CONTRACT      — contracted exception/return semantics are encoded where the
                  record states them;
  DISCRIMINATES — the case names a real discriminating input (the wrong
                  implementation it kills), not boilerplate.

A missing verdict is never a pass (UnjudgedQualityError after one retry);
FAIL emits an INCONSISTENT_CONTENT repair gap on the CASE; PASS verdicts are
cacheable per (node_id, content-hash).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from backend.analysis.gaps import GapType
from backend.quality.combined_check import UnjudgedQualityError
from backend.quality.oracle_check import (
    _SYSTEM_PROMPT,
    ORACLE_AXES,
    OracleItem,
    collect_oracle_items,
    create_oracle_checker,
    oracle_pass_key,
)


class FakeLLM:
    """Scripted LLM: returns queued responses in order, records prompts."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[list[Any]] = []

    async def ainvoke(self, messages: list[Any]) -> Any:
        self.calls.append(messages)
        return SimpleNamespace(content=self._responses.pop(0))


def _item(node_id: str = "CASE_HLR-0001") -> OracleItem:
    return OracleItem(
        node_id=node_id,
        case_content=(
            "Given an empty input file, when run() is called, then it raises "
            "ParseError (caught by except ValueError)."
        ),
        requirement_block=(
            "[HLR-0001] If the input file is empty, then the system shall "
            "raise ParseError."
        ),
        contract_block=(
            '[CONTRACT-0001] {"symbol": "run", "raises": '
            '[{"cls": "ParseError", "base": "ValueError", "when": "empty input"}]}'
        ),
    )


# ── Judge prompt pins — the three oracle checks ──────────────────────────────


class TestOracleJudgePrompt:
    def test_axes_are_outcome_contract_discriminates(self) -> None:
        assert ORACLE_AXES == ("OUTCOME", "CONTRACT", "DISCRIMINATES")

    def test_prompt_demands_outcome_follows_from_requirement(self) -> None:
        assert "OUTCOME" in _SYSTEM_PROMPT
        assert "follow" in _SYSTEM_PROMPT.lower()
        assert "plausible" in _SYSTEM_PROMPT.lower()

    def test_prompt_demands_contract_exception_and_return_semantics(self) -> None:
        assert "CONTRACT" in _SYSTEM_PROMPT
        assert "exception" in _SYSTEM_PROMPT.lower()
        assert "return" in _SYSTEM_PROMPT.lower()

    def test_prompt_demands_a_real_discriminating_input(self) -> None:
        assert "DISCRIMINATES" in _SYSTEM_PROMPT
        assert "discriminating" in _SYSTEM_PROMPT.lower()
        assert "boilerplate" in _SYSTEM_PROMPT.lower()

    def test_prompt_forbids_scoring_silence_as_pass(self) -> None:
        assert "EVERY" in _SYSTEM_PROMPT


# ── Verdict parsing and gap emission ─────────────────────────────────────────


class TestOracleVerdicts:
    @pytest.mark.asyncio
    async def test_all_pass_returns_no_gaps(self) -> None:
        llm = FakeLLM(
            ["CASE_HLR-0001: OUTCOME=PASS CONTRACT=PASS DISCRIMINATES=PASS"]
        )
        check = create_oracle_checker(llm)
        assert await check([_item()]) == []
        assert len(llm.calls) == 1

    @pytest.mark.asyncio
    async def test_judge_receives_case_requirement_and_contract(self) -> None:
        llm = FakeLLM(
            ["CASE_HLR-0001: OUTCOME=PASS CONTRACT=PASS DISCRIMINATES=PASS"]
        )
        check = create_oracle_checker(llm)
        await check([_item()])
        human = llm.calls[0][-1].content
        assert "CASE_HLR-0001" in human
        assert "HLR-0001" in human
        assert "ParseError" in human
        assert "CONTRACT-0001" in human

    @pytest.mark.asyncio
    async def test_failed_axis_emits_inconsistent_content_gap_on_the_case(
        self,
    ) -> None:
        llm = FakeLLM(
            [
                "CASE_HLR-0001: OUTCOME=FAIL(asserts sorted output the "
                "requirement never states) CONTRACT=PASS "
                "DISCRIMINATES=FAIL(no concrete input named)"
            ]
        )
        check = create_oracle_checker(llm)
        gaps = await check([_item()])

        assert len(gaps) == 1  # one repair gap per CASE, axes merged
        gap = gaps[0]
        assert gap.type is GapType.INCONSISTENT_CONTENT
        assert gap.node_id == "CASE_HLR-0001"
        failures = gap.context["oracle_failures"]
        assert {f["axis"] for f in failures} == {"OUTCOME", "DISCRIMINATES"}
        assert "sorted output" in gaps[0].description

    @pytest.mark.asyncio
    async def test_missing_verdict_is_reasked_once_then_raises(self) -> None:
        llm = FakeLLM(["", ""])  # truncated twice
        check = create_oracle_checker(llm)
        with pytest.raises(UnjudgedQualityError):
            await check([_item()])
        assert len(llm.calls) == 2

    @pytest.mark.asyncio
    async def test_retry_recovers_missing_verdict(self) -> None:
        llm = FakeLLM(
            [
                "",
                "CASE_HLR-0001: OUTCOME=PASS CONTRACT=PASS DISCRIMINATES=PASS",
            ]
        )
        check = create_oracle_checker(llm)
        assert await check([_item()]) == []

    @pytest.mark.asyncio
    async def test_hallucinated_case_id_is_ignored(self) -> None:
        llm = FakeLLM(
            [
                "CASE_HLR-0001: OUTCOME=PASS CONTRACT=PASS DISCRIMINATES=PASS\n"
                "CASE_HLR-9999: OUTCOME=FAIL(made up) CONTRACT=PASS "
                "DISCRIMINATES=PASS"
            ]
        )
        check = create_oracle_checker(llm)
        assert await check([_item()]) == []

    @pytest.mark.asyncio
    async def test_empty_item_list_makes_no_llm_call(self) -> None:
        llm = FakeLLM([])
        check = create_oracle_checker(llm)
        assert await check([]) == []
        assert llm.calls == []


# ── PASS cache key ───────────────────────────────────────────────────────────


class TestOraclePassKey:
    def test_key_is_node_id_plus_content_hash(self) -> None:
        key = oracle_pass_key(_item())
        assert key[0] == "CASE_HLR-0001"
        assert key == oracle_pass_key(_item())

    def test_key_rotates_when_case_content_changes(self) -> None:
        a = _item()
        b = OracleItem(
            node_id=a.node_id,
            case_content=a.case_content + " tweaked",
            requirement_block=a.requirement_block,
            contract_block=a.contract_block,
        )
        assert oracle_pass_key(a) != oracle_pass_key(b)

    def test_key_rotates_when_requirement_changes(self) -> None:
        a = _item()
        b = OracleItem(
            node_id=a.node_id,
            case_content=a.case_content,
            requirement_block="[HLR-0001] The system shall do something else.",
            contract_block=a.contract_block,
        )
        assert oracle_pass_key(a) != oracle_pass_key(b)


# ── Item collection: traced requirement + owning module's CONTRACT ───────────


def _node(
    node_id: str,
    node_type: str,
    *,
    content: str = "",
    parent_id: str | None = None,
    trace_to: list[str] | None = None,
    properties: dict[str, Any] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        node_id=node_id,
        node_type=node_type,
        content=content,
        parent_id=parent_id,
        trace_to=trace_to or [],
        properties=properties or {},
    )


class FakeGraph:
    def __init__(self, nodes: list[SimpleNamespace]) -> None:
        self._nodes = {n.node_id: n for n in nodes}

    def all_nodes(self) -> list[SimpleNamespace]:
        return list(self._nodes.values())

    def node_sync(self, node_id: str) -> SimpleNamespace | None:
        return self._nodes.get(node_id)


def _graph_nodes() -> list[SimpleNamespace]:
    api = [{"module": "engine", "symbol": "run", "kind": "function",
            "signature": "def run(path: str) -> Report",
            "raises": [{"cls": "ParseError", "base": "ValueError",
                        "when": "input is malformed"}]}]
    return [
        _node("MODULE-0001", "MODULE", trace_to=["HLR-0001"]),
        _node("CONTRACT-0001", "CONTRACT", parent_id="MODULE-0001",
              content="run(path) -> Report", properties={"public_api": api}),
        _node("HLR-0001", "HLR", content="The system shall parse input files."),
        _node("LLR-0001", "LLR", parent_id="HLR-0001", trace_to=["HLR-0001"],
              content="The system shall parse each row into a record."),
        _node("CASE_HLR-0001", "CASE_HLR", parent_id="SUITE-0001",
              trace_to=["HLR-0001"],
              content="Given a file, when parsed, then records are returned."),
        _node("CASE_LLR-0001", "CASE_LLR", parent_id="SUITE-0001",
              trace_to=["LLR-0001"],
              content="Given a row, when parsed, then one record is returned."),
    ]


class TestCollectOracleItems:
    def test_items_carry_requirement_and_owning_module_contract(self) -> None:
        items = collect_oracle_items(FakeGraph(_graph_nodes()))
        by_id = {i.node_id: i for i in items}
        assert set(by_id) == {"CASE_HLR-0001", "CASE_LLR-0001"}

        hlr_item = by_id["CASE_HLR-0001"]
        assert "HLR-0001" in hlr_item.requirement_block
        assert "parse input files" in hlr_item.requirement_block
        assert "ParseError" in hlr_item.contract_block

        # CASE_LLR resolves its module through LLR → parent HLR → MODULE.
        llr_item = by_id["CASE_LLR-0001"]
        assert "LLR-0001" in llr_item.requirement_block
        assert "ParseError" in llr_item.contract_block

    def test_case_with_no_resolvable_requirement_is_skipped(self) -> None:
        nodes = _graph_nodes()
        nodes.append(
            _node("CASE_HLR-0002", "CASE_HLR", trace_to=["HLR-9999"],
                  content="Given something, then something.")
        )
        items = collect_oracle_items(FakeGraph(nodes))
        assert "CASE_HLR-0002" not in {i.node_id for i in items}

    def test_missing_contract_yields_explicit_no_record_block(self) -> None:
        nodes = [n for n in _graph_nodes() if n.node_type != "CONTRACT"]
        items = collect_oracle_items(FakeGraph(nodes))
        assert items, "cases must still be judged without a CONTRACT record"
        assert all("no contract record" in i.contract_block.lower() for i in items)
