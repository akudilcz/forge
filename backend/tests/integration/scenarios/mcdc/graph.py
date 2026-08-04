"""MC/DC scenario — eligibility validator with compound boolean logic.

Tests that the agent generates code with branching logic and writes
tests that achieve full MC/DC coverage (each condition in a compound
decision independently affects the outcome).

Graph:
    PROJECT-0001: Eligibility Validator
    └── DOCUMENT-0001 (parent: PROJECT-0001)
        └── PARA-0001 (parent: DOCUMENT-0001)
            └── HLR-0001: Eligibility Rules (parent: PARA-0001)
                ├── LLR-0001: Validate age (parent: HLR-0001)
                ├── LLR-0002: Check eligibility (parent: HLR-0001)
                └── LLR-0003: Assess risk level (parent: HLR-0001)

    ARCHITECTURE-0001 (parent: PROJECT-0001)
    └── MODULE-0001: Validator (parent: ARCHITECTURE-0001)
        ├── CONTRACT-0001: Validator API (parent: MODULE-0001)
        └── DESIGN-0001: Validator Implementation (parent: MODULE-0001)

    SUITE-0001 (parent: PROJECT-0001)
    ├── CASE_LLR-0001 (parent: SUITE-0001)
    ├── CASE_LLR-0002 (parent: SUITE-0001)
    └── CASE_LLR-0003 (parent: SUITE-0001)
"""

from __future__ import annotations

from backend.graph.engine import ProjectGraph
from backend.graph.models import GraphNode, LifecycleState, NodeType


async def build_graph(graph: ProjectGraph) -> None:
    """Populate *graph* with the MC/DC scenario nodes."""
    nodes = [
        GraphNode(
            node_id="PROJECT-0001",
            node_type=NodeType.PROJECT,
            title="Eligibility Validator",
            content="A loan eligibility validator with compound boolean logic.",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="DOCUMENT-0001",
            node_type=NodeType.DOCUMENT,
            title="Validator Spec",
            content="Specification for eligibility validation.",
            parent_id="PROJECT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="PARA-0001",
            node_type=NodeType.PARA,
            title="Requirements",
            content="Functional requirements for the eligibility validator.",
            parent_id="DOCUMENT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="HLR-0001",
            node_type=NodeType.HLR,
            title="Eligibility Rules",
            content=(
                "The system SHALL validate applicant eligibility based on "
                "age, income, credit score, and collateral status."
            ),
            parent_id="PARA-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="LLR-0001",
            node_type=NodeType.LLR,
            title="Validate Age",
            content=(
                "validate_age(age: int) -> bool:\n"
                "Return True if 18 <= age <= 65, False otherwise.\n"
                "Raise ValueError if age < 0."
            ),
            parent_id="HLR-0001",
            trace_to=["HLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="LLR-0002",
            node_type=NodeType.LLR,
            title="Check Eligibility",
            content=(
                "check_eligibility(age: int, income: float, "
                "credit_score: int, has_collateral: bool) -> bool:\n"
                "Return True only if ALL of:\n"
                "  - validate_age(age) is True\n"
                "  - income >= 30000\n"
                "  - (credit_score >= 700 OR has_collateral is True)\n"
                "Each condition must independently affect the result "
                "(MC/DC: Modified Condition/Decision Coverage required)."
            ),
            parent_id="HLR-0001",
            trace_to=["HLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="LLR-0003",
            node_type=NodeType.LLR,
            title="Assess Risk Level",
            content=(
                "assess_risk(income: float, credit_score: int, "
                "has_collateral: bool) -> str:\n"
                "Return one of 'low', 'medium', or 'high':\n"
                "  - 'low' if credit_score >= 750 AND income >= 50000\n"
                "  - 'high' if credit_score < 600 OR income < 30000\n"
                "  - 'medium' otherwise"
            ),
            parent_id="HLR-0001",
            trace_to=["HLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        # ── Architecture ─────────────────────────────────────────────
        GraphNode(
            node_id="ARCHITECTURE-0001",
            node_type=NodeType.ARCHITECTURE,
            title="Validator Architecture",
            content="Single-module architecture.",
            parent_id="PROJECT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="MODULE-0001",
            node_type=NodeType.MODULE,
            title="Validator",
            content="Module containing eligibility validation logic.",
            parent_id="ARCHITECTURE-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CONTRACT-0001",
            node_type=NodeType.CONTRACT,
            title="Validator API",
            content=(
                "def validate_age(age: int) -> bool: ...\n"
                "def check_eligibility(age: int, income: float, "
                "credit_score: int, has_collateral: bool) -> bool: ...\n"
                "def assess_risk(income: float, credit_score: int, "
                "has_collateral: bool) -> str: ..."
            ),
            parent_id="MODULE-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="DESIGN-0001",
            node_type=NodeType.DESIGN,
            title="Validator Implementation",
            content=(
                "Implement eligibility functions in src/validator.py.\n\n"
                "validate_age(age):\n"
                "  - Raise ValueError if age < 0\n"
                "  - Return 18 <= age <= 65\n\n"
                "check_eligibility(age, income, credit_score, has_collateral):\n"
                "  - Return validate_age(age) AND income >= 30000 "
                "AND (credit_score >= 700 OR has_collateral)\n"
                "  - This is a compound boolean decision requiring MC/DC test coverage\n\n"
                "assess_risk(income, credit_score, has_collateral):\n"
                "  - Return 'low' if credit_score >= 750 AND income >= 50000\n"
                "  - Return 'high' if credit_score < 600 OR income < 30000\n"
                "  - Return 'medium' otherwise\n\n"
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
            title="Validator Tests",
            content="Test suite for eligibility validation.",
            parent_id="PROJECT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CASE_LLR-0001",
            node_type=NodeType.CASE_LLR,
            title="Test Validate Age",
            content=(
                "Test validate_age boundary conditions:\n"
                "- age=17 -> False, age=18 -> True, age=65 -> True, "
                "age=66 -> False\n"
                "- age=-1 -> ValueError"
            ),
            parent_id="SUITE-0001",
            trace_to=["LLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CASE_LLR-0002",
            node_type=NodeType.CASE_LLR,
            title="Test Check Eligibility",
            content=(
                "MC/DC tests for check_eligibility compound decision:\n"
                "Each condition must independently flip the result.\n"
                "- All conditions true -> True\n"
                "- Only age invalid (others true) -> False\n"
                "- Only income too low (others true) -> False\n"
                "- Only credit_score low AND no collateral -> False\n"
                "- Credit_score low BUT has_collateral -> True\n"
                "- Credit_score high AND no collateral -> True"
            ),
            parent_id="SUITE-0001",
            trace_to=["LLR-0002"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CASE_LLR-0003",
            node_type=NodeType.CASE_LLR,
            title="Test Assess Risk",
            content=(
                "Test all three risk paths:\n"
                "- 'low': credit_score=800, income=60000\n"
                "- 'high': credit_score=500, income=40000\n"
                "- 'high': credit_score=700, income=20000\n"
                "- 'medium': credit_score=700, income=40000"
            ),
            parent_id="SUITE-0001",
            trace_to=["LLR-0003"],
            lifecycle=LifecycleState.ACTIVE,
        ),
    ]

    for node in nodes:
        await graph.add_node(node)
