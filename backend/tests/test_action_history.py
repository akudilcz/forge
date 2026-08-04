"""Tests for build_task_description (action_history removed — context now via checkpointer)."""
from __future__ import annotations

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.crew.task_builder import build_task_description


def test_build_task_description_returns_description_and_expected() -> None:
    """build_task_description returns a (description, expected_output) tuple."""
    gap = Gap(
        type=GapType.UNMODULARISED,
        priority=GapPriority.DESIGN,
        node_id="HLR-0001",
        description="needs modularisation",
    )
    desc, expected = build_task_description(gap, "some context")
    assert isinstance(desc, str)
    assert isinstance(expected, str)
    assert len(desc) > 0
    assert len(expected) > 0


def test_build_task_description_includes_context() -> None:
    """Ancestor context is embedded in the description."""
    gap = Gap(
        type=GapType.UNMODULARISED,
        priority=GapPriority.DESIGN,
        node_id="HLR-0001",
        description="needs modularisation",
    )
    desc, _ = build_task_description(gap, "MODULE ALPHA context here")
    assert "MODULE ALPHA context here" in desc


def test_build_task_description_attempt_prefix() -> None:
    """Retry attempts get an ATTEMPT prefix warning."""
    gap = Gap(
        type=GapType.UNMODULARISED,
        priority=GapPriority.DESIGN,
        node_id="HLR-0001",
        description="needs modularisation",
    )
    desc, _ = build_task_description(gap, "ctx", attempt=2)
    assert "ATTEMPT 2" in desc
    assert "Call tools" in desc


def test_build_task_description_no_attempt_prefix_on_first() -> None:
    """First attempt has no ATTEMPT prefix."""
    gap = Gap(
        type=GapType.UNMODULARISED,
        priority=GapPriority.DESIGN,
        node_id="HLR-0001",
        description="needs modularisation",
    )
    desc, _ = build_task_description(gap, "ctx", attempt=1)
    assert "ATTEMPT" not in desc
