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
    API_SURFACE_MISMATCH = 4     # CONTRACT public_api entry not exposed
    PROHIBITED_CONSTRUCT = 5     # CONTRACT-banned construct used in src/
    FAILING_TESTS = 6
    INVALID_TRACES = 7
    UNTRACED_FUNCTIONS = 8
    LOW_STRUCTURAL_COVERAGE = 9   # statement coverage < 100% for a file
    LOW_BRANCH_COVERAGE = 10     # MC/DC branch coverage < 100%
    UNIMPLEMENTED_REQUIREMENT = 11  # LLR absent from all source-file @traces
    UNCOVERED_REQUIREMENT = 12    # LLR with no passing test evidence
    WEAK_TRACE = 13              # function traces to LLR but doesn't implement it
    SCOPE_CREEP = 14             # function not backed by any requirement


@dataclass
class Gap:
    """A single code-generation gap detected in the workspace."""

    kind: GapKind
    node_id: str
    file_path: str
    details: str
    context: dict[str, Any] = field(default_factory=dict)
