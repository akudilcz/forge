"""Tests for the batched combined quality checker."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.analysis.gaps import GapType
from backend.crew.combined_quality_check import (
    _parse_verdicts,
    create_combined_quality_checker,
)


def _resp(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=text)


@pytest.mark.asyncio
async def test_empty_items_returns_empty_without_llm_call() -> None:
    llm = AsyncMock()
    check = create_combined_quality_checker(llm)
    gaps = await check([])
    assert gaps == []
    llm.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_batches_all_nodes_in_single_llm_call() -> None:
    llm = AsyncMock()
    llm.ainvoke = AsyncMock(return_value=_resp(
        "HLR-0001: ATOMIC=PASS EARS=PASS MATCH=PASS SPECIFIC=PASS\n"
        "HLR-0002: ATOMIC=FAIL(two obligations) EARS=PASS MATCH=PASS SPECIFIC=PASS\n"
        "MODULE-0001: MATCH=PASS SPECIFIC=FAIL(generic)"
    ))
    items = [
        ("HLR-0001", "HLR", "Good Title", "The system shall sort."),
        ("HLR-0002", "HLR", "Another Title", "The system shall sort and validate."),
        ("MODULE-0001", "MODULE", "Core", "Responsibilities here."),
    ]
    check = create_combined_quality_checker(llm)
    gaps = await check(items)

    assert llm.ainvoke.await_count == 1  # single batched call
    types = [(g.node_id, g.type) for g in gaps]
    assert ("HLR-0002", GapType.NON_ATOMIC_REQUIREMENT) in types
    assert ("MODULE-0001", GapType.VAGUE_TITLE) in types
    # HLR-0001 clean → no gap
    assert not any(g.node_id == "HLR-0001" for g in gaps)


def test_parse_handles_all_four_failures_for_a_requirement() -> None:
    items = [("HLR-0001", "HLR", "Handle Things", "The system shall do X and Y.")]
    text = "HLR-0001: ATOMIC=FAIL(X+Y) EARS=FAIL(negative) MATCH=FAIL(broad) SPECIFIC=FAIL(vague)"
    gaps = _parse_verdicts(items, text)
    types = {g.type for g in gaps}
    assert types == {
        GapType.NON_ATOMIC_REQUIREMENT,
        GapType.NON_EARS_REQUIREMENT,
        GapType.STALE_TITLE,
        GapType.VAGUE_TITLE,
    }


def test_parse_skips_req_axes_for_non_requirement_nodes() -> None:
    items = [("MODULE-0001", "MODULE", "Misc", "Module responsibilities.")]
    text = "MODULE-0001: ATOMIC=FAIL(ignored) EARS=FAIL(ignored) MATCH=PASS SPECIFIC=FAIL(vague label)"
    gaps = _parse_verdicts(items, text)
    # ATOMIC/EARS should not emit gaps for non-requirement types
    types = {g.type for g in gaps}
    assert types == {GapType.VAGUE_TITLE}


def test_parse_tolerates_unknown_node_lines() -> None:
    items = [("HLR-0001", "HLR", "Good", "The system shall sort.")]
    text = (
        "UNKNOWN-0001: MATCH=FAIL(...)\n"  # not in items — should be ignored
        "HLR-0001: ATOMIC=PASS EARS=PASS MATCH=PASS SPECIFIC=PASS"
    )
    gaps = _parse_verdicts(items, text)
    assert gaps == []


def test_parse_tolerates_missing_axes() -> None:
    # Missing axes default to PASS (no gap emitted).
    items = [("HLR-0001", "HLR", "Good", "The system shall sort.")]
    text = "HLR-0001: ATOMIC=PASS"
    gaps = _parse_verdicts(items, text)
    assert gaps == []


def test_parse_case_insensitive() -> None:
    items = [("HLR-0001", "HLR", "Good", "The system shall sort.")]
    text = "HLR-0001: atomic=pass ears=fail(neg) match=pass specific=pass"
    gaps = _parse_verdicts(items, text)
    assert len(gaps) == 1
    assert gaps[0].type == GapType.NON_EARS_REQUIREMENT
