"""Calculator scenario — minimal realistic project graph.

Builds a graph with 13 nodes following exact production parent_id
constraints from gap_finder.py / gap_analyser.py:

    PROJECT-0001: Calculator
    └── DOCUMENT-0001 (parent: PROJECT-0001)
        └── PARA-0001 (parent: DOCUMENT-0001)
            ├── HLR-0001: Basic Arithmetic (parent: PARA-0001)
            │   ├── LLR-0001: Addition (parent: HLR-0001)
            │   └── LLR-0002: Division (parent: HLR-0001)
            └── HLR-0002: Error Handling (parent: PARA-0001)

    ARCHITECTURE-0001 (parent: PROJECT-0001)
    └── MODULE-0001: Arithmetic (parent: ARCHITECTURE-0001)
        ├── CONTRACT-0001: Arithmetic API (parent: MODULE-0001)
        └── DESIGN-0001: Arithmetic Impl (parent: MODULE-0001)

    SUITE-0001: Arithmetic Tests (parent: PROJECT-0001)
    ├── CASE_LLR-0001: Test Addition (parent: SUITE-0001)
    └── CASE_LLR-0002: Test Division (parent: SUITE-0001)
"""

from __future__ import annotations

from backend.graph.engine import ProjectGraph
from backend.graph.models import GraphNode, LifecycleState, NodeType


async def build_graph(graph: ProjectGraph) -> None:
    """Populate *graph* with the calculator scenario nodes."""
    nodes = [
        GraphNode(
            node_id="PROJECT-0001",
            node_type=NodeType.PROJECT,
            title="Calculator",
            content="A simple calculator project for integration testing.",
            lifecycle=LifecycleState.ACTIVE,
        ),
        # ── Document branch ──────────────────────────────────────────
        GraphNode(
            node_id="DOCUMENT-0001",
            node_type=NodeType.DOCUMENT,
            title="Calculator Spec",
            content="Specification for a basic calculator.",
            parent_id="PROJECT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="PARA-0001",
            node_type=NodeType.PARA,
            title="Requirements",
            content="Functional requirements for calculator operations.",
            parent_id="DOCUMENT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="HLR-0001",
            node_type=NodeType.HLR,
            title="Basic Arithmetic",
            content=(
                "The calculator SHALL support addition and division of "
                "two floating-point numbers."
            ),
            parent_id="PARA-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="HLR-0002",
            node_type=NodeType.HLR,
            title="Error Handling",
            content="The calculator SHALL raise ValueError on division by zero.",
            parent_id="PARA-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="LLR-0001",
            node_type=NodeType.LLR,
            title="Addition",
            content="add(a: float, b: float) -> float: return a + b",
            parent_id="HLR-0001",
            trace_to=["HLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="LLR-0002",
            node_type=NodeType.LLR,
            title="Division",
            content=(
                "divide(a: float, b: float) -> float: "
                "raise ValueError if b == 0, else return a / b"
            ),
            parent_id="HLR-0001",
            trace_to=["HLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        # ── Architecture branch ──────────────────────────────────────
        GraphNode(
            node_id="ARCHITECTURE-0001",
            node_type=NodeType.ARCHITECTURE,
            title="Calculator Architecture",
            content="Single-module architecture for the calculator.",
            parent_id="PROJECT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="MODULE-0001",
            node_type=NodeType.MODULE,
            title="Arithmetic",
            content="Module containing arithmetic operations.",
            parent_id="ARCHITECTURE-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CONTRACT-0001",
            node_type=NodeType.CONTRACT,
            title="Arithmetic API",
            content=(
                "class Calculator:\n"
                "    def add(self, a: float, b: float) -> float: ...\n"
                "    def divide(self, a: float, b: float) -> float: ..."
            ),
            parent_id="MODULE-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="DESIGN-0001",
            node_type=NodeType.DESIGN,
            title="Arithmetic Implementation",
            content=(
                "Implement Calculator class in src/calculator.py.\n"
                "- add(a, b) returns a + b\n"
                "- divide(a, b) raises ValueError when b == 0, "
                "otherwise returns a / b\n"
                "All public methods must have @traces decorators "
                "referencing their LLR IDs."
            ),
            parent_id="MODULE-0001",
            trace_to=["LLR-0001", "LLR-0002"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        # ── Test branch ──────────────────────────────────────────────
        GraphNode(
            node_id="SUITE-0001",
            node_type=NodeType.SUITE,
            title="Arithmetic Tests",
            content="Test suite for the Arithmetic module.",
            parent_id="PROJECT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CASE_LLR-0001",
            node_type=NodeType.CASE_LLR,
            title="Test Addition",
            content=(
                "Verify add(2, 3) == 5.0 and add(-1, 1) == 0.0. "
                "Traces to LLR-0001."
            ),
            parent_id="SUITE-0001",
            trace_to=["LLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CASE_LLR-0002",
            node_type=NodeType.CASE_LLR,
            title="Test Division",
            content=(
                "Verify divide(10, 2) == 5.0 and divide(1, 0) raises "
                "ValueError. Traces to LLR-0002."
            ),
            parent_id="SUITE-0001",
            trace_to=["LLR-0002"],
            lifecycle=LifecycleState.ACTIVE,
        ),
    ]

    for node in nodes:
        await graph.add_node(node)
