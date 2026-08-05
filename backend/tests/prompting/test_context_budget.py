"""Tests for the priority-aware token-budget context packer."""

from __future__ import annotations

from backend.prompting.context_budget import (
    P_ANCESTOR_CHAIN,
    P_BACKGROUND,
    P_LANDSCAPE,
    P_TRACE_TO,
    Section,
    count_tokens,
    pack,
)


def test_empty_sections_returns_empty_string() -> None:
    assert pack([]) == ""


def test_sections_within_budget_preserved_in_full() -> None:
    sections = [
        Section(P_TRACE_TO, "a", "alpha " * 10),
        Section(P_ANCESTOR_CHAIN, "b", "bravo " * 10),
    ]
    out = pack(sections, budget_tokens=10_000)
    assert "alpha" in out
    assert "bravo" in out
    # Every word preserved — no mid-string truncation.
    assert out.count("alpha") == 10
    assert out.count("bravo") == 10


def test_over_budget_drops_lowest_priority_whole_section() -> None:
    """When over budget, whole sections disappear — none are sliced."""
    high = "high priority content " * 50
    low = "low priority content " * 50
    sections = [
        Section(P_TRACE_TO, "high", high),
        Section(P_BACKGROUND, "low", low),
    ]
    tiny_budget = count_tokens(high) + 2  # fits one but not both
    out = pack(sections, budget_tokens=tiny_budget)
    assert "high priority" in out
    assert "low priority" not in out
    # The high-priority section appears in full — not clipped.
    assert out.count("high priority content") == 50


def test_drops_preserve_emission_order_among_kept() -> None:
    """After dropping, kept sections still appear in insertion order."""
    s1 = "first section"
    s2 = "dropped middle"
    s3 = "third section"
    sections = [
        Section(P_TRACE_TO, "s1", s1),
        Section(P_BACKGROUND, "s2", s2),
        Section(P_TRACE_TO, "s3", s3),
    ]
    budget = count_tokens(s1) + count_tokens(s3) + 4  # excludes s2
    out = pack(sections, budget_tokens=budget)
    assert "dropped" not in out
    assert out.index("first") < out.index("third")


def test_count_tokens_nonzero_for_nonempty() -> None:
    assert count_tokens("") == 0
    assert count_tokens("hello world") > 0


def test_equal_priorities_drop_in_insertion_order() -> None:
    """Tie-break: lowest-priority section that appears first is dropped."""
    sections = [
        Section(P_BACKGROUND, "first_low", "x " * 50),
        Section(P_LANDSCAPE, "mid", "y " * 50),
        Section(P_BACKGROUND, "second_low", "z " * 50),
    ]
    tiny = count_tokens("y " * 50) + count_tokens("z " * 50) + 4
    out = pack(sections, budget_tokens=tiny)
    # Dropped first_low (priority == BACKGROUND, first match); kept mid + second_low
    assert "x " * 5 not in out
    assert "y " in out
    assert "z " in out
