"""Behavioural tests for GraphSearchTool fuzzy matching (backend/tools/graph_search.py)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from backend.tools.graph_search import GraphSearchTool, _content_similarity


def _node(node_id: str, node_type: str = "HLR", title: str = "", content: str = "") -> Any:
    return SimpleNamespace(
        node_id=node_id, node_type=node_type, title=title, content=content,
    )


def _tool(nodes: list[Any]) -> GraphSearchTool:
    graph = SimpleNamespace(all_nodes=lambda: nodes)
    return GraphSearchTool(graph=graph)


def test_no_graph_returns_error() -> None:
    assert GraphSearchTool()._execute(query="x") == "ERROR: Graph not available"


def test_title_field_skips_nodes_without_title() -> None:
    nodes = [
        _node("HLR-1", title="", content="authenticate users"),
        _node("HLR-2", title="authenticate users"),
    ]
    out = json.loads(_tool(nodes)._execute(query="authenticate users", field="title"))
    assert [r["node_id"] for r in out] == ["HLR-2"]
    assert out[0]["matched_field"] == "title"


def test_content_field_skips_nodes_without_content() -> None:
    nodes = [
        _node("HLR-1", title="authenticate users", content=""),
        _node("HLR-2", content="the system shall authenticate users"),
    ]
    out = json.loads(_tool(nodes)._execute(query="authenticate users", field="content"))
    assert [r["node_id"] for r in out] == ["HLR-2"]
    assert out[0]["matched_field"] == "content"


def test_no_results_below_threshold() -> None:
    nodes = [_node("HLR-1", title="zzzz")]
    out = _tool(nodes)._execute(query="authentication", threshold=0.9)
    assert out.startswith("No matches found for 'authentication'")


def test_node_type_filter_applies() -> None:
    nodes = [
        _node("HLR-1", node_type="HLR", title="login flow"),
        _node("LLR-1", node_type="LLR", title="login flow"),
    ]
    out = json.loads(_tool(nodes)._execute(query="login flow", node_type="llr"))
    assert [r["node_id"] for r in out] == ["LLR-1"]


def test_content_similarity_substring_short_circuits() -> None:
    assert _content_similarity("login", "\n\nusers login here\nother line") == 0.95


def test_content_similarity_best_line_wins() -> None:
    # First line scores higher than the second; blank lines are skipped.
    score = _content_similarity("alpha bexa", "alpha beta gamma\n\nzzzz")
    assert 0 < score < 0.95
