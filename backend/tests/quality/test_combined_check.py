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
from backend.quality.combined_check import (
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


# ── Hallucinated node ids in judge verdicts (specs/12 §7.4) ─────────────────
#
# Forensic origin: a halt message of the shape
#   "Quality batch left 1 node(s) unjudged after retry — never defaulting to
#    pass. Unjudged: HLR-9999: ['ATOMIC']"
# where HLR-9999 exists in no graph. Unjudged accounting must be computed
# strictly as candidate-set minus judged; verdict lines for unknown ids are
# ignored with one WARN naming them; the retry only ever re-sends real
# candidates — so a hallucinated id can never appear in the error.


def _capture_emits(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str, str]]:
    from backend.server import forge_logger as fl_module

    captured: list[tuple[str, str, str]] = []

    def capture(level: str, cat: object, msg: str, *args: object, **kw: object) -> None:
        detail = " ".join(str(a) for a in args)
        captured.append((level, str(cat), f"{msg} {detail}".strip()))

    monkeypatch.setattr(fl_module.forge_logger, "emit", capture)
    return captured


def test_parse_warns_once_naming_unknown_verdict_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A verdict line for an id outside the candidate set is dropped — but
    never silently: one WARN names the hallucinated id(s)."""
    captured = _capture_emits(monkeypatch)
    items = [("HLR-0001", "HLR", "Good", "The system shall sort.")]
    text = (
        "HLR-9999: ATOMIC=FAIL(phantom) EARS=PASS MATCH=PASS SPECIFIC=PASS\n"
        "HLR-0001: ATOMIC=PASS EARS=PASS MATCH=PASS SPECIFIC=PASS"
    )
    gaps, missing = _parse_verdicts(items, text)

    assert gaps == []
    assert missing == {}
    warns = [c for c in captured if c[0] == "WARN" and "HLR-9999" in c[2]]
    assert len(warns) == 1, f"expected exactly one WARN naming HLR-9999, got {captured}"


def test_parse_does_not_warn_on_prose_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preamble/prose containing a colon is not a verdict line — no WARN."""
    captured = _capture_emits(monkeypatch)
    items = [("HLR-0001", "HLR", "Good", "The system shall sort.")]
    text = (
        "Here are the verdicts: as requested\n"
        "HLR-0001: ATOMIC=PASS EARS=PASS MATCH=PASS SPECIFIC=PASS"
    )
    gaps, missing = _parse_verdicts(items, text)

    assert gaps == []
    assert missing == {}
    assert not [c for c in captured if c[0] == "WARN"]


@pytest.mark.asyncio
async def test_unjudged_error_names_only_real_candidates() -> None:
    """Exact halt-message shape regression: even when the judge hallucinates
    HLR-9999 in both rounds, the error names only the REAL unjudged candidate
    and the retry payload contains only real candidates."""
    llm = _llm_seq(
        # Round one: judges HLR-0001, hallucinates HLR-9999, drops HLR-0002's
        # ATOMIC verdict.
        "HLR-0001: ATOMIC=PASS EARS=PASS MATCH=PASS SPECIFIC=PASS\n"
        "HLR-9999: ATOMIC=FAIL(phantom) EARS=PASS MATCH=PASS SPECIFIC=PASS\n"
        "HLR-0002: EARS=PASS MATCH=PASS SPECIFIC=PASS",
        # Retry: hallucinates again, still no ATOMIC verdict for HLR-0002.
        "HLR-9999: ATOMIC=PASS EARS=PASS MATCH=PASS SPECIFIC=PASS\n"
        "HLR-0002: EARS=PASS MATCH=PASS SPECIFIC=PASS",
    )
    items = [
        ("HLR-0001", "HLR", "Good", "The system shall sort."),
        ("HLR-0002", "HLR", "Other", "The system shall merge."),
    ]
    check = create_combined_quality_checker(llm)

    with pytest.raises(UnjudgedQualityError) as excinfo:
        await check(items)

    assert str(excinfo.value) == (
        "Quality batch left 1 node(s) unjudged after retry "
        "— never defaulting to pass. Unjudged: HLR-0002: ['ATOMIC']"
    )
    assert "HLR-9999" not in str(excinfo.value)
    assert set(excinfo.value.missing) <= {nid for nid, _, _, _ in items}
    # The retry only ever re-sends REAL candidates.
    retry_payload = llm.ainvoke.await_args_list[1].args[0][1].content
    assert "[HLR-0002]" in retry_payload
    assert "HLR-9999" not in retry_payload


# ── U3: EARS axis judges pattern CHOICE only (specs/13) ──────────────────────


def test_system_prompt_scopes_ears_axis_to_pattern_choice() -> None:
    from backend.quality.combined_check import _SYSTEM_PROMPT

    # The judge owns semantics-vs-pattern fit; surface syntax belongs to the
    # deterministic write-time classifier.
    assert "right EARS pattern" in _SYSTEM_PROMPT
    assert "If <condition>, then" in _SYSTEM_PROMPT
    assert "When <trigger>" in _SYSTEM_PROMPT
    assert "While <state>" in _SYSTEM_PROMPT
    assert "Where <feature>" in _SYSTEM_PROMPT
    assert "syntax" in _SYSTEM_PROMPT.lower()
    # The caricature is gone: the judge no longer polices the old prefix rule.
    assert '"The system shall <action>"' not in _SYSTEM_PROMPT


def test_ears_fail_description_names_expected_pattern() -> None:
    items = [
        ("HLR-0001", "HLR", "Malformed Input Error",
         "When the input is malformed, the system shall raise ValueError."),
    ]
    text = (
        "HLR-0001: ATOMIC=PASS "
        "EARS=FAIL(expected Unwanted-behaviour: If <condition>, then ...) "
        "MATCH=PASS SPECIFIC=PASS"
    )
    gaps, missing = _parse_verdicts(items, text)
    assert missing == {}
    [gap] = gaps
    assert gap.type == GapType.NON_EARS_REQUIREMENT
    # The FAIL description must surface the judge's expected pattern.
    assert "expected Unwanted-behaviour" in gap.description
