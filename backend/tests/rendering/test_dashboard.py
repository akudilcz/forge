"""Tests for the Phase 11 Dashboard renderer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from backend.rendering.dashboard import _render_phase_doc, render_dashboard


def _make_node(node_id: str, node_type: str, title: str = "Title", content: str = "Content",
               parent_id: str | None = None, trace_to: list[str] | None = None) -> MagicMock:
    node = MagicMock()
    node.node_id = node_id
    node.node_type = node_type
    node.title = title
    node.content = content
    node.parent_id = parent_id
    node.trace_to = trace_to or []
    node.lifecycle = "ACTIVE"
    node.version = 1
    return node


def _make_graph(
    nodes: list[MagicMock] | None = None,
    children_map: dict[str, list[MagicMock]] | None = None,
) -> MagicMock:
    graph = MagicMock()
    node_list = nodes or []
    node_dict = {n.node_id: n for n in node_list}
    graph.all_nodes.return_value = node_list
    graph.node_sync = lambda nid: node_dict.get(nid)
    children_map = children_map or {}
    graph.children_sync = lambda nid: children_map.get(nid, [])
    return graph


@pytest.mark.asyncio
async def test_render_dashboard_creates_files(tmp_path: Path) -> None:
    """Dashboard renders one .md file per phase into workspace/docs/."""
    hlr = _make_node("HLR-0001", "HLR", "Login Required",
                      "The system shall require login.")
    graph = _make_graph([hlr])

    written = await render_dashboard(graph, tmp_path)

    assert len(written) == 8
    assert (tmp_path / "docs").is_dir()
    hlr_doc = tmp_path / "docs" / "03-HLR.md"
    assert hlr_doc.exists()
    text = hlr_doc.read_text()
    assert "HLR-0001" in text
    assert "Login Required" in text
    assert "The system shall require login." in text


@pytest.mark.asyncio
async def test_render_dashboard_empty_graph(tmp_path: Path) -> None:
    """Dashboard produces docs with 'no nodes' message when graph is empty."""
    graph = _make_graph()
    written = await render_dashboard(graph, tmp_path)

    assert len(written) == 8
    text = (tmp_path / "docs" / "03-HLR.md").read_text()
    assert "No nodes produced" in text


def test_render_phase_doc_inlines_traced_requirements() -> None:
    """Node with trace_to inlines the full requirement text, not just IDs."""
    llr1 = _make_node("LLR-0001", "LLR", "Validate Token",
                       "Tokens must be validated before use.")
    llr2 = _make_node("LLR-0002", "LLR", "Hash Password",
                       "Passwords must be hashed with bcrypt.")
    module = _make_node("MODULE-0001", "MODULE", "Auth Module",
                         "Authentication module.")
    design = _make_node(
        "DESIGN-0001", "DESIGN", "Auth Design", "Design spec.",
        parent_id="MODULE-0001", trace_to=["LLR-0001", "LLR-0002"],
    )
    graph = _make_graph([llr1, llr2, module, design])

    md = _render_phase_doc(8, "Design Specifications", [design], graph)

    # Full requirement text is inlined
    assert "Tokens must be validated before use." in md
    assert "Passwords must be hashed with bcrypt." in md
    # Titles are shown
    assert "Validate Token" in md
    assert "Hash Password" in md
    # Parent is shown
    assert "Auth Module" in md


def test_render_phase_doc_inlines_contracts() -> None:
    """DESIGN nodes inline sibling CONTRACT content."""
    module = _make_node("MODULE-001", "MODULE", "Core", "Core module.")
    contract = _make_node("CONTRACT-001", "CONTRACT", "CoreAPI",
                           "class CoreService:\n    def process(self) -> Result")
    design = _make_node(
        "DESIGN-001", "DESIGN", "Core Impl", "Implementation.",
        parent_id="MODULE-001",
    )
    graph = _make_graph(
        [module, contract, design],
        children_map={"MODULE-001": [contract]},
    )

    md = _render_phase_doc(8, "Design Specifications", [design], graph)

    assert "Public Interface" in md
    assert "class CoreService" in md
    assert "def process(self) -> Result" in md


def test_render_phase_doc_multiple_nodes_sorted() -> None:
    """Multiple nodes appear in node_id order."""
    graph = _make_graph()
    n1 = _make_node("HLR-0002", "HLR", "B", "Second")
    n2 = _make_node("HLR-0001", "HLR", "A", "First")
    md = _render_phase_doc(3, "High-Level Requirements", [n2, n1], graph)
    pos_a = md.index("HLR-0001")
    pos_b = md.index("HLR-0002")
    assert pos_a < pos_b


def test_render_case_inlines_requirement_text() -> None:
    """CASE nodes inline the full requirement text they trace to."""
    llr = _make_node("LLR-0005", "LLR", "Rate Limiting",
                      "The system shall limit requests to 100/minute.")
    case = _make_node(
        "CASE-0001", "CASE_LLR", "Test Rate Limiting",
        "Verify that exceeding 100 requests triggers a 429 response.",
        trace_to=["LLR-0005"],
    )
    graph = _make_graph([llr, case])

    md = _render_phase_doc(10, "Verification Cases", [case], graph)

    assert "Rate Limiting" in md
    assert "limit requests to 100/minute" in md
    assert "exceeding 100 requests triggers a 429" in md
