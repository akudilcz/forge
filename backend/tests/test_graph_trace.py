"""Behavioural tests for GraphTraceTool (backend/tools/graph_trace.py)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from backend.tools.graph_trace import GraphTraceTool


def _node(node_id: str, node_type: str = "HLR", title: str = "t") -> SimpleNamespace:
    return SimpleNamespace(node_id=node_id, node_type=node_type, title=title)


class _StubGraph:
    """Minimal duck-typed graph for trace queries."""

    def __init__(
        self,
        nodes: dict[str, Any],
        tracers: dict[str, list[str]],
    ) -> None:
        self._nodes = nodes
        self._tracers = tracers

    async def traceability_chain(self, node_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            node_id=node_id,
            ancestors=[{"node_id": "DOC-1", "node_type": "DOCUMENT", "label": "Doc"}],
        )

    def nodes_tracing_to(self, target_id: str) -> list[str]:
        return self._tracers.get(target_id, [])

    def node_sync(self, node_id: str) -> Any:
        return self._nodes.get(node_id)

    def all_nodes(self) -> list[Any]:
        return list(self._nodes.values())


def _tool(graph: Any) -> GraphTraceTool:
    return GraphTraceTool(graph=graph)


def test_no_graph_returns_error() -> None:
    assert GraphTraceTool()._execute(operation="chain", node_id="X") == "ERROR: Graph not available"


def test_unknown_operation() -> None:
    tool = _tool(_StubGraph({}, {}))
    out = tool._execute(operation="bogus")
    assert "Unknown operation 'bogus'" in out


def test_chain_requires_node_id() -> None:
    tool = _tool(_StubGraph({}, {}))
    assert tool._execute(operation="chain") == "ERROR: node_id is required for 'chain'"


def test_chain_returns_ancestry_json() -> None:
    tool = _tool(_StubGraph({}, {}))
    out = json.loads(tool._execute(operation="chain", node_id="LLR-1"))
    assert out["node_id"] == "LLR-1"
    assert out["ancestors"][0]["node_id"] == "DOC-1"


def test_traced_by_requires_node_id() -> None:
    tool = _tool(_StubGraph({}, {}))
    assert tool._execute(operation="traced_by") == "ERROR: node_id is required for 'traced_by'"


def test_traced_by_no_tracers() -> None:
    tool = _tool(_StubGraph({}, {}))
    assert tool._execute(operation="traced_by", node_id="HLR-1") == "No nodes trace to 'HLR-1'"


def test_traced_by_lists_tracers_and_skips_unresolvable() -> None:
    graph = _StubGraph(
        nodes={"LLR-1": _node("LLR-1", "LLR")},
        tracers={"HLR-1": ["LLR-1", "ghost"]},
    )
    out = json.loads(_tool(graph)._execute(operation="traced_by", node_id="HLR-1"))
    assert [r["node_id"] for r in out] == ["LLR-1"]


def test_coverage_requires_node_type() -> None:
    tool = _tool(_StubGraph({}, {}))
    assert tool._execute(operation="coverage") == "ERROR: node_type is required for 'coverage'"


def test_coverage_no_nodes_of_type() -> None:
    tool = _tool(_StubGraph({"X": _node("X", "LLR")}, {}))
    assert tool._execute(operation="coverage", node_type="HLR") == "No nodes of type 'HLR' found"


def test_coverage_reports_tracers_and_none_markers() -> None:
    graph = _StubGraph(
        nodes={
            "HLR-1": _node("HLR-1", "HLR"),
            "HLR-2": _node("HLR-2", "HLR"),
            "LLR-1": _node("LLR-1", "LLR"),
        },
        tracers={"HLR-1": ["LLR-1", "ghost"]},
    )
    out = json.loads(_tool(graph)._execute(operation="coverage", node_type="hlr"))
    by_id = {r["node_id"]: r for r in out}
    assert by_id["HLR-1"]["traced_by"] == ["LLR:LLR-1"]
    assert by_id["HLR-2"]["traced_by"] == ["(none)"]


def test_exception_becomes_error_string() -> None:
    class _Broken:
        def nodes_tracing_to(self, node_id: str) -> list[str]:
            raise RuntimeError("boom")

    out = _tool(_Broken())._execute(operation="traced_by", node_id="X")
    assert out == "ERROR: boom"
