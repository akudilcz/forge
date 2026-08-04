"""Tests for the LogCategory enum and its normalisation helpers."""

from __future__ import annotations

import pytest

from backend.observability import LogCategory, normalise_category, validate_category
from backend.observability.log_record import _CATEGORY_BY_CANONICAL


def test_enum_has_no_trailing_whitespace() -> None:
    """Every category value is clean — no padding artefacts."""
    for cat in LogCategory:
        assert cat.value == cat.value.strip()
        assert cat.value == cat.value.upper()
        assert cat.value, f"Empty category on {cat.name}"


def test_every_member_registered_in_lookup() -> None:
    for cat in LogCategory:
        assert _CATEGORY_BY_CANONICAL[cat.value] is cat


def test_normalise_strips_padding_and_uppercases() -> None:
    assert normalise_category("TOOL ") == "TOOL"
    assert normalise_category("  tool  ") == "TOOL"
    assert normalise_category("SYS  ") == "SYS"


def test_normalise_accepts_enum_member() -> None:
    assert normalise_category(LogCategory.LLM) == "LLM"


def test_validate_returns_enum_for_known_category() -> None:
    assert validate_category("TOOL ") is LogCategory.TOOL
    assert validate_category("llm") is LogCategory.LLM
    assert validate_category(LogCategory.PHASE) is LogCategory.PHASE


def test_validate_raises_on_unknown_category() -> None:
    with pytest.raises(ValueError, match="Unknown LogCategory"):
        validate_category("NONSENSE")


def test_every_emit_site_in_codebase_uses_a_known_category() -> None:
    """Scan the backend for ``emit("LEVEL", "CATEGORY", ...)`` calls and
    confirm every category is a known LogCategory value.

    Catches typos that would silently produce un-filterable records.
    """
    import re
    from pathlib import Path

    known = {c.value for c in LogCategory}
    pattern = re.compile(r"emit\(\s*\"[A-Z]+\"\s*,\s*\"([A-Z _]+?)\"")
    root = Path(__file__).parent.parent  # backend/
    offenders: list[tuple[Path, str]] = []
    for py in root.rglob("*.py"):
        if "/tests/" in str(py) or "/__pycache__/" in str(py):
            continue
        text = py.read_text(encoding="utf-8", errors="replace")
        for match in pattern.finditer(text):
            cat = match.group(1).strip()
            if cat and cat not in known:
                offenders.append((py, cat))
    assert not offenders, (
        "Unknown categories found at emit sites:\n"
        + "\n".join(f"  {p}: {c!r}" for p, c in offenders)
    )
