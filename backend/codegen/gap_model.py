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
    WEAK_CASE = 6                # surviving mutant on a traced line (mutation round)
    FAILING_TESTS = 7
    INVALID_TRACES = 8
    UNTRACED_FUNCTIONS = 9
    UNIMPLEMENTED_REQUIREMENT = 10  # LLR absent from all source-file @traces
    UNCOVERED_REQUIREMENT = 11    # LLR with no passing test evidence
    WEAK_TRACE = 12              # function traces to LLR but doesn't implement it
    SCOPE_CREEP = 13             # function not backed by any requirement

    # Statement/branch (MC/DC) coverage percentages are deliberately NOT
    # gap kinds. U10 gate rebalance (Inozemtseva & Holmes: coverage weakly
    # correlates with suite effectiveness): they are computed, persisted,
    # and logged loudly as report-only metrics — never blocking gaps. The
    # hard gate is requirements coverage (specs/03).


@dataclass
class Gap:
    """A single code-generation gap detected in the workspace."""

    kind: GapKind
    node_id: str
    file_path: str
    details: str
    context: dict[str, Any] = field(default_factory=dict)
