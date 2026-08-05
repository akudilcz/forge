"""Tests for markdown-heading-based section extraction."""

from __future__ import annotations

from backend.prompting.markdown_sections import extract_sections

_ARCH_DOC = """\
# Project Architecture

## Executive Summary
We build software.

## Technology Stack
Python 3.12, FastAPI, NetworkX.
Async by default.

## Module Design
Single module named `forge`.
Classes: `Flow`, `GapAnalyser`.

## Cross-Cutting Concerns
Error handling is explicit.
Logging via structlog.

## Key Decisions
Semantic graph over schema-less blobs.
"""


def test_single_section_extraction() -> None:
    out = extract_sections(_ARCH_DOC, ["Technology Stack"])
    assert "Python 3.12" in out
    assert "Async by default" in out
    assert "Classes" not in out  # Module Design excluded


def test_multiple_sections_extraction_in_document_order() -> None:
    out = extract_sections(_ARCH_DOC, ["Technology Stack", "Cross-Cutting Concerns"])
    assert "Python 3.12" in out
    assert "Error handling" in out
    # Document order preserved
    assert out.index("Python 3.12") < out.index("Error handling")


def test_case_insensitive_match_default() -> None:
    out = extract_sections(_ARCH_DOC, ["module design"])
    assert "Classes" in out


def test_no_match_returns_empty() -> None:
    assert extract_sections(_ARCH_DOC, ["Nonexistent Heading"]) == ""


def test_empty_inputs() -> None:
    assert extract_sections("", ["Anything"]) == ""
    assert extract_sections(_ARCH_DOC, []) == ""


def test_full_section_content_preserved() -> None:
    """Zero-truncation: the selected section is returned in full."""
    out = extract_sections(_ARCH_DOC, ["Technology Stack"])
    assert "Python 3.12, FastAPI, NetworkX." in out
    assert "Async by default." in out
