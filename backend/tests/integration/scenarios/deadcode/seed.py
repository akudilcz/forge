"""Pre-seed files for the deadcode scenario.

Creates a workspace with intentionally broken/incomplete code:
  - src/converter.py: partial impl with dead code
  - tests/test_test_celsius_to_fahrenheit.py: passing but incomplete test

The agent must:
  1. Remove dead code (orphan helper, unreachable branch, unused var)
  2. Add @traces to fahrenheit_to_celsius
  3. Implement celsius_to_kelvin (missing entirely)
  4. Write tests for LLR-0002 and LLR-0003
  5. Achieve 100% coverage
"""

from __future__ import annotations

from pathlib import Path

# Source file with dead code baked in
_CONVERTER_PY = '''\
from tracing import traces


@traces("LLR-0001")
def celsius_to_fahrenheit(c: float) -> float:
    """Convert Celsius to Fahrenheit."""
    return c * 9 / 5 + 32


def fahrenheit_to_celsius(f: float) -> float:
    """Convert Fahrenheit to Celsius.

    NOTE: missing @traces decorator — agent must add it.
    """
    # DEAD CODE: unused variable
    result_cache = {}
    converted = (f - 32) * 5 / 9
    # DEAD CODE: unreachable branch (converted is always a float)
    if isinstance(converted, str):
        return 0.0
    return converted


def _legacy_round(value: float, precision: int = 2) -> float:
    """DEAD CODE: orphan helper not traced to any LLR."""
    return round(value, precision)


# celsius_to_kelvin is MISSING — agent must implement it
'''

# Test file — only covers celsius_to_fahrenheit
_TEST_C_TO_F = '''\
from tracing import traces
from src.converter import celsius_to_fahrenheit


@traces("LLR-0001", case="CASE_LLR-0001")
def test_freezing_point():
    assert celsius_to_fahrenheit(0) == 32.0


@traces("LLR-0001", case="CASE_LLR-0001")
def test_boiling_point():
    assert celsius_to_fahrenheit(100) == 212.0
'''


def seed_workspace(workspace: Path) -> None:
    """Write the pre-seeded files into the workspace."""
    src_dir = workspace / "src"
    tests_dir = workspace / "tests"
    src_dir.mkdir(exist_ok=True)
    tests_dir.mkdir(exist_ok=True)

    (src_dir / "converter.py").write_text(_CONVERTER_PY, encoding="utf-8")
    (tests_dir / "test_test_celsius_to_fahrenheit.py").write_text(
        _TEST_C_TO_F, encoding="utf-8",
    )
