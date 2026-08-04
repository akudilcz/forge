"""Coverage-gaps scenario — seeded code with missed branches.

Pre-seeds source code and tests that achieve ~100% statement coverage but
miss critical branches.  The agent must identify uncovered branch edges
and write additional tests without breaking existing ones.

Coverage gaps baked in:
  - clamp(): boundary-exact values (value == lo, value == hi) never tested
  - parse_score(): empty-string branch untested; only valid ints tested
  - grade(): only 'A' and 'F' paths tested; 'B', 'C', 'D' branches missed
  - letter_grades(): empty-list early return untested; error branch untested

Graph:
    PROJECT-0001: Grade Calculator
    └── DOCUMENT-0001 (parent: PROJECT-0001)
        └── PARA-0001 (parent: DOCUMENT-0001)
            └── HLR-0001: Grading Rules (parent: PARA-0001)
                ├── LLR-0001: Clamp value to range (parent: HLR-0001)
                ├── LLR-0002: Parse score string (parent: HLR-0001)
                ├── LLR-0003: Map score to grade (parent: HLR-0001)
                └── LLR-0004: Grade a batch (parent: HLR-0001)

    ARCHITECTURE-0001 (parent: PROJECT-0001)
    └── MODULE-0001: Grader (parent: ARCHITECTURE-0001)
        ├── CONTRACT-0001: Grader API (parent: MODULE-0001)
        └── DESIGN-0001: Grader Implementation (parent: MODULE-0001)

    SUITE-0001 (parent: PROJECT-0001)
    ├── CASE_LLR-0001 (parent: SUITE-0001)
    ├── CASE_LLR-0002 (parent: SUITE-0001)
    ├── CASE_LLR-0003 (parent: SUITE-0001)
    └── CASE_LLR-0004 (parent: SUITE-0001)
"""

from __future__ import annotations

from backend.graph.engine import ProjectGraph
from backend.graph.models import GraphNode, LifecycleState, NodeType


