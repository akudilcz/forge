"""Scope creep scenario — validates trace quality gate catches unrequired code.

A simple lookup table with requirements that say "return value or raise KeyError".
The agent is tempted to add fallbacks, caching, default values, or retry logic —
all of which are scope creep because the requirements say "raise KeyError" on miss.

Graph:
    PROJECT-0001: Config Lookup
    └── DOCUMENT-0001 (parent: PROJECT-0001)
        └── PARA-0001 (parent: DOCUMENT-0001)
            ├── HLR-0001: Config Retrieval (parent: PARA-0001)
            │   ├── LLR-0001: get_value (parent: HLR-0001)
            │   ├── LLR-0002: set_value (parent: HLR-0001)
            │   └── LLR-0003: list_keys (parent: HLR-0001)

    ARCHITECTURE-0001 (parent: PROJECT-0001)
    └── MODULE-0001: ConfigStore (parent: ARCHITECTURE-0001)
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
    """Populate graph with the scope creep scenario."""
    nodes = [
        GraphNode(
            node_id="PROJECT-0001",
            node_type=NodeType.PROJECT,
            title="Config Lookup",
            content="A key-value configuration store.",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="DOCUMENT-0001",
            node_type=NodeType.DOCUMENT,
            title="Config Spec",
            content="Specification for the config lookup system.",
            parent_id="PROJECT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="PARA-0001",
            node_type=NodeType.PARA,
            title="Requirements",
            content="Functional requirements for config store.",
            parent_id="DOCUMENT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="HLR-0001",
            node_type=NodeType.HLR,
            title="Config Retrieval",
            content="The system SHALL store and retrieve configuration values by key.",
            parent_id="PARA-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="LLR-0001",
            node_type=NodeType.LLR,
            title="get_value",
            content=(
                "get_value(key: str) -> str: Return the value for the given key. "
                "Raise KeyError if the key does not exist. "
                "Do NOT return a default value — the caller handles missing keys."
            ),
            parent_id="HLR-0001",
            trace_to=["HLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="LLR-0002",
            node_type=NodeType.LLR,
            title="set_value",
            content=(
                "set_value(key: str, value: str) -> None: Store the value for the key. "
                "Overwrite if the key already exists."
            ),
            parent_id="HLR-0001",
            trace_to=["HLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="LLR-0003",
            node_type=NodeType.LLR,
            title="list_keys",
            content=(
                "list_keys() -> list[str]: Return a sorted list of all keys in the store."
            ),
            parent_id="HLR-0001",
            trace_to=["HLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        # ── Architecture ─────────────────────────────────────────────
        GraphNode(
            node_id="ARCHITECTURE-0001",
            node_type=NodeType.ARCHITECTURE,
            title="Config Architecture",
            content="Single-module in-memory dictionary store.",
            parent_id="PROJECT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="MODULE-0001",
            node_type=NodeType.MODULE,
            title="ConfigStore",
            content="Module containing the config store class.",
            parent_id="ARCHITECTURE-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CONTRACT-0001",
            node_type=NodeType.CONTRACT,
            title="ConfigStore API",
            content=(
                "class ConfigStore:\n"
                "    def get_value(self, key: str) -> str: ...\n"
                "    def set_value(self, key: str, value: str) -> None: ...\n"
                "    def list_keys(self) -> list[str]: ..."
            ),
            parent_id="MODULE-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="DESIGN-0001",
            node_type=NodeType.DESIGN,
            title="ConfigStore Implementation",
            content=(
                "Implement ConfigStore using a plain dict.\n"
                "- get_value: dict lookup, raise KeyError on miss\n"
                "- set_value: dict assignment\n"
                "- list_keys: sorted(dict.keys())\n"
                "No caching, no defaults, no fallbacks, no persistence."
            ),
            parent_id="MODULE-0001",
            trace_to=["LLR-0001", "LLR-0002", "LLR-0003"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        # ── Test cases ───────────────────────────────────────────────
        GraphNode(
            node_id="SUITE-0001",
            node_type=NodeType.SUITE,
            title="ConfigStore Tests",
            content="Test suite for config store.",
            parent_id="PROJECT-0001",
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CASE_LLR-0001",
            node_type=NodeType.CASE_LLR,
            title="Test get_value",
            content=(
                "Verify get_value('host') returns 'localhost' after set. "
                "Verify get_value('missing') raises KeyError."
            ),
            parent_id="SUITE-0001",
            trace_to=["LLR-0001"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CASE_LLR-0002",
            node_type=NodeType.CASE_LLR,
            title="Test set_value",
            content=(
                "Verify set_value('host', 'localhost') stores the value. "
                "Verify set_value overwrites existing keys."
            ),
            parent_id="SUITE-0001",
            trace_to=["LLR-0002"],
            lifecycle=LifecycleState.ACTIVE,
        ),
        GraphNode(
            node_id="CASE_LLR-0003",
            node_type=NodeType.CASE_LLR,
            title="Test list_keys",
            content=(
                "Verify list_keys() returns sorted keys. "
                "Verify list_keys() returns empty list for empty store."
            ),
            parent_id="SUITE-0001",
            trace_to=["LLR-0003"],
            lifecycle=LifecycleState.ACTIVE,
        ),
    ]

    for node in nodes:
        await graph.add_node(node)
