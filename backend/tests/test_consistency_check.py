"""Tests for LLM-based consistency checkers (plain text, no structured output)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.analysis.gaps import GapPriority, GapType
from backend.crew.consistency_check import (
    _parse_conformance,
    _parse_contradiction,
    _parse_decomposition,
    _reason_after_dash,
    create_architecture_conformance_checker,
    create_decomposition_checker,
    create_requirement_consistency_checker,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_llm(response_text: str) -> MagicMock:
    llm = MagicMock()
    resp = MagicMock()
    resp.content = response_text
    llm.ainvoke = AsyncMock(return_value=resp)
    return llm


# ── Requirement consistency checker ─────────────────────────────────────────


class TestRequirementConsistencyChecker:
    @pytest.mark.asyncio
    async def test_pass_produces_no_gaps(self) -> None:
        llm = _make_llm("CONTRADICTION: PASS")
        checker = create_requirement_consistency_checker(llm)
        gaps = await checker("HLR-001", "The system shall accept CSV.", "HLR-002: ...")
        assert gaps == []

    @pytest.mark.asyncio
    async def test_fail_produces_contradiction_gap(self) -> None:
        llm = _make_llm("CONTRADICTION: FAIL - conflicts with HLR-002 on input format")
        checker = create_requirement_consistency_checker(llm)
        gaps = await checker("HLR-001", "Accept all formats.", "HLR-002: reject non-CSV")
        assert len(gaps) == 1
        assert gaps[0].type == GapType.CONTRADICTORY_REQUIREMENTS
        assert gaps[0].priority == GapPriority.MAINTENANCE
        assert gaps[0].node_id == "HLR-001"
        assert "reasoning" in gaps[0].context

    @pytest.mark.asyncio
    async def test_empty_content_skips_llm(self) -> None:
        llm = _make_llm("should not be called")
        checker = create_requirement_consistency_checker(llm)
        gaps = await checker("HLR-001", "   ", "siblings")
        assert gaps == []
        llm.ainvoke.assert_not_awaited()


# ── Decomposition checker ───────────────────────────────────────────────────


class TestDecompositionChecker:
    @pytest.mark.asyncio
    async def test_pass_produces_no_gaps(self) -> None:
        llm = _make_llm("DECOMPOSITION: PASS")
        checker = create_decomposition_checker(llm)
        gaps = await checker("HLR-010", "HLR text", "LLR-1: ...", "contract text")
        assert gaps == []

    @pytest.mark.asyncio
    async def test_fail_produces_incomplete_gap(self) -> None:
        llm = _make_llm("DECOMPOSITION: FAIL - CONTRACT defines 3 endpoints but LLRs only cover 2")
        checker = create_decomposition_checker(llm)
        gaps = await checker("HLR-010", "Handle all API endpoints", "LLR-1: ...", "3 endpoints")
        assert len(gaps) == 1
        assert gaps[0].type == GapType.INCOMPLETE_DECOMPOSITION
        assert gaps[0].priority == GapPriority.MAINTENANCE
        assert gaps[0].node_id == "HLR-010"

    @pytest.mark.asyncio
    async def test_empty_hlr_skips_llm(self) -> None:
        llm = _make_llm("should not be called")
        checker = create_decomposition_checker(llm)
        gaps = await checker("HLR-010", "", "LLRs", "contract")
        assert gaps == []
        llm.ainvoke.assert_not_awaited()


# ── Architecture conformance checker ─────────────────────────────────────────


class TestArchitectureConformanceChecker:
    @pytest.mark.asyncio
    async def test_both_pass_no_gaps(self) -> None:
        llm = _make_llm("CONTRACT: PASS\nCOUPLING: PASS")
        checker = create_architecture_conformance_checker(llm)
        gaps = await checker("DES-001", "design text", "contract", "all modules")
        assert gaps == []

    @pytest.mark.asyncio
    async def test_contract_fail_produces_violation_gap(self) -> None:
        llm = _make_llm("CONTRACT: FAIL - uses private helper not in CONTRACT\nCOUPLING: PASS")
        checker = create_architecture_conformance_checker(llm)
        gaps = await checker("DES-001", "design text", "contract", "all modules")
        assert len(gaps) == 1
        assert gaps[0].type == GapType.CONTRACT_VIOLATION
        assert gaps[0].node_id == "DES-001"

    @pytest.mark.asyncio
    async def test_coupling_fail_produces_coupling_gap(self) -> None:
        llm = _make_llm("CONTRACT: PASS\nCOUPLING: FAIL - references ModuleB internals")
        checker = create_architecture_conformance_checker(llm)
        gaps = await checker("DES-002", "design text", "contract", "all modules")
        assert len(gaps) == 1
        assert gaps[0].type == GapType.CROSS_MODULE_COUPLING
        assert gaps[0].node_id == "DES-002"

    @pytest.mark.asyncio
    async def test_both_fail_produces_two_gaps(self) -> None:
        llm = _make_llm(
            "CONTRACT: FAIL - ignores interface\nCOUPLING: FAIL - imports from other module"
        )
        checker = create_architecture_conformance_checker(llm)
        gaps = await checker("DES-003", "design text", "contract", "all modules")
        assert len(gaps) == 2
        types = {g.type for g in gaps}
        assert GapType.CONTRACT_VIOLATION in types
        assert GapType.CROSS_MODULE_COUPLING in types

    @pytest.mark.asyncio
    async def test_empty_design_skips_llm(self) -> None:
        llm = _make_llm("should not be called")
        checker = create_architecture_conformance_checker(llm)
        gaps = await checker("DES-004", "  ", "contract", "all modules")
        assert gaps == []
        llm.ainvoke.assert_not_awaited()


# ── Parser unit tests ────────────────────────────────────────────────────────


class TestParseContradiction:
    def test_pass(self) -> None:
        assert _parse_contradiction("N1", "content", "CONTRADICTION: PASS") == []

    def test_fail_with_reason(self) -> None:
        gaps = _parse_contradiction("N1", "content", "CONTRADICTION: FAIL - conflicts")
        assert len(gaps) == 1
        assert gaps[0].type == GapType.CONTRADICTORY_REQUIREMENTS
        assert gaps[0].context["reasoning"] == "conflicts"

    def test_fail_no_reason(self) -> None:
        gaps = _parse_contradiction("N1", "content", "CONTRADICTION: FAIL")
        assert len(gaps) == 1
        assert gaps[0].context["reasoning"] == ""


class TestParseDecomposition:
    def test_pass(self) -> None:
        assert _parse_decomposition("H1", "content", "DECOMPOSITION: PASS") == []

    def test_fail(self) -> None:
        gaps = _parse_decomposition("H1", "content", "DECOMPOSITION: FAIL - missing coverage")
        assert len(gaps) == 1
        assert gaps[0].type == GapType.INCOMPLETE_DECOMPOSITION


class TestParseConformance:
    def test_both_pass(self) -> None:
        assert _parse_conformance("D1", "c", "CONTRACT: PASS\nCOUPLING: PASS") == []

    def test_contract_only_fail(self) -> None:
        gaps = _parse_conformance("D1", "c", "CONTRACT: FAIL - bad\nCOUPLING: PASS")
        assert len(gaps) == 1
        assert gaps[0].type == GapType.CONTRACT_VIOLATION

    def test_coupling_only_fail(self) -> None:
        gaps = _parse_conformance("D1", "c", "CONTRACT: PASS\nCOUPLING: FAIL - bad")
        assert len(gaps) == 1
        assert gaps[0].type == GapType.CROSS_MODULE_COUPLING

    def test_both_fail(self) -> None:
        gaps = _parse_conformance("D1", "c", "CONTRACT: FAIL - x\nCOUPLING: FAIL - y")
        assert len(gaps) == 2


class TestReasonAfterDash:
    def test_extracts_reason(self) -> None:
        text = "CONTRADICTION: FAIL - some reason here"
        assert _reason_after_dash(text, "CONTRADICTION:") == "some reason here"

    def test_no_dash_returns_empty(self) -> None:
        text = "CONTRADICTION: FAIL"
        assert _reason_after_dash(text, "CONTRADICTION:") == ""

    def test_missing_prefix_returns_empty(self) -> None:
        assert _reason_after_dash("SOMETHING: ELSE", "CONTRADICTION:") == ""
