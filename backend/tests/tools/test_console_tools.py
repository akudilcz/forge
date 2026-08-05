"""Tests for Console-exclusive tools: graph_search, graph_grep, graph_stats, graph_trace."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from backend.tools.graph_grep import GraphGrepTool
from backend.tools.graph_search import GraphSearchTool
from backend.tools.graph_stats import GraphStatsTool
from backend.tools.graph_trace import GraphTraceTool


def _make_node(node_id: str, node_type: str, title: str, content: str = "") -> MagicMock:
    n = MagicMock()
    n.node_id = node_id
    n.node_type = node_type
    n.title = title
    n.content = content
    n.lifecycle = MagicMock(value="ACTIVE")
    n.properties = {}
    return n


def _make_graph(nodes: list[MagicMock] | None = None) -> MagicMock:
    graph = MagicMock()
    graph.all_nodes.return_value = nodes or []
    graph.node_sync.side_effect = lambda nid: next(
        (n for n in (nodes or []) if n.node_id == nid), None
    )
    graph.nodes_tracing_to.return_value = []
    graph.any_trace_to.return_value = False
    return graph


# ── GraphSearchTool ──────────────────────────────────────────────────────────

def test_graph_search_finds_by_title_and_content() -> None:
    nodes = [
        _make_node("hlr.1", "HLR", "Authentication", "Users must provide valid credentials."),
        _make_node("hlr.2", "HLR", "Authorization", "The system stores data securely."),
    ]
    tool = GraphSearchTool(graph=_make_graph(nodes))
    result = json.loads(tool._execute(query="Authent", field="title"))
    assert result[0]["node_id"] == "hlr.1"
    assert result[0]["matched_field"] == "title"

    result = json.loads(tool._execute(query="credentials", field="content"))
    assert result[0]["node_id"] == "hlr.1"


def test_graph_search_filters_by_type() -> None:
    nodes = [_make_node("hlr.1", "HLR", "Auth"), _make_node("llr.1", "LLR", "Auth detail")]
    result = json.loads(GraphSearchTool(graph=_make_graph(nodes))._execute(query="Auth", node_type="LLR"))
    assert all(r["node_type"] == "LLR" for r in result)


def test_graph_search_no_matches_and_no_graph() -> None:
    tool = GraphSearchTool(graph=_make_graph([_make_node("hlr.1", "HLR", "Auth")]))
    assert "No matches" in tool._execute(query="zzzznonexistent", threshold=0.9)
    assert "ERROR" in GraphSearchTool(graph=None)._execute(query="test")


# ── GraphGrepTool ────────────────────────────────────────────────────────────

def test_graph_grep_finds_regex_in_content() -> None:
    nodes = [
        _make_node("hlr.1", "HLR", "Auth", "The system shall authenticate users via OAuth2."),
        _make_node("hlr.2", "HLR", "Data", "Data is stored in PostgreSQL."),
    ]
    result = json.loads(GraphGrepTool(graph=_make_graph(nodes))._execute(pattern=r"OAuth\d"))
    assert len(result) == 1
    assert result[0]["node_id"] == "hlr.1"


def test_graph_grep_searches_title_and_handles_errors() -> None:
    nodes = [_make_node("hlr.1", "HLR", "Authentication Module", "content")]
    assert len(json.loads(GraphGrepTool(graph=_make_graph(nodes))._execute(pattern=r"Auth.*Module", field="title"))) == 1
    assert "ERROR" in GraphGrepTool(graph=_make_graph([]))._execute(pattern="[invalid")
    assert "No matches" in GraphGrepTool(graph=_make_graph(nodes))._execute(pattern="zzzzz")


# ── GraphStatsTool ───────────────────────────────────────────────────────────

def test_graph_stats_returns_counts() -> None:
    nodes = [_make_node("hlr.1", "HLR", "A"), _make_node("hlr.2", "HLR", "B"), _make_node("llr.1", "LLR", "C")]
    result = json.loads(GraphStatsTool(graph=_make_graph(nodes))._execute(include_gaps=False))
    assert result["total_nodes"] == 3
    assert result["by_type"]["HLR"] == 2
    assert result["by_lifecycle"]["ACTIVE"] == 3


def test_graph_stats_with_gap_analyser() -> None:
    from backend.analysis.gaps import Gap, GapPriority, GapType
    analyser = MagicMock()
    analyser.analyse.return_value = [
        Gap(type=GapType.UNDESIGNED, priority=GapPriority.DESIGN, node_id="hlr.1", description="test"),
    ]
    result = json.loads(GraphStatsTool(graph=_make_graph([_make_node("hlr.1", "HLR", "A")]), analyser=analyser)._execute(include_gaps=True))
    assert result["total_gaps"] == 1
    assert "UNDESIGNED" in result["gap_summary"]


def test_graph_stats_no_graph() -> None:
    assert "ERROR" in GraphStatsTool(graph=None)._execute()


# ── GraphTraceTool ───────────────────────────────────────────────────────────

def test_graph_trace_traced_by() -> None:
    nodes = [_make_node("hlr.1", "HLR", "Auth"), _make_node("mod.1", "MODULE", "Auth Module")]
    graph = _make_graph(nodes)
    graph.nodes_tracing_to.return_value = ["mod.1"]
    result = json.loads(GraphTraceTool(graph=graph)._execute(operation="traced_by", node_id="hlr.1"))
    assert result[0]["node_id"] == "mod.1"


def test_graph_trace_traced_by_none() -> None:
    graph = _make_graph([_make_node("hlr.1", "HLR", "Auth")])
    graph.nodes_tracing_to.return_value = []
    assert "No nodes trace" in GraphTraceTool(graph=graph)._execute(operation="traced_by", node_id="hlr.1")


def test_graph_trace_coverage() -> None:
    nodes = [_make_node("hlr.1", "HLR", "Auth"), _make_node("hlr.2", "HLR", "Data"), _make_node("mod.1", "MODULE", "Auth Module")]
    graph = _make_graph(nodes)
    graph.nodes_tracing_to.side_effect = lambda nid: ["mod.1"] if nid == "hlr.1" else []
    result = json.loads(GraphTraceTool(graph=graph)._execute(operation="coverage", node_type="HLR"))
    assert len(result) == 2
    assert result[0]["traced_by"] == ["MODULE:mod.1"]
    assert result[1]["traced_by"] == ["(none)"]


def test_graph_trace_errors() -> None:
    tool = GraphTraceTool(graph=_make_graph([]))
    assert "ERROR" in tool._execute(operation="traced_by", node_id="")
    assert "Unknown operation" in tool._execute(operation="invalid")
