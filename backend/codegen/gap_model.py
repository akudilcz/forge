"""Gap data model — categories and record for code-generation gaps.

Shared by the gap finder and its checker modules so checkers can build
``Gap`` records without importing the finder itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class GapKind(IntEnum):
    """Gap categories ordered by priority (lower = higher priority).

    TEST_ENV_BROKEN is first because if the environment is broken,
    no other verification is meaningful.  SYNTAX_ERROR is next because
    a file with a syntax error cannot be imported or tested — fixing
    it unblocks all downstream checks.
    """

    TEST_ENV_BROKEN = 0
    SYNTAX_ERROR = 1              # file has a Python syntax error
    MISSING_SOURCE = 2
    MISSING_TEST = 3
    FAILING_TESTS = 4
    INVALID_TRACES = 5
    UNTRACED_FUNCTIONS = 6
    LOW_STRUCTURAL_COVERAGE = 7   # statement coverage < 100% for a file
    LOW_BRANCH_COVERAGE = 8      # MC/DC branch coverage < 100%
    UNIMPLEMENTED_REQUIREMENT = 9  # LLR absent from all source-file @traces
    UNCOVERED_REQUIREMENT = 10    # LLR with no passing test evidence
    WEAK_TRACE = 11              # function traces to LLR but doesn't implement it
    SCOPE_CREEP = 12             # function not backed by any requirement


@dataclass
class Gap:
    """A single code-generation gap detected in the workspace."""

    kind: GapKind
    node_id: str
    file_path: str
    details: str
    context: dict[str, Any] = field(default_factory=dict)
