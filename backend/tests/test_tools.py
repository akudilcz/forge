"""Tests for ForgeTool implementations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.tools.file_read import FileReadTool
from backend.tools.file_write import FileWriteTool
from backend.tools.graph_read import GraphReadTool
from backend.tools.graph_write import GraphWriteTool

if TYPE_CHECKING:
    from backend.graph.models import GraphNode

# ── FileReadTool ──────────────────────────────────────────────────────────────

def test_file_read_existing_file(tmp_path: Path) -> None:
    f = tmp_path / "hello.txt"
    f.write_text("hello world")
    tool = FileReadTool(workspace=str(tmp_path))
    assert "   1 | hello world" in tool._execute(path="hello.txt")


@pytest.mark.parametrize(("path", "setup"), [
    ("nope.txt", None),
    ("subdir", "dir"),
])
def test_file_read_errors(tmp_path: Path, path: str, setup: str | None) -> None:
    if setup == "dir":
        (tmp_path / path).mkdir()
    tool = FileReadTool(workspace=str(tmp_path))
    assert "ERROR" in tool._execute(path=path)


# ── FileWriteTool ─────────────────────────────────────────────────────────────

def test_file_write_creates_file(tmp_path: Path) -> None:
    tool = FileWriteTool(workspace=str(tmp_path))
    tool._execute(path="out.txt", content="hello")
    assert (tmp_path / "out.txt").read_text() == "hello"


def test_file_write_creates_subdirs(tmp_path: Path) -> None:
    tool = FileWriteTool(workspace=str(tmp_path))
    tool._execute(path="sub/dir/file.txt", content="data")
    assert (tmp_path / "sub" / "dir" / "file.txt").exists()


def test_file_write_rejects_invalid_python(tmp_path: Path) -> None:
    tool = FileWriteTool(workspace=str(tmp_path))
    result = tool._execute(path="bad.py", content="def broken(\n")
    assert "REJECTED" in result
    assert "line" in result.lower()
    assert not (tmp_path / "bad.py").exists()


def test_file_write_path_escape_raises(tmp_path: Path) -> None:
    import pytest

    workspace = tmp_path / "ws"
    workspace.mkdir()
    tool = FileWriteTool(workspace=str(workspace))
    with pytest.raises(ValueError, match="outside the workspace"):
        tool._execute(path="../evil.txt", content="data")
    assert not (tmp_path / "evil.txt").exists()


def test_file_write_absolute_path_escape_raises(tmp_path: Path) -> None:
    import pytest

    workspace = tmp_path / "ws"
    workspace.mkdir()
    tool = FileWriteTool(workspace=str(workspace))
    outside = tmp_path / "evil.txt"
    with pytest.raises(ValueError, match="outside the workspace"):
        tool._execute(path=str(outside), content="data")
    assert not outside.exists()


# ── GraphReadTool ─────────────────────────────────────────────────────────────

def test_graph_read_no_graph() -> None:
    assert "ERROR" in GraphReadTool(graph=None)._execute(operation="node", node_id="req.1")


def test_graph_read_node_found() -> None:
    from backend.graph.models import GraphNode, NodeType
    node = GraphNode(node_id="req.1", node_type=NodeType.HLR.value, title="R1")
    mock_graph = MagicMock()
    mock_graph.node = AsyncMock(return_value=node)
    mock_graph.all_nodes = MagicMock(return_value=[node])
    result = GraphReadTool(graph=mock_graph)._execute(operation="node", node_id="req.1")
    assert "req.1" in result


def test_graph_read_node_not_found() -> None:
    mock_graph = MagicMock()
    mock_graph.node = AsyncMock(return_value=None)
    result = GraphReadTool(graph=mock_graph)._execute(operation="node", node_id="missing")
    assert "not found" in result.lower() or "missing" in result


def test_graph_read_nodes_with_filters() -> None:
    from backend.graph.models import GraphNode, NodeType
    nodes = [
        GraphNode(node_id="req.1", node_type=NodeType.HLR.value, title="R1"),
        GraphNode(node_id="llr.1.ctr.abc", node_type=NodeType.CONTRACT.value, title="C1"),
    ]
    mock_graph = MagicMock()
    mock_graph.all_nodes = MagicMock(return_value=nodes)
    tool = GraphReadTool(graph=mock_graph)

    # prefix filter
    data = json.loads(tool._execute(operation="nodes", type_prefix="req."))
    assert all(n["node_id"].startswith("req.") for n in data)

    # node_type filter
    data = json.loads(tool._execute(operation="nodes", node_type="CONTRACT"))
    assert all(n["type"] == NodeType.CONTRACT.value for n in data)


def test_graph_read_nodes_properties_included() -> None:
    from backend.graph.models import GraphNode, NodeType
    node = GraphNode(
        node_id="llr.1.ctr.abc", node_type=NodeType.CONTRACT.value, title="C1",
        properties={"extra": "value"},
    )
    mock_graph = MagicMock()
    mock_graph.all_nodes = MagicMock(return_value=[node])
    data = json.loads(GraphReadTool(graph=mock_graph)._execute(operation="nodes"))
    assert data[0]["properties"]["extra"] == "value"


def test_graph_read_unknown_operation() -> None:
    assert "Unknown" in GraphReadTool(graph=MagicMock())._execute(operation="explode")


def test_graph_read_siblings() -> None:
    from backend.graph.models import GraphNode, NodeType
    sibling = GraphNode(node_id="par.c2", node_type=NodeType.MODULE.value, title="Sibling")
    mock_graph = MagicMock()
    mock_graph.siblings_sync.return_value = [sibling]
    assert "par.c2" in GraphReadTool(graph=mock_graph)._execute(operation="siblings", node_id="par.c1")


# ── GraphWriteTool ────────────────────────────────────────────────────────────

def test_graph_write_no_graph() -> None:
    assert "ERROR" in GraphWriteTool(graph=None)._execute(operation="add_node", node_id="x")


def test_graph_write_add_node() -> None:
    from backend.graph.models import GraphNode, NodeType
    new_node = GraphNode(node_id="req.2", node_type=NodeType.HLR.value, title="R2")
    mock_graph = MagicMock()
    mock_graph.add_node = AsyncMock(return_value=new_node)
    result = GraphWriteTool(graph=mock_graph)._execute(
        operation="add_node", node_id="req.2", node_type="req", title="R2",
        content="requirement text", properties="{}",
    )
    assert "OK" in result
    assert "req.2" in result


def test_graph_write_add_node_with_parent_id() -> None:
    captured: dict[str, GraphNode] = {}

    async def capture_add_node(node: GraphNode) -> GraphNode:
        captured["node"] = node
        return node

    mock_graph = MagicMock()
    mock_graph.add_node = capture_add_node
    result = GraphWriteTool(graph=mock_graph)._execute(
        operation="add_node", node_id="req.hlr.abc.0001", node_type="REQ",
        parent_id="doc.whitepaper.par.abc.0001", content="The system shall do X.",
    )
    assert "OK" in result
    assert captured["node"].parent_id == "doc.whitepaper.par.abc.0001"


def test_graph_write_add_node_empty_parent_becomes_none() -> None:
    captured: dict[str, GraphNode] = {}

    async def capture_add_node(node: GraphNode) -> GraphNode:
        captured["node"] = node
        return node

    mock_graph = MagicMock()
    mock_graph.add_node = capture_add_node
    GraphWriteTool(graph=mock_graph)._execute(
        operation="add_node", node_id="req.hlr.xyz", node_type="REQ", content="something",
    )
    assert captured["node"].parent_id is None


def test_graph_write_add_node_auto_generates_id() -> None:
    captured: dict[str, GraphNode] = {}

    async def capture_add_node(node: GraphNode) -> GraphNode:
        captured["node"] = node
        return node

    async def mock_allocate(node_type: str) -> str:
        return f"{node_type.upper()}-0001"

    mock_graph = MagicMock()
    mock_graph.add_node = capture_add_node
    mock_graph.allocate_node_id = mock_allocate
    result = GraphWriteTool(graph=mock_graph)._execute(
        operation="add_node", node_type="REQ",
        parent_id="doc.spec.par.abc.0001", content="The system shall do X.",
    )
    assert "OK" in result
    assert captured["node"].node_id == "REQ-0001"


def test_graph_write_update_node() -> None:
    from backend.graph.models import GraphNode, ImpactSet, NodeType
    existing = GraphNode(node_id="req.2", node_type=NodeType.HLR.value, title="R2")
    mock_graph = MagicMock()
    mock_graph.update_node = AsyncMock(return_value=(existing, ImpactSet(root_node_id="req.2")))
    result = GraphWriteTool(graph=mock_graph)._execute(
        operation="update_node", node_id="req.2", content="updated text", reason="test",
    )
    assert "OK" in result


def test_graph_write_add_edge() -> None:
    from backend.graph.models import EdgeType, GraphEdge
    edge = GraphEdge(edge_type=EdgeType.DERIVES_FROM.value, source_id="req.2", target_id="doc.spec")
    mock_graph = MagicMock()
    mock_graph.add_edge = AsyncMock(return_value=edge)
    result = GraphWriteTool(graph=mock_graph)._execute(
        operation="add_edge", edge_type="DERIVES_FROM", source_id="req.2", target_id="doc.spec",
    )
    assert "OK" in result


def test_graph_write_delete_node() -> None:
    mock_graph = MagicMock()
    mock_graph.delete_node = AsyncMock(return_value=None)
    result = GraphWriteTool(graph=mock_graph)._execute(operation="delete_node", node_id="doc.spec.par.001")
    assert "OK" in result
    assert "doc.spec.par.001" in result


def test_graph_write_reparent_node() -> None:
    from backend.graph.models import GraphNode, NodeType
    moved = GraphNode(node_id="req.child", node_type=NodeType.HLR.value, title="Child", parent_id="doc.new")
    mock_graph = MagicMock()
    mock_graph.reparent_node = AsyncMock(return_value=moved)
    result = GraphWriteTool(graph=mock_graph)._execute(
        operation="reparent_node", node_id="req.child", parent_id="doc.new", reason="simplification",
    )
    assert "OK" in result
    mock_graph.reparent_node.assert_called_once_with("req.child", "doc.new", "agent", "simplification")


def test_graph_write_reparent_rejects_para_under_hlr() -> None:
    """Reparenting a PARA under an HLR should be rejected — prevents cross-layer corruption."""
    from backend.graph.models import GraphNode, NodeType

    para = GraphNode(node_id="PARA-0003", node_type=NodeType.PARA.value, title="Para")
    hlr = GraphNode(node_id="HLR-0002", node_type=NodeType.HLR.value, title="HLR")

    mock_graph = MagicMock()
    mock_graph.node = AsyncMock(side_effect=lambda nid: para if nid == "PARA-0003" else hlr)
    mock_graph.reparent_node = AsyncMock()

    result = GraphWriteTool(graph=mock_graph)._execute(
        operation="reparent_node", node_id="PARA-0003", parent_id="HLR-0002",
    )
    assert "ERROR" in result
    assert "Cannot reparent" in result
    assert "DOCUMENT" in result  # should tell agent valid parents
    mock_graph.reparent_node.assert_not_called()


def test_graph_write_reparent_allows_llr_under_hlr() -> None:
    """Reparenting an LLR under an HLR is valid and should succeed."""
    from backend.graph.models import GraphNode, NodeType

    llr = GraphNode(node_id="LLR-0001", node_type=NodeType.LLR.value, title="LLR")
    hlr = GraphNode(node_id="HLR-0001", node_type=NodeType.HLR.value, title="HLR")

    mock_graph = MagicMock()
    mock_graph.node = AsyncMock(side_effect=lambda nid: llr if nid == "LLR-0001" else hlr)
    mock_graph.reparent_node = AsyncMock()

    result = GraphWriteTool(graph=mock_graph)._execute(
        operation="reparent_node", node_id="LLR-0001", parent_id="HLR-0001",
    )
    assert "OK" in result
    mock_graph.reparent_node.assert_called_once()


def test_graph_write_reparent_rejects_orphaning_move() -> None:
    """Reparenting the only HLR under a PARA should be rejected."""
    from backend.graph.models import GraphNode, NodeType

    hlr = GraphNode(
        node_id="HLR-0001", node_type=NodeType.HLR.value,
        title="Only HLR", parent_id="PARA-0001",
    )
    para_old = GraphNode(
        node_id="PARA-0001", node_type="PARA", title="Old Parent",
    )
    para_new = GraphNode(
        node_id="PARA-0002", node_type="PARA", title="New Parent",
    )

    mock_graph = MagicMock()
    mock_graph.node = AsyncMock(
        side_effect=lambda nid: {"HLR-0001": hlr, "PARA-0001": para_old, "PARA-0002": para_new}[nid],
    )
    # HLR-0001 is the ONLY HLR child of PARA-0001
    mock_graph.children_sync.return_value = [hlr]
    mock_graph.reparent_node = AsyncMock()

    result = GraphWriteTool(graph=mock_graph)._execute(
        operation="reparent_node", node_id="HLR-0001", parent_id="PARA-0002",
    )
    assert "ERROR" in result
    assert "only" in result.lower()
    mock_graph.reparent_node.assert_not_called()


def test_graph_write_reparent_allows_when_siblings_remain() -> None:
    """Reparenting an HLR is allowed when siblings remain under the source PARA."""
    from backend.graph.models import GraphNode, NodeType

    hlr1 = GraphNode(
        node_id="HLR-0001", node_type=NodeType.HLR.value,
        title="HLR 1", parent_id="PARA-0001",
    )
    hlr2 = GraphNode(
        node_id="HLR-0002", node_type=NodeType.HLR.value,
        title="HLR 2", parent_id="PARA-0001",
    )
    para_old = GraphNode(node_id="PARA-0001", node_type="PARA", title="Parent")
    para_new = GraphNode(node_id="PARA-0002", node_type="PARA", title="New")

    mock_graph = MagicMock()
    mock_graph.node = AsyncMock(
        side_effect=lambda nid: {
            "HLR-0001": hlr1, "HLR-0002": hlr2,
            "PARA-0001": para_old, "PARA-0002": para_new,
        }[nid],
    )
    # Two HLR children — moving one leaves the other
    mock_graph.children_sync.return_value = [hlr1, hlr2]
    mock_graph.reparent_node = AsyncMock()

    result = GraphWriteTool(graph=mock_graph)._execute(
        operation="reparent_node", node_id="HLR-0001", parent_id="PARA-0002",
    )
    assert "OK" in result
    mock_graph.reparent_node.assert_called_once()


def test_graph_write_unknown_operation() -> None:
    assert "Unknown" in GraphWriteTool(graph=MagicMock())._execute(operation="explode")


def test_graph_write_update_trace_stores_trace_to() -> None:
    existing_node = MagicMock()
    existing_node.properties = {"req_level": "llr"}
    existing_node.trace_to = []
    mock_graph = MagicMock()
    mock_graph.node_sync.return_value = existing_node
    mock_graph.update_node = AsyncMock(return_value=(MagicMock(), MagicMock()))
    result = GraphWriteTool(graph=mock_graph)._execute(
        operation="update_trace", node_id="mod.1", trace_to='["req.llr.1"]',
    )
    assert "OK" in result
    assert mock_graph.update_node.call_args[1]["trace_to"] == ["req.llr.1"]


def test_graph_write_update_trace_rejects_invalid_json() -> None:
    existing_node = MagicMock()
    existing_node.properties = {}
    mock_graph = MagicMock()
    mock_graph.node_sync.return_value = existing_node
    result = GraphWriteTool(graph=mock_graph)._execute(
        operation="update_trace", node_id="mod.1", trace_to="not-valid-json",
    )
    assert "ERROR" in result


def test_graph_write_add_traces_appends_and_deduplicates() -> None:
    existing_node = MagicMock()
    existing_node.trace_to = ["HLR-0001", "HLR-0002"]
    existing_node.properties = {}
    mock_graph = MagicMock()
    mock_graph.node_sync.return_value = existing_node
    mock_graph.update_node = AsyncMock(return_value=(MagicMock(), MagicMock()))
    tool = GraphWriteTool(graph=mock_graph)

    # new item appended
    tool._execute(operation="add_traces", node_id="mod.1", trace_to='["HLR-0003"]')
    merged = mock_graph.update_node.call_args[1]["trace_to"]
    assert "HLR-0003" in merged
    assert "HLR-0001" in merged

    # duplicate not added — no update_node call, returns early
    existing_node.trace_to = ["HLR-0001"]
    mock_graph.update_node.reset_mock()
    result = tool._execute(operation="add_traces", node_id="mod.1", trace_to='["HLR-0001"]')
    assert "no new traces" in result
    mock_graph.update_node.assert_not_called()


def test_graph_write_remove_traces() -> None:
    existing_node = MagicMock()
    existing_node.trace_to = ["HLR-0001", "HLR-0002", "HLR-0003"]
    existing_node.properties = {}
    mock_graph = MagicMock()
    mock_graph.node_sync.return_value = existing_node
    mock_graph.update_node = AsyncMock(return_value=(MagicMock(), MagicMock()))
    GraphWriteTool(graph=mock_graph)._execute(
        operation="remove_traces", node_id="mod.1", trace_to='["HLR-0002"]',
    )
    remaining = mock_graph.update_node.call_args[1]["trace_to"]
    assert remaining == ["HLR-0001", "HLR-0003"]
