"""Deadcode scenario — temperature converter with pre-seeded partial code.

Tests that the agent can:
  1. Remove dead code (untraced orphan functions, unreachable branches)
  2. Implement missing functions
  3. Fix broken tests
  4. Achieve 100% statement + branch coverage

Graph:
    PROJECT-0001: Temperature Converter
    └── DOCUMENT-0001 (parent: PROJECT-0001)
        └── PARA-0001 (parent: DOCUMENT-0001)
            ├── HLR-0001: Temperature Conversion (parent: PARA-0001)
            │   ├── LLR-0001: Celsius to Fahrenheit (parent: HLR-0001)
            │   ├── LLR-0002: Fahrenheit to Celsius (parent: HLR-0001)
            │   └── LLR-0003: Celsius to Kelvin (parent: HLR-0001)

    ARCHITECTURE-0001 (parent: PROJECT-0001)
    └── MODULE-0001: Converter (parent: ARCHITECTURE-0001)
        ├── CONTRACT-0001: Converter API (parent: MODULE-0001)
        └── DESIGN-0001: Converter Implementation (parent: MODULE-0001)

    SUITE-0001 (parent: PROJECT-0001)
    ├── CASE_LLR-0001 (parent: SUITE-0001, trace_to: [LLR-0001])
    ├── CASE_LLR-0002 (parent: SUITE-0001, trace_to: [LLR-0002])
    └── CASE_LLR-0003 (parent: SUITE-0001, trace_to: [LLR-0003])
"""

from __future__ import annotations

from backend.graph.engine import ProjectGraph
from backend.graph.models import GraphNode, LifecycleState, NodeType


async def build_graph(graph: ProjectGraph) -> None:
    """Populate *graph* with the deadcode scenario nodes."""
    nodes = [
        GraphNode(
            node_id="PROJECT-0001",
            node_type=NodeType.PROJECT,
            title="Temperature Converter",
            content="A temperature conversion utility.",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="DOCUMENT-0001",
            node_type=NodeType.DOCUMENT,
            title="Converter Spec",
            content="Specification for temperature conversions.",
            parent_id="PROJECT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="PARA-0001",
            node_type=NodeType.PARA,
            title="Requirements",
            content="Functional requirements for converter.",
            parent_id="DOCUMENT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="HLR-0001",
            node_type=NodeType.HLR,
            title="Temperature Conversion",
            content="The converter SHALL convert between Celsius, Fahrenheit, and Kelvin.",
            parent_id="PARA-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="LLR-0001",
            node_type=NodeType.LLR,
            title="Celsius to Fahrenheit",
            content="celsius_to_fahrenheit(c: float) -> float: return c * 9/5 + 32",
            parent_id="HLR-0001",
            trace_to=["HLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="LLR-0002",
            node_type=NodeType.LLR,
            title="Fahrenheit to Celsius",
            content="fahrenheit_to_celsius(f: float) -> float: return (f - 32) * 5/9",
            parent_id="HLR-0001",
            trace_to=["HLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="LLR-0003",
            node_type=NodeType.LLR,
            title="Celsius to Kelvin",
            content="celsius_to_kelvin(c: float) -> float: return c + 273.15",
            parent_id="HLR-0001",
            trace_to=["HLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        # ── Architecture ─────────────────────────────────────────────
        GraphNode(
            node_id="ARCHITECTURE-0001",
            node_type=NodeType.ARCHITECTURE,
            title="Converter Architecture",
            content="Single-module architecture.",
            parent_id="PROJECT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="MODULE-0001",
            node_type=NodeType.MODULE,
            title="Converter",
            content="Module containing temperature conversion functions.",
            parent_id="ARCHITECTURE-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CONTRACT-0001",
            node_type=NodeType.CONTRACT,
            title="Converter API",
            content=(
                "def celsius_to_fahrenheit(c: float) -> float: ...\n"
                "def fahrenheit_to_celsius(f: float) -> float: ...\n"
                "def celsius_to_kelvin(c: float) -> float: ..."
            ),
            parent_id="MODULE-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="DESIGN-0001",
            node_type=NodeType.DESIGN,
            title="Converter Implementation",
            content=(
                "Implement temperature conversion functions in src/converter.py.\n"
                "- celsius_to_fahrenheit(c) returns c * 9/5 + 32\n"
                "- fahrenheit_to_celsius(f) returns (f - 32) * 5/9\n"
                "- celsius_to_kelvin(c) returns c + 273.15\n"
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
            title="Converter Tests",
            content="Test suite for temperature conversions.",
            parent_id="PROJECT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CASE_LLR-0001",
            node_type=NodeType.CASE_LLR,
            title="Test Celsius to Fahrenheit",
            content=(
                "Verify celsius_to_fahrenheit(0) == 32.0 and "
                "celsius_to_fahrenheit(100) == 212.0."
            ),
            parent_id="SUITE-0001",
            trace_to=["LLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CASE_LLR-0002",
            node_type=NodeType.CASE_LLR,
            title="Test Fahrenheit to Celsius",
            content=(
                "Verify fahrenheit_to_celsius(32) == 0.0 and "
                "fahrenheit_to_celsius(212) == 100.0."
            ),
            parent_id="SUITE-0001",
            trace_to=["LLR-0002"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CASE_LLR-0003",
            node_type=NodeType.CASE_LLR,
            title="Test Celsius to Kelvin",
            content=(
                "Verify celsius_to_kelvin(0) == 273.15 and "
                "celsius_to_kelvin(-273.15) == 0.0."
            ),
            parent_id="SUITE-0001",
            trace_to=["LLR-0003"],
            lifecycle=LifecycleState.ACTIVE,
        ),
    ]

    for node in nodes:
        await graph.add_node(node)
