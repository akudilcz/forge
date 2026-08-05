"""Tests for the batched combined quality checker.

A missing verdict is never a pass. A node line the model garbled, or an axis
it omitted, used to default to ``(True, "")`` — a truncated batch response
scored every dropped node as clean. The checker now re-invokes the LLM once
for unjudged nodes/axes and raises ``UnjudgedQualityError`` if any remain.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.analysis.gaps import GapType
from backend.crew.combined_quality_check import (
    UnjudgedQualityError,
    _parse_verdicts,
    create_combined_quality_checker,
)


def _resp(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=text)


def _llm_seq(*texts: str) -> AsyncMock:
    llm = AsyncMock()
    llm.ainvoke = AsyncMock(side_effect=[_resp(t) for t in texts])
    return llm


@pytest.mark.asyncio
async def test_empty_items_returns_empty_without_llm_call() -> None:
    llm = AsyncMock()
    check = create_combined_quality_checker(llm)
    gaps = await check([])
    assert gaps == []
    llm.ainvoke.assert_not_called()


@pytest.mark.asyncio
async def test_batches_all_nodes_in_single_llm_call() -> None:
    llm = _llm_seq(
        "HLR-0001: ATOMIC=PASS EARS=PASS MATCH=PASS SPECIFIC=PASS\n"
        "HLR-0002: ATOMIC=FAIL(two obligations) EARS=PASS MATCH=PASS SPECIFIC=PASS\n"
        "MODULE-0001: MATCH=PASS SPECIFIC=FAIL(generic)"
    )
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
    gaps, missing = _parse_verdicts(items, text)
    types = {g.type for g in gaps}
    assert types == {
        GapType.NON_ATOMIC_REQUIREMENT,
        GapType.NON_EARS_REQUIREMENT,
        GapType.STALE_TITLE,
        GapType.VAGUE_TITLE,
    }
    assert missing == {}


def test_parse_skips_req_axes_for_non_requirement_nodes() -> None:
    items = [("MODULE-0001", "MODULE", "Misc", "Module responsibilities.")]
    text = "MODULE-0001: ATOMIC=FAIL(ignored) EARS=FAIL(ignored) MATCH=PASS SPECIFIC=FAIL(vague label)"
    gaps, missing = _parse_verdicts(items, text)
    # ATOMIC/EARS should not emit gaps for non-requirement types
    types = {g.type for g in gaps}
    assert types == {GapType.VAGUE_TITLE}
    assert missing == {}


def test_parse_tolerates_unknown_node_lines() -> None:
    items = [("HLR-0001", "HLR", "Good", "The system shall sort.")]
    text = (
        "UNKNOWN-0001: MATCH=FAIL(...)\n"  # not in items — should be ignored
        "HLR-0001: ATOMIC=PASS EARS=PASS MATCH=PASS SPECIFIC=PASS"
    )
    gaps, missing = _parse_verdicts(items, text)
    assert gaps == []
    assert missing == {}


def test_parse_reports_missing_axes_instead_of_defaulting_to_pass() -> None:
    """The rank-13 regression: an omitted axis is unjudged, not a PASS."""
    items = [("HLR-0001", "HLR", "Good", "The system shall sort.")]
    text = "HLR-0001: ATOMIC=PASS"
    gaps, missing = _parse_verdicts(items, text)
    assert gaps == []
    assert missing == {"HLR-0001": {"EARS", "MATCH", "SPECIFIC"}}


def test_parse_reports_dropped_node_as_fully_unjudged() -> None:
    """A garbled/omitted node line must not silently score as passing."""
    items = [
        ("HLR-0001", "HLR", "Good", "The system shall sort."),
        ("HLR-0002", "HLR", "Other", "The system shall merge."),
    ]
    text = "HLR-0001: ATOMIC=PASS EARS=PASS MATCH=PASS SPECIFIC=PASS"
    gaps, missing = _parse_verdicts(items, text)
    assert gaps == []
    assert missing == {"HLR-0002": {"ATOMIC", "EARS", "MATCH", "SPECIFIC"}}


def test_parse_case_insensitive() -> None:
    items = [("HLR-0001", "HLR", "Good", "The system shall sort.")]
    text = "HLR-0001: atomic=pass ears=fail(neg) match=pass specific=pass"
    gaps, missing = _parse_verdicts(items, text)
    assert len(gaps) == 1
    assert gaps[0].type == GapType.NON_EARS_REQUIREMENT
    assert missing == {}


@pytest.mark.asyncio
async def test_unjudged_nodes_trigger_exactly_one_retry() -> None:
    """Nodes/axes without a verdict are re-asked in a single follow-up call."""
    llm = _llm_seq(
        # First response drops HLR-0002 entirely.
        "HLR-0001: ATOMIC=PASS EARS=PASS MATCH=PASS SPECIFIC=PASS",
        # Retry judges the dropped node — and finds a failure.
        "HLR-0002: ATOMIC=FAIL(two obligations) EARS=PASS MATCH=PASS SPECIFIC=PASS",
    )
    items = [
        ("HLR-0001", "HLR", "Good", "The system shall sort."),
        ("HLR-0002", "HLR", "Other", "The system shall merge and validate."),
    ]
    check = create_combined_quality_checker(llm)
    gaps = await check(items)

    assert llm.ainvoke.await_count == 2
    assert [(g.node_id, g.type) for g in gaps] == [
        ("HLR-0002", GapType.NON_ATOMIC_REQUIREMENT)
    ]
    # The retry payload only contains the unjudged node.
    retry_payload = llm.ainvoke.await_args_list[1].args[0][1].content
    assert "HLR-0002" in retry_payload
    assert "HLR-0001" not in retry_payload


@pytest.mark.asyncio
async def test_retry_covers_missing_axes_of_partially_judged_node() -> None:
    llm = _llm_seq(
        "HLR-0001: ATOMIC=PASS EARS=PASS",  # MATCH/SPECIFIC missing
        "HLR-0001: ATOMIC=PASS EARS=PASS MATCH=PASS SPECIFIC=FAIL(vague)",
    )
    items = [("HLR-0001", "HLR", "Good", "The system shall sort.")]
    check = create_combined_quality_checker(llm)
    gaps = await check(items)

    assert llm.ainvoke.await_count == 2
    assert [(g.node_id, g.type) for g in gaps] == [("HLR-0001", GapType.VAGUE_TITLE)]


@pytest.mark.asyncio
async def test_retry_does_not_duplicate_round_one_verdicts() -> None:
    """Axes already judged in round one keep their verdict; a contradictory
    retry line for those axes is ignored."""
    llm = _llm_seq(
        "HLR-0001: ATOMIC=FAIL(two obligations) EARS=PASS",  # titles missing
        "HLR-0001: ATOMIC=FAIL(two obligations) EARS=PASS MATCH=PASS SPECIFIC=PASS",
    )
    items = [("HLR-0001", "HLR", "Good", "The system shall sort and merge.")]
    check = create_combined_quality_checker(llm)
    gaps = await check(items)

    atomic_gaps = [g for g in gaps if g.type == GapType.NON_ATOMIC_REQUIREMENT]
    assert len(atomic_gaps) == 1


@pytest.mark.asyncio
async def test_still_unjudged_after_retry_raises() -> None:
    """Never default to pass: an axis with no verdict after the retry is a
    loud failure, not a clean sweep."""
    llm = _llm_seq(
        "HLR-0001: ATOMIC=PASS EARS=PASS MATCH=PASS SPECIFIC=PASS",
        "",  # retry comes back empty/garbled
    )
    items = [
        ("HLR-0001", "HLR", "Good", "The system shall sort."),
        ("HLR-0002", "HLR", "Other", "The system shall merge."),
    ]
    check = create_combined_quality_checker(llm)

    with pytest.raises(UnjudgedQualityError, match="HLR-0002"):
        await check(items)
    assert llm.ainvoke.await_count == 2  # exactly one retry, no infinite loop
