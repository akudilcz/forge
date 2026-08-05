"""Tests for backend.agents.gap_prompts default resolution."""

from __future__ import annotations

from unittest.mock import patch

from backend.agents.gap_prompts import (
    _GAP_TEMPLATE_MAP,
    get_default_gap_prompt,
    has_default_gap_prompt,
)


def test_has_default_gap_prompt_known_type() -> None:
    assert has_default_gap_prompt("UNARCHITECTED") is True


def test_has_default_gap_prompt_unknown_type() -> None:
    assert has_default_gap_prompt("TOTALLY_MADE_UP_GAP") is False


def test_get_default_gap_prompt_renders_template() -> None:
    with patch(
        "backend.agents.gap_prompts.render",
        return_value="rendered prompt body",
    ) as mock_render:
        out = get_default_gap_prompt("UNARCHITECTED")
    assert out == "rendered prompt body"
    mock_render.assert_called_once_with("gaps/unarchitected.j2")


def test_get_default_gap_prompt_unknown_returns_none() -> None:
    assert get_default_gap_prompt("NOT_A_GAP") is None


def test_gap_template_map_covers_core_structural_gaps() -> None:
    """Every core structural gap has a template entry."""
    core = {
        "UNCHUNKED_DOCUMENT", "UNCOVERED_PARA", "UNARCHITECTED",
        "UNMODULARISED", "UNCONTRACTED", "UNREFINED_HLR",
        "UNDESIGNED", "UNSUITED", "UNTESTED_HLR", "UNTESTED_LLR",
    }
    assert core.issubset(set(_GAP_TEMPLATE_MAP))
