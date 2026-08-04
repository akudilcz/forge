"""Gap definitions for the FORGE Gap Analyser.

A Gap represents a specific, actionable deficiency in the Project Graph.
Gaps are detected by the Gap Analyser and resolved by Agents.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class GapType(str, Enum):
    """Enumeration of all possible Gap types.

    V4 structural gaps follow the architecture-first pipeline:
      UNCHUNKED_DOCUMENT → UNCOVERED_PARA → UNARCHITECTED → UNMODULARISED
      → UNCONTRACTED → UNREFINED_HLR → UNDESIGNED → UNSUITED
      → UNTESTED_HLR → UNTESTED_LLR → UNSYNCED_DESIGN → UNSYNCED_TEST
    """

    # ── V4 structural gaps (architecture-first pipeline order) ────────────────
    UNCHUNKED_DOCUMENT = "UNCHUNKED_DOCUMENT"
    UNCOVERED_PARA = "UNCOVERED_PARA"
    UNARCHITECTED = "UNARCHITECTED"          # P3: PROJECT has no ARCHITECTURE
    UNMODULARISED = "UNMODULARISED"          # P4: HLR not covered by MODULE.trace_to
    UNCONTRACTED = "UNCONTRACTED"            # P5: MODULE has no CONTRACT child
    UNREFINED_HLR = "UNREFINED_HLR"         # P6: HLR has no LLR children
    UNDESIGNED = "UNDESIGNED"               # P7: LLR not covered by DESIGN.trace_to
    UNSUITED = "UNSUITED"                    # P8: PROJECT has no SUITE child
    UNTESTED_HLR = "UNTESTED_HLR"           # P9: HLR has no HLR test case
    UNTESTED_LLR = "UNTESTED_LLR"           # P10: LLR has no LLR test case
    UNSYNCED_DESIGN = "UNSYNCED_DESIGN"     # P12: DESIGN has no CODE child (workspace not linked)
    UNSYNCED_TEST = "UNSYNCED_TEST"         # P13: CASE has no TEST child (workspace test not linked)

    # ── Quality Gaps (Integrity Violations) ───────────────────────────────────
    STALE_NODE = "STALE_NODE"
    ORPHAN_NODE = "ORPHAN_NODE"
    EMPTY_CONTENT = "EMPTY_CONTENT"
    STALE_TRACE_TO = "STALE_TRACE_TO"
    INCONSISTENT_CONTENT = "INCONSISTENT_CONTENT"
    MALFORMED_REQUIREMENT = "MALFORMED_REQUIREMENT"  # HLR/LLR not starting with 'The system shall…'
    NON_ATOMIC_REQUIREMENT = "NON_ATOMIC_REQUIREMENT"  # HLR/LLR covers multiple obligations — needs splitting
    NON_EARS_REQUIREMENT = "NON_EARS_REQUIREMENT"    # HLR/LLR doesn't follow required format template
    UNTITLED_NODE = "UNTITLED_NODE"                  # Node missing a 3-5 word human-readable title
    TITLE_COLLIDES_WITH_PARENT = "TITLE_COLLIDES_WITH_PARENT"  # Child title duplicates parent's — scope not narrowed
    SIBLING_TITLE_DUPLICATE = "SIBLING_TITLE_DUPLICATE"  # Two siblings under same parent share identical titles
    STALE_TITLE = "STALE_TITLE"                      # Title no longer accurately summarises the content scope
    VAGUE_TITLE = "VAGUE_TITLE"                      # Title is a generic label ("Handle Cases") rather than a concrete noun phrase
    DUPLICATE_NODE = "DUPLICATE_NODE"                # Requirement is a semantic duplicate of a sibling
    INADEQUATE_CONTENT = "INADEQUATE_CONTENT"        # Content too short/vague to be actionable downstream
    VAGUE_REQUIREMENT = "VAGUE_REQUIREMENT"          # HLR/LLR uses ambiguous language with no measurable criteria
    UNTESTABLE_REQUIREMENT = "UNTESTABLE_REQUIREMENT"  # Requirement cannot be verified by testing
    CONTRADICTORY_REQUIREMENTS = "CONTRADICTORY_REQUIREMENTS"  # Sibling requirements conflict with each other
    CONTRACT_VIOLATION = "CONTRACT_VIOLATION"        # DESIGN doesn't conform to its MODULE's CONTRACT interface
    CROSS_MODULE_COUPLING = "CROSS_MODULE_COUPLING"  # DESIGN references internals of another MODULE
    INCOMPLETE_DECOMPOSITION = "INCOMPLETE_DECOMPOSITION"  # HLR's LLRs don't fully cover the HLR given CONTRACT/DESIGN context
    EMPTY_TRACE = "EMPTY_TRACE"                      # MODULE or DESIGN has empty trace_to — traces to nothing
    CIRCULAR_TRACE = "CIRCULAR_TRACE"                # trace_to forms a cycle
    STALE_ARCHITECTURE = "STALE_ARCHITECTURE"        # ARCHITECTURE created before a significant fraction of current HLRs — re-derive
    STALE_SUITE = "STALE_SUITE"                      # SUITE created before significant HLR/LLR population change — re-derive
    STALE_CODE = "STALE_CODE"                        # Code gen failed OR workspace file diverges from DESIGN spec
    MISSING_CODE = "MISSING_CODE"                    # DESIGN.file_path is set but workspace file is missing/unreadable


class GapPriority(int, Enum):
    """Priority levels for Gaps (lower number = higher priority).

    The architecture-first ordering places CONTRACT_DESIGN (P5) before
    REQUIREMENTS_LLR (P6) — the full architectural skeleton (ARCHITECTURE,
    MODULEs, CONTRACTs) must be established before LLRs are elaborated.
    TEST_SUITE (P8) fires before test cases so the strategy document exists first.
    """

    DOCUMENT_STRUCTURE = 1   # UNCHUNKED_DOCUMENT
    REQUIREMENTS_HLR = 2     # UNCOVERED_PARA
    ARCHITECTURE = 3         # UNARCHITECTED
    MODULARISATION = 4       # UNMODULARISED
    CONTRACT_DESIGN = 5      # UNCONTRACTED
    REQUIREMENTS_LLR = 6     # UNREFINED_HLR
    DESIGN = 7               # UNDESIGNED
    TEST_SUITE = 8           # UNSUITED
    TEST_HLR = 9             # UNTESTED_HLR
    TEST_LLR = 10            # UNTESTED_LLR
    CODE_SYNC = 12           # UNSYNCED_DESIGN
    TEST_SYNC = 13           # UNSYNCED_TEST
    MAINTENANCE = 13         # All quality gaps


class Gap(BaseModel):
    """A detected gap in the Project Graph.

    Attributes:
        type: The specific type of gap (e.g., UNCOVERED_PARA).
        priority: The priority level (1-13).
        node_id: The ID of the node where the gap exists.
        description: Human-readable description of the gap.
        context: Additional context needed for resolution (e.g., parent ID).
    """

    type: GapType
    priority: GapPriority
    node_id: str
    description: str
    context: dict[str, Any] = Field(default_factory=dict)

    def __hash__(self) -> int:
        return hash((self.type, self.node_id))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Gap):
            return NotImplemented
        return self.type == other.type and self.node_id == other.node_id
