"""Tests for deliverables_helpers — ALL functions."""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from backend.rendering.deliverables_helpers import (
    build_trace_map,
    node_lookup,
    nodes_by_type,
    pct,
    req_section,
    write_file,
)

# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_node(
    node_id: str,
    node_type: str,
    title: str | None = "Title",
    content: str = "",
    trace_to: list[str] | None = None,
) -> MagicMock:
    node = MagicMock()
    node.node_id = node_id
    node.node_type = node_type
    node.title = title
    node.content = content
    node.trace_to = trace_to or []
    return node


def _make_graph(nodes: list[MagicMock]) -> MagicMock:
    graph = MagicMock()
    graph.all_nodes.return_value = nodes
    return graph


# ── write_file ───────────────────────────────────────────────────────────────


def test_write_file_creates_and_writes(tmp_path: Path) -> None:
    """write_file writes UTF-8 content to the specified path."""
    target = tmp_path / "output.txt"
    write_file(target, "hello world")
    assert target.read_text(encoding="utf-8") == "hello world"


def test_write_file_overwrites_existing(tmp_path: Path) -> None:
    """write_file overwrites existing file content."""
    target = tmp_path / "output.txt"
    target.write_text("old content", encoding="utf-8")
    write_file(target, "new content")
    assert target.read_text(encoding="utf-8") == "new content"


def test_write_file_unicode(tmp_path: Path) -> None:
    """write_file handles unicode content correctly."""
    target = tmp_path / "unicode.txt"
    write_file(target, "Requirement: 100% coverage \u2014 pass/fail")
    assert "\u2014" in target.read_text(encoding="utf-8")


# ── nodes_by_type ────────────────────────────────────────────────────────────


def test_nodes_by_type_single_type() -> None:
    """Filter nodes by a single type."""
    nodes = [
        _make_node("H-1", "HLR"),
        _make_node("D-1", "DESIGN"),
        _make_node("H-2", "HLR"),
    ]
    graph = _make_graph(nodes)
    result = nodes_by_type(graph, "HLR")
    assert [n.node_id for n in result] == ["H-1", "H-2"]


def test_nodes_by_type_multiple_types() -> None:
    """Filter nodes by multiple types."""
    nodes = [
        _make_node("H-1", "HLR"),
        _make_node("L-1", "LLR"),
        _make_node("D-1", "DESIGN"),
    ]
    graph = _make_graph(nodes)
    result = nodes_by_type(graph, "HLR", "LLR")
    assert [n.node_id for n in result] == ["H-1", "L-1"]


def test_nodes_by_type_no_matches() -> None:
    """Returns empty list when no nodes match."""
    nodes = [_make_node("D-1", "DESIGN")]
    graph = _make_graph(nodes)
    result = nodes_by_type(graph, "HLR")
    assert result == []


def test_nodes_by_type_sorted_by_node_id() -> None:
    """Results are sorted by node_id."""
    nodes = [
        _make_node("H-3", "HLR"),
        _make_node("H-1", "HLR"),
        _make_node("H-2", "HLR"),
    ]
    graph = _make_graph(nodes)
    result = nodes_by_type(graph, "HLR")
    assert [n.node_id for n in result] == ["H-1", "H-2", "H-3"]


def test_nodes_by_type_empty_graph() -> None:
    """Empty graph returns empty list."""
    graph = _make_graph([])
    result = nodes_by_type(graph, "HLR")
    assert result == []


# ── node_lookup ──────────────────────────────────────────────────────────────


def test_node_lookup_builds_dict() -> None:
    """Builds node_id -> node dict from all graph nodes."""
    nodes = [_make_node("H-1", "HLR"), _make_node("D-1", "DESIGN")]
    graph = _make_graph(nodes)
    lookup = node_lookup(graph)
    assert set(lookup.keys()) == {"H-1", "D-1"}
    assert lookup["H-1"].node_type == "HLR"


def test_node_lookup_empty_graph() -> None:
    """Empty graph returns empty dict."""
    graph = _make_graph([])
    assert node_lookup(graph) == {}


# ── build_trace_map ──────────────────────────────────────────────────────────


def test_build_trace_map_with_traces() -> None:
    """Nodes with trace_to produce reverse mapping."""
    nodes = [
        _make_node("C-1", "CASE_HLR", trace_to=["H-1", "H-2"]),
        _make_node("C-2", "CASE_HLR", trace_to=["H-1"]),
    ]
    result = build_trace_map(nodes)
    assert result == {"H-1": ["C-1", "C-2"], "H-2": ["C-1"]}


def test_build_trace_map_no_traces() -> None:
    """Nodes without trace_to produce empty map."""
    nodes = [_make_node("D-1", "DESIGN", trace_to=[])]
    result = build_trace_map(nodes)
    assert result == {}


def test_build_trace_map_empty_list() -> None:
    """Empty node list produces empty map."""
    assert build_trace_map([]) == {}


def test_build_trace_map_none_trace_to() -> None:
    """Nodes with trace_to=None are handled gracefully."""
    node = _make_node("D-1", "DESIGN")
    node.trace_to = None
    # build_trace_map iterates (n.trace_to or []) so None -> []
    result = build_trace_map([node])
    assert result == {}


# ── pct ──────────────────────────────────────────────────────────────────────


def test_pct_normal_case() -> None:
    """Normal percentage calculation."""
    assert pct(3, 4) == "75%"


def test_pct_zero_denominator() -> None:
    """Zero denominator returns em dash."""
    assert pct(0, 0) == "\u2014"
    assert pct(5, 0) == "\u2014"


def test_pct_100_percent() -> None:
    """100% case."""
    assert pct(10, 10) == "100%"


def test_pct_zero_numerator() -> None:
    """Zero numerator with non-zero denominator."""
    assert pct(0, 5) == "0%"


def test_pct_integer_division() -> None:
    """Uses integer division (floor), not rounding."""
    # 1/3 = 33.33... → floor to 33%
    assert pct(1, 3) == "33%"


# ── req_section ──────────────────────────────────────────────────────────────


def test_req_section_with_content() -> None:
    """Renders heading, blank line, content, blank line."""
    node = _make_node("H-1", "HLR", title="Auth Login", content="User must authenticate.")
    lookup: dict[str, Any] = {}
    lines = req_section(node, lookup)
    assert lines[0] == "### H-1: Auth Login"
    assert lines[1] == ""
    assert lines[2] == "User must authenticate."
    assert lines[3] == ""


def test_req_section_without_content() -> None:
    """Node without content renders heading and blank line only."""
    node = _make_node("H-2", "HLR", title="Empty Req", content="")
    lookup: dict[str, Any] = {}
    lines = req_section(node, lookup)
    assert lines == ["### H-2: Empty Req", ""]


def test_req_section_custom_heading() -> None:
    """Custom heading level is used."""
    node = _make_node("L-1", "LLR", title="Detail Req", content="Details.")
    lookup: dict[str, Any] = {}
    lines = req_section(node, lookup, heading="####")
    assert lines[0] == "#### L-1: Detail Req"


def test_req_section_untitled_node() -> None:
    """Node with no title renders (untitled)."""
    node = _make_node("H-3", "HLR", title=None, content="Some content.")
    lookup: dict[str, Any] = {}
    lines = req_section(node, lookup)
    assert lines[0] == "### H-3: (untitled)"


def test_req_section_content_stripped() -> None:
    """Content whitespace is stripped."""
    node = _make_node("H-4", "HLR", title="Trimmed", content="  padded content  \n\n")
    lookup: dict[str, Any] = {}
    lines = req_section(node, lookup)
    assert lines[2] == "padded content"
