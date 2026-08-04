"""Pre-seed files for the coverage-gaps scenario.

Creates a workspace with correct source code whose tests achieve ~100%
statement coverage but miss several branch edges:

  Coverage gaps baked in:
    clamp():
      - Tests cover value < lo and lo < value < hi, but NOT value > hi
      - Boundary values (value == lo, value == hi) never tested
    parse_score():
      - Empty-string branch never tested (only valid ints tested)
    grade():
      - Only 'A' (score=95) and 'F' (score=30) tested
      - 'B', 'C', 'D' branches never hit → branch coverage gap
    letter_grades():
      - Empty-list early return never tested
      - ValueError catch branch ('?') never tested

  The agent must:
    1. Identify uncovered branches from coverage feedback
    2. Add tests that hit the missed branches
    3. NOT break existing passing tests
    4. Achieve 100% statement + branch coverage
"""

from __future__ import annotations

from pathlib import Path

_GRADER_PY = '''\
from tracing import traces


@traces("LLR-0001")
def clamp(value: float, lo: float, hi: float) -> float:
    """Clamp value to [lo, hi] range."""
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


@traces("LLR-0002")
def parse_score(text: str) -> float:
    """Parse a score string to float."""
    stripped = text.strip()
    if not stripped:
        return 0.0
    return float(stripped)


@traces("LLR-0003")
def grade(score: float) -> str:
    """Map a numeric score to a letter grade."""
    clamped = clamp(score, 0, 100)
    if clamped >= 90:
        return "A"
    if clamped >= 80:
        return "B"
    if clamped >= 70:
        return "C"
    if clamped >= 60:
        return "D"
    return "F"


@traces("LLR-0004")
def letter_grades(scores: list[str]) -> list[str]:
    """Grade a batch of score strings."""
    if not scores:
        return []
    results: list[str] = []
    for s in scores:
        try:
            results.append(grade(parse_score(s)))
        except ValueError:
            results.append("?")
    return results
'''

# Tests that cover most statements but miss branches.
# Statement coverage is high because every function is called,
# but many branch edges are never taken.
_TEST_CLAMP = '''\
from tracing import traces
from src.grader import clamp


@traces("LLR-0001", case="CASE_LLR-0001")
def test_clamp_below():
    """value < lo -> returns lo."""
    assert clamp(-5, 0, 100) == 0


@traces("LLR-0001", case="CASE_LLR-0001")
def test_clamp_within():
    """lo < value < hi -> returns value."""
    assert clamp(50, 0, 100) == 50
'''
# MISSING: test_clamp_above (value > hi), boundary (value == lo, value == hi)

_TEST_PARSE = '''\
from tracing import traces
from src.grader import parse_score


@traces("LLR-0002", case="CASE_LLR-0002")
def test_parse_valid_int():
    """Valid integer string."""
    assert parse_score("85") == 85.0


@traces("LLR-0002", case="CASE_LLR-0002")
def test_parse_valid_float():
    """Valid float string."""
    assert parse_score("72.5") == 72.5
'''
# MISSING: test empty string, whitespace-only, invalid string

_TEST_GRADE = '''\
from tracing import traces
from src.grader import grade


@traces("LLR-0003", case="CASE_LLR-0003")
def test_grade_a():
    """High score -> A."""
    assert grade(95) == "A"


@traces("LLR-0003", case="CASE_LLR-0003")
def test_grade_f():
    """Low score -> F."""
    assert grade(30) == "F"
'''
# MISSING: tests for B, C, D grades — those branches never hit

_TEST_BATCH = '''\
from tracing import traces
from src.grader import letter_grades


@traces("LLR-0004", case="CASE_LLR-0004")
def test_letter_grades_valid():
    """All valid scores."""
    assert letter_grades(["95", "50"]) == ["A", "F"]
'''
# MISSING: empty list, invalid entry ("abc" -> "?")


def seed_workspace(workspace: Path) -> None:
    """Write the pre-seeded files into the workspace."""
    src_dir = workspace / "src"
    tests_dir = workspace / "tests"
    src_dir.mkdir(exist_ok=True)
    tests_dir.mkdir(exist_ok=True)

    (src_dir / "grader.py").write_text(_GRADER_PY, encoding="utf-8")
    (tests_dir / "test_test_clamp.py").write_text(_TEST_CLAMP, encoding="utf-8")
    (tests_dir / "test_test_parse_score.py").write_text(_TEST_PARSE, encoding="utf-8")
    (tests_dir / "test_test_grade.py").write_text(_TEST_GRADE, encoding="utf-8")
    (tests_dir / "test_test_letter_grades.py").write_text(_TEST_BATCH, encoding="utf-8")