async def build_graph(graph: ProjectGraph) -> None:
    """Populate *graph* with the coverage-gaps scenario nodes."""
    nodes = [
        GraphNode(
            node_id="PROJECT-0001",
            node_type=NodeType.PROJECT,
            title="Grade Calculator",
            content="A grading utility seeded with coverage gaps.",
            lifecycle=LifecycleState.ACTIVE,
        ),
        # ── Document branch ──────────────────────────────────────────
        GraphNode(
            node_id="DOCUMENT-0001",
            node_type=NodeType.DOCUMENT,
            title="Grader Spec",
            content="Specification for grade calculation.",
            parent_id="PROJECT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="PARA-0001",
            node_type=NodeType.PARA,
            title="Requirements",
            content="Functional requirements for the grading system.",
            parent_id="DOCUMENT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="HLR-0001",
            node_type=NodeType.HLR,
            title="Grading Rules",
            content=(
                "The system SHALL parse score strings, clamp values, "
                "assign letter grades, and process batches."
            ),
            parent_id="PARA-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        # ── LLRs ────────────────────────────────────────────────────
        GraphNode(
            node_id="LLR-0001",
            node_type=NodeType.LLR,
            title="Clamp Value",
            content=(
                "clamp(value: float, lo: float, hi: float) -> float:\n"
                "Return lo if value < lo.\n"
                "Return hi if value > hi.\n"
                "Return value otherwise (lo <= value <= hi)."
            ),
            parent_id="HLR-0001",
            trace_to=["HLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="LLR-0002",
            node_type=NodeType.LLR,
            title="Parse Score",
            content=(
                "parse_score(text: str) -> float:\n"
                "Strip whitespace from text.\n"
                "Return 0.0 if the stripped text is empty.\n"
                "Try to convert to float; raise ValueError on failure."
            ),
            parent_id="HLR-0001",
            trace_to=["HLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="LLR-0003",
            node_type=NodeType.LLR,
            title="Map Score to Grade",
            content=(
                "grade(score: float) -> str:\n"
                "Clamp score to [0, 100] using clamp().\n"
                "Return 'A' if score >= 90.\n"
                "Return 'B' if score >= 80.\n"
                "Return 'C' if score >= 70.\n"
                "Return 'D' if score >= 60.\n"
                "Return 'F' otherwise."
            ),
            parent_id="HLR-0001",
            trace_to=["HLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="LLR-0004",
            node_type=NodeType.LLR,
            title="Grade Batch",
            content=(
                "letter_grades(scores: list[str]) -> list[str]:\n"
                "Return [] if scores is empty.\n"
                "For each score string, call parse_score then grade.\n"
                "If parse_score raises ValueError, use '?' for that entry.\n"
                "Return the list of grade strings."
            ),
            parent_id="HLR-0001",
            trace_to=["HLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        # ── Architecture ─────────────────────────────────────────────
        GraphNode(
            node_id="ARCHITECTURE-0001",
            node_type=NodeType.ARCHITECTURE,
            title="Grader Architecture",
            content="Single-module architecture.",
            parent_id="PROJECT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="MODULE-0001",
            node_type=NodeType.MODULE,
            title="Grader",
            content="Module containing grade calculation functions.",
            parent_id="ARCHITECTURE-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CONTRACT-0001",
            node_type=NodeType.CONTRACT,
            title="Grader API",
            content=(
                "def clamp(value: float, lo: float, hi: float) -> float: ...\n"
                "def parse_score(text: str) -> float: ...\n"
                "def grade(score: float) -> str: ...\n"
                "def letter_grades(scores: list[str]) -> list[str]: ..."
            ),
            parent_id="MODULE-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="DESIGN-0001",
            node_type=NodeType.DESIGN,
            title="Grader Implementation",
            content=(
                "Implement grading functions in src/grader.py.\n\n"
                "clamp(value, lo, hi):\n"
                "  - Three-way conditional: value < lo, value > hi, else pass-through\n\n"
                "parse_score(text):\n"
                "  - Strip, check empty, convert to float\n\n"
                "grade(score):\n"
                "  - Clamp then if/elif chain for A/B/C/D/F\n\n"
                "letter_grades(scores):\n"
                "  - Early return for empty list\n"
                "  - Loop with try/except for parse failures\n\n"
                "All functions must have @traces decorators."
            ),
            parent_id="MODULE-0001",
            trace_to=["LLR-0001", "LLR-0002", "LLR-0003", "LLR-0004"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        # ── Test cases ───────────────────────────────────────────────
        GraphNode(
            node_id="SUITE-0001",
            node_type=NodeType.SUITE,
            title="Grader Tests",
            content="Test suite for the grading system.",
            parent_id="PROJECT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CASE_LLR-0001",
            node_type=NodeType.CASE_LLR,
            title="Test Clamp",
            content=(
                "Test all three clamp branches:\n"
                "- value < lo: clamped to lo\n"
                "- value > hi: clamped to hi\n"
                "- lo <= value <= hi: returned unchanged\n"
                "- Boundary: value == lo, value == hi"
            ),
            parent_id="SUITE-0001",
            trace_to=["LLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CASE_LLR-0002",
            node_type=NodeType.CASE_LLR,
            title="Test Parse Score",
            content=(
                "Test all parse_score branches:\n"
                "- Valid integer string: '85' -> 85.0\n"
                "- Valid float string: '72.5' -> 72.5\n"
                "- Empty string: '' -> 0.0\n"
                "- Whitespace-only: '   ' -> 0.0\n"
                "- Invalid string: 'abc' -> ValueError"
            ),
            parent_id="SUITE-0001",
            trace_to=["LLR-0002"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CASE_LLR-0003",
            node_type=NodeType.CASE_LLR,
            title="Test Grade",
            content=(
                "Test all five grade branches:\n"
                "- score=95 -> 'A'\n"
                "- score=85 -> 'B'\n"
                "- score=75 -> 'C'\n"
                "- score=65 -> 'D'\n"
                "- score=50 -> 'F'\n"
                "- Boundary: 90 -> 'A', 80 -> 'B', 70 -> 'C', 60 -> 'D'\n"
                "- Clamping: score=-10 -> 'F', score=110 -> 'A'"
            ),
            parent_id="SUITE-0001",
            trace_to=["LLR-0003"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CASE_LLR-0004",
            node_type=NodeType.CASE_LLR,
            title="Test Letter Grades",
            content=(
                "Test all letter_grades branches:\n"
                "- Empty list: returns []\n"
                "- Valid scores: ['95', '75'] -> ['A', 'C']\n"
                "- Invalid entry: ['abc'] -> ['?']\n"
                "- Mixed: ['90', 'bad', '50'] -> ['A', '?', 'F']"
            ),
            parent_id="SUITE-0001",
            trace_to=["LLR-0004"],
            lifecycle=LifecycleState.ACTIVE,
        ),
    ]

    for node in nodes:
        await graph.add_node(node)
