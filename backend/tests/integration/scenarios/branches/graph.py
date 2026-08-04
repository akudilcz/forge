"""Branches scenario — data classifier with dense branching logic.

Exercises branch coverage on code with loops, early returns, nested
conditionals, exception handling, and short-circuit evaluation.  Statement
coverage is easy; branch coverage demands tests that hit every True/False
edge of every decision point.

Graph:
    PROJECT-0001: Data Classifier
    └── DOCUMENT-0001 (parent: PROJECT-0001)
        └── PARA-0001 (parent: DOCUMENT-0001)
            └── HLR-0001: Classification Rules (parent: PARA-0001)
                ├── LLR-0001: Classify single value (parent: HLR-0001)
                ├── LLR-0002: Classify batch (parent: HLR-0001)
                └── LLR-0003: Summarise batch (parent: HLR-0001)

    ARCHITECTURE-0001 (parent: PROJECT-0001)
    └── MODULE-0001: Classifier (parent: ARCHITECTURE-0001)
        ├── CONTRACT-0001: Classifier API (parent: MODULE-0001)
        └── DESIGN-0001: Classifier Implementation (parent: MODULE-0001)

    SUITE-0001 (parent: PROJECT-0001)
    ├── CASE_LLR-0001 (parent: SUITE-0001)
    ├── CASE_LLR-0002 (parent: SUITE-0001)
    └── CASE_LLR-0003 (parent: SUITE-0001)
"""

from __future__ import annotations

from backend.graph.engine import ProjectGraph
from backend.graph.models import GraphNode, LifecycleState, NodeType


async def build_graph(graph: ProjectGraph) -> None:
    """Populate *graph* with the branches scenario nodes."""
    nodes = [
        GraphNode(
            node_id="PROJECT-0001",
            node_type=NodeType.PROJECT,
            title="Data Classifier",
            content="A numeric data classifier requiring full branch coverage.",
            lifecycle=LifecycleState.ACTIVE,
        ),
        # ── Document branch ──────────────────────────────────────────
        GraphNode(
            node_id="DOCUMENT-0001",
            node_type=NodeType.DOCUMENT,
            title="Classifier Spec",
            content="Specification for data classification rules.",
            parent_id="PROJECT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="PARA-0001",
            node_type=NodeType.PARA,
            title="Requirements",
            content="Functional requirements for the classifier.",
            parent_id="DOCUMENT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="HLR-0001",
            node_type=NodeType.HLR,
            title="Classification Rules",
            content=(
                "The system SHALL classify numeric values by category "
                "and provide batch classification with summary statistics."
            ),
            parent_id="PARA-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        # ── LLRs ────────────────────────────────────────────────────
        GraphNode(
            node_id="LLR-0001",
            node_type=NodeType.LLR,
            title="Classify Single Value",
            content=(
                "classify(value: float) -> str:\n"
                "Raise TypeError if value is not int or float.\n"
                "Return 'negative' if value < 0.\n"
                "Return 'zero' if value == 0.\n"
                "Return 'small' if 0 < value <= 10.\n"
                "Return 'medium' if 10 < value <= 100.\n"
                "Return 'large' if value > 100."
            ),
            parent_id="HLR-0001",
            trace_to=["HLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="LLR-0002",
            node_type=NodeType.LLR,
            title="Classify Batch",
            content=(
                "classify_batch(values: list[float]) -> list[str]:\n"
                "Return an empty list if values is empty.\n"
                "Skip None entries in the list (do not classify them).\n"
                "For each non-None entry, call classify(value).\n"
                "If classify raises TypeError, use 'invalid' for that entry.\n"
                "Return the list of classification strings."
            ),
            parent_id="HLR-0001",
            trace_to=["HLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="LLR-0003",
            node_type=NodeType.LLR,
            title="Summarise Batch",
            content=(
                "summarise(values: list[float]) -> dict:\n"
                "Call classify_batch(values) to get labels.\n"
                "Return a dict with keys:\n"
                "  'total': number of classified entries (excluding skipped Nones),\n"
                "  'counts': dict mapping each label to its count,\n"
                "  'has_errors': True if any entry was 'invalid', False otherwise.\n"
                "If total is 0, return {'total': 0, 'counts': {}, 'has_errors': False}."
            ),
            parent_id="HLR-0001",
            trace_to=["HLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        # ── Architecture ─────────────────────────────────────────────
        GraphNode(
            node_id="ARCHITECTURE-0001",
            node_type=NodeType.ARCHITECTURE,
            title="Classifier Architecture",
            content="Single-module architecture.",
            parent_id="PROJECT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="MODULE-0001",
            node_type=NodeType.MODULE,
            title="Classifier",
            content="Module containing data classification functions.",
            parent_id="ARCHITECTURE-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CONTRACT-0001",
            node_type=NodeType.CONTRACT,
            title="Classifier API",
            content=(
                "def classify(value: float) -> str: ...\n"
                "def classify_batch(values: list[float]) -> list[str]: ...\n"
                "def summarise(values: list[float]) -> dict: ..."
            ),
            parent_id="MODULE-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="DESIGN-0001",
            node_type=NodeType.DESIGN,
            title="Classifier Implementation",
            content=(
                "Implement classification functions in src/classifier.py.\n\n"
                "classify(value):\n"
                "  - Raise TypeError if not int/float\n"
                "  - Chain of if/elif returning category string\n\n"
                "classify_batch(values):\n"
                "  - Return [] for empty input\n"
                "  - Loop: skip None entries, catch TypeError for invalid\n\n"
                "summarise(values):\n"
                "  - Delegate to classify_batch\n"
                "  - Build counts dict, detect 'invalid' entries\n"
                "  - Early return for empty results\n\n"
                "All functions must have @traces decorators."
            ),
            parent_id="MODULE-0001",
            trace_to=["LLR-0001", "LLR-0002", "LLR-0003"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        # ── Test cases ───────────────────────────────────────────────
        GraphNode(
            node_id="SUITE-0001",
            node_type=NodeType.SUITE,
            title="Classifier Tests",
            content="Test suite for the data classifier.",
            parent_id="PROJECT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CASE_LLR-0001",
            node_type=NodeType.CASE_LLR,
            title="Test Classify Single Value",
            content=(
                "Test every branch of classify():\n"
                "- TypeError for string input\n"
                "- 'negative' for -5\n"
                "- 'zero' for 0\n"
                "- 'small' for 5 and for 10 (boundary)\n"
                "- 'medium' for 50 and for 100 (boundary)\n"
                "- 'large' for 101"
            ),
            parent_id="SUITE-0001",
            trace_to=["LLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CASE_LLR-0002",
            node_type=NodeType.CASE_LLR,
            title="Test Classify Batch",
            content=(
                "Test every branch of classify_batch():\n"
                "- Empty list returns []\n"
                "- List with None entries: Nones are skipped\n"
                "- List with invalid (string) entries: 'invalid' used\n"
                "- Mixed list: valid, None, and invalid entries combined"
            ),
            parent_id="SUITE-0001",
            trace_to=["LLR-0002"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CASE_LLR-0003",
            node_type=NodeType.CASE_LLR,
            title="Test Summarise",
            content=(
                "Test every branch of summarise():\n"
                "- Empty list: total=0, counts={}, has_errors=False\n"
                "- All valid: correct counts, has_errors=False\n"
                "- Contains invalid: has_errors=True\n"
                "- All None: total=0 (all skipped)"
            ),
            parent_id="SUITE-0001",
            trace_to=["LLR-0003"],
            lifecycle=LifecycleState.ACTIVE,
        ),
    ]

    for node in nodes:
        await graph.add_node(node)
