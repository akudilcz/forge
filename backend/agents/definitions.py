"""Agent definitions and role mappings for the FORGE system.

This module defines the specific agent roles and their responsibilities,
mapping them directly to the Gap types they are responsible for resolving.
"""

from enum import Enum

from backend.analysis.gaps import GapType


class AgentRole(str, Enum):
    """The specialized roles in the FORGE agent team."""

    DOCUMENT_SPECIALIST = "Document Specialist"
    REQUIREMENTS_ENGINEER = "Requirements Engineer"
    DESIGN_ARCHITECT = "Design Architect"
    SOFTWARE_ENGINEER = "Software Engineer"
    TEST_ENGINEER = "Test Engineer"
    QUALITY_AUDITOR = "Quality Auditor"
    CONSOLE = "Console"


# Mapping from GapType to the Agent responsible for fixing it.
# V4 pipeline order: DS → RE(HLR) → DA(arch/mod/ctr) → RE(LLR) → SE(design/sync) → TE(suite/hlr/llr/sync) → QA
GAP_AGENT_MAPPING: dict[GapType, AgentRole] = {
    # Phase 2 — Document Specialist
    GapType.UNCHUNKED_DOCUMENT: AgentRole.DOCUMENT_SPECIALIST,
    # Phase 3 — Requirements Engineer (HLRs from paragraphs)
    GapType.UNCOVERED_PARA: AgentRole.REQUIREMENTS_ENGINEER,
    # Phases 4–6 — Design Architect (architecture skeleton)
    GapType.UNARCHITECTED: AgentRole.DESIGN_ARCHITECT,
    GapType.UNMODULARISED: AgentRole.DESIGN_ARCHITECT,
    GapType.UNCONTRACTED: AgentRole.DESIGN_ARCHITECT,
    # Phase 7 — Requirements Engineer (LLRs, with full arch context)
    GapType.UNREFINED_HLR: AgentRole.REQUIREMENTS_ENGINEER,
    # Phase 8 — Software Engineer (DESIGN specs: API + responsibilities, no code)
    GapType.UNDESIGNED: AgentRole.SOFTWARE_ENGINEER,
    # Phases 9–10 — Test Engineer (test strategy + test cases)
    GapType.UNSUITED: AgentRole.TEST_ENGINEER,
    GapType.UNTESTED_HLR: AgentRole.TEST_ENGINEER,
    GapType.UNTESTED_LLR: AgentRole.TEST_ENGINEER,
    # UNSYNCED_DESIGN / UNSYNCED_TEST: handled by workspace_sync step (no agent)
    # Quality Auditor — all quality gaps (routed per-phase by node type)
    GapType.STALE_NODE: AgentRole.QUALITY_AUDITOR,
    GapType.ORPHAN_NODE: AgentRole.QUALITY_AUDITOR,
    GapType.EMPTY_CONTENT: AgentRole.QUALITY_AUDITOR,
    GapType.STALE_TRACE_TO: AgentRole.QUALITY_AUDITOR,
    GapType.INCONSISTENT_CONTENT: AgentRole.QUALITY_AUDITOR,
    GapType.UNTITLED_NODE: AgentRole.QUALITY_AUDITOR,
    GapType.TITLE_COLLIDES_WITH_PARENT: AgentRole.QUALITY_AUDITOR,
    GapType.SIBLING_TITLE_DUPLICATE: AgentRole.QUALITY_AUDITOR,
    GapType.STALE_TITLE: AgentRole.QUALITY_AUDITOR,
    GapType.VAGUE_TITLE: AgentRole.QUALITY_AUDITOR,
    GapType.DUPLICATE_NODE: AgentRole.QUALITY_AUDITOR,
    # Requirements Engineer — rewrites malformed HLR/LLR wording / splits non-atomic
    GapType.MALFORMED_REQUIREMENT: AgentRole.REQUIREMENTS_ENGINEER,
    GapType.NON_ATOMIC_REQUIREMENT: AgentRole.REQUIREMENTS_ENGINEER,
    GapType.NON_EARS_REQUIREMENT: AgentRole.REQUIREMENTS_ENGINEER,
    # Requirement quality — LLM-detected
    GapType.VAGUE_REQUIREMENT: AgentRole.REQUIREMENTS_ENGINEER,
    GapType.UNTESTABLE_REQUIREMENT: AgentRole.REQUIREMENTS_ENGINEER,
    GapType.CONTRADICTORY_REQUIREMENTS: AgentRole.REQUIREMENTS_ENGINEER,
    GapType.INCOMPLETE_DECOMPOSITION: AgentRole.REQUIREMENTS_ENGINEER,
    # Content adequacy — LLM-detected
    GapType.INADEQUATE_CONTENT: AgentRole.QUALITY_AUDITOR,
    # Architectural conformance — LLM-detected
    GapType.CONTRACT_VIOLATION: AgentRole.SOFTWARE_ENGINEER,
    GapType.CROSS_MODULE_COUPLING: AgentRole.SOFTWARE_ENGINEER,
    # Structural staleness — re-derive owner documents
    GapType.STALE_ARCHITECTURE: AgentRole.DESIGN_ARCHITECT,
    GapType.STALE_SUITE: AgentRole.TEST_ENGINEER,
    # Code-sync health — handled by workspace_sync / quality audit
    GapType.STALE_CODE: AgentRole.QUALITY_AUDITOR,
    GapType.MISSING_CODE: AgentRole.QUALITY_AUDITOR,
    # Trace integrity
    GapType.EMPTY_TRACE: AgentRole.QUALITY_AUDITOR,
    GapType.CIRCULAR_TRACE: AgentRole.QUALITY_AUDITOR,
}


class AgentDefinition:
    """Static configuration for a single FORGE agent role.

    Attributes:
        role: The AgentRole enum value this definition describes.
        goal: High-level purpose string derived from the role.
    """

    def __init__(self, role: AgentRole):
        self.role = role
        self.goal = self._get_goal()

    def _get_goal(self) -> str:
        """Returns the high-level goal for the agent based on its role."""
        goals = {
            AgentRole.DOCUMENT_SPECIALIST: "Parse raw documents into structured paragraphs.",
            AgentRole.REQUIREMENTS_ENGINEER: "Derive formal requirements from source text.",
            AgentRole.DESIGN_ARCHITECT: "Create modular software designs from requirements.",
            AgentRole.SOFTWARE_ENGINEER: "Create design specs and sync workspace code files.",
            AgentRole.TEST_ENGINEER: "Verify requirements via test cases and sync workspace test files.",
            AgentRole.QUALITY_AUDITOR: "Ensure system integrity and compliance.",
            AgentRole.CONSOLE: "Execute any ad-hoc user request against the project graph and workspace.",
        }
        return goals.get(self.role, "Assist with software engineering tasks.")


# Registry of all agent definitions
AGENT_REGISTRY: dict[AgentRole, AgentDefinition] = {
    role: AgentDefinition(role=role) for role in AgentRole
}
