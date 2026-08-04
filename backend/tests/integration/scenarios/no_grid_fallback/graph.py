"""No-grid-fallback scenario — validates kinematic path integrity.

A lattice planner with strict kinematic requirements. The requirements
specify A* search using motion primitives (constant-curvature arcs).
Any BFS/grid-cell path that bypasses the motion primitive system
violates the kinematic constraints even if it's collision-free.

The key test: after code gen, every path-returning function must
use the motion primitive API. No function should traverse the grid
with simple 8-connected cell moves.

Graph:
    PROJECT-0001: Lattice Planner
    └── DOCUMENT-0001 (parent: PROJECT-0001)
        └── PARA-0001 (parent: DOCUMENT-0001)
            ├── HLR-0001: Path Planning (parent: PARA-0001)
            │   ├── LLR-0001: Return kinematic path or failure
            │   ├── LLR-0002: Use motion primitives (constant-curvature arcs)
            │   └── LLR-0003: Heuristic via backward Dijkstra flood

    ARCHITECTURE-0001 (parent: PROJECT-0001)
    └── MODULE-0001: Planner (parent: ARCHITECTURE-0001)
        ├── CONTRACT-0001 (parent: MODULE-0001)
        └── DESIGN-0001 (parent: MODULE-0001)

    SUITE-0001 (parent: PROJECT-0001)
    ├── CASE_LLR-0001 (trace_to: [LLR-0001])
    ├── CASE_LLR-0002 (trace_to: [LLR-0002])
    └── CASE_LLR-0003 (trace_to: [LLR-0003])
"""

from __future__ import annotations

from backend.graph.engine import ProjectGraph
from backend.graph.models import GraphNode, LifecycleState, NodeType


async def build_graph(graph: ProjectGraph) -> None:
    """Populate graph with the kinematic planner scenario."""
    nodes = [
        GraphNode(
            node_id="PROJECT-0001",
            node_type=NodeType.PROJECT,
            title="Lattice Planner",
            content="A kinematic lattice-based path planner.",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="DOCUMENT-0001",
            node_type=NodeType.DOCUMENT,
            title="Planner Spec",
            content="Specification for kinematic lattice planner.",
            parent_id="PROJECT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="PARA-0001",
            node_type=NodeType.PARA,
            title="Requirements",
            content="Functional requirements.",
            parent_id="DOCUMENT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="HLR-0001",
            node_type=NodeType.HLR,
            title="Path Planning",
            content=(
                "The planner SHALL compute a kinematically feasible path "
                "using lattice-based motion primitives, or signal failure."
            ),
            parent_id="PARA-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="LLR-0001",
            node_type=NodeType.LLR,
            title="Return kinematic path or failure",
            content=(
                "plan(start, goal) -> list[Pose]: Return an ordered list of poses "
                "that follow kinematically feasible motion primitives from start to "
                "goal. Raise PlanningError if no feasible path exists. "
                "Do NOT use grid-cell BFS or any non-kinematic path. "
                "Every segment of the returned path MUST correspond to a motion "
                "primitive from the primitive set."
            ),
            parent_id="HLR-0001",
            trace_to=["HLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="LLR-0002",
            node_type=NodeType.LLR,
            title="Use motion primitives",
            content=(
                "The search SHALL expand states using ONLY motion primitives "
                "(constant-curvature arcs). Each expansion produces a successor "
                "state by applying one primitive's (dx, dy, delta_heading). "
                "No grid-cell BFS, no 8-connected neighbor expansion, no deque-based "
                "flood fill. ALL path segments must be kinematically constrained."
            ),
            parent_id="HLR-0001",
            trace_to=["HLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="LLR-0003",
            node_type=NodeType.LLR,
            title="Heuristic via backward Dijkstra",
            content=(
                "The heuristic SHALL be computed via backward Dijkstra flood "
                "on the 2-D grid with 8-connected movement."
            ),
            parent_id="HLR-0001",
            trace_to=["HLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        # ── Architecture ─────────────────────────────────────────────
        GraphNode(
            node_id="ARCHITECTURE-0001",
            node_type=NodeType.ARCHITECTURE,
            title="Planner Architecture",
            content="Single-module lattice planner.",
            parent_id="PROJECT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="MODULE-0001",
            node_type=NodeType.MODULE,
            title="Planner",
            content="Module containing the lattice planner.",
            parent_id="ARCHITECTURE-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CONTRACT-0001",
            node_type=NodeType.CONTRACT,
            title="Planner API",
            content=(
                "class Planner:\n"
                "    def plan(self, start: Pose, goal: Pose) -> list[Pose]: ...\n"
            ),
            parent_id="MODULE-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="DESIGN-0001",
            node_type=NodeType.DESIGN,
            title="Lattice Planner Implementation",
            content=(
                "Implement A* search over a lattice of motion primitives.\n"
                "- States are (x, y, heading_bin) tuples on the grid\n"
                "- Expansion uses ONLY motion primitives (constant-curvature arcs)\n"
                "- Heuristic: backward Dijkstra flood on 2-D grid\n"
                "- Return kinematic path or raise PlanningError\n"
                "- Do NOT implement grid-cell BFS fallback or any non-kinematic path"
            ),
            parent_id="MODULE-0001",
            trace_to=["LLR-0001", "LLR-0002", "LLR-0003"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        # ── Test cases ───────────────────────────────────────────────
        GraphNode(
            node_id="SUITE-0001",
            node_type=NodeType.SUITE,
            title="Planner Tests",
            content="Test suite for lattice planner.",
            parent_id="PROJECT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CASE_LLR-0001",
            node_type=NodeType.CASE_LLR,
            title="Test path or failure",
            content=(
                "Verify plan() returns a path on a free grid and raises "
                "PlanningError on a blocked grid."
            ),
            parent_id="SUITE-0001",
            trace_to=["LLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CASE_LLR-0002",
            node_type=NodeType.CASE_LLR,
            title="Test kinematic constraint",
            content=(
                "Verify every segment of the returned path corresponds to "
                "a motion primitive. No grid-cell moves allowed."
            ),
            parent_id="SUITE-0001",
            trace_to=["LLR-0002"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CASE_LLR-0003",
            node_type=NodeType.CASE_LLR,
            title="Test heuristic",
            content=(
                "Verify the heuristic returns finite values for reachable "
                "cells and infinity for unreachable cells."
            ),
            parent_id="SUITE-0001",
            trace_to=["LLR-0003"],
            lifecycle=LifecycleState.ACTIVE,
        ),
    ]

    for node in nodes:
        await graph.add_node(node)
