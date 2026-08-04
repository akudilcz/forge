"""Tests for trace_manager — sync_traces delegates to code_gen."""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.crew.trace_manager import sync_traces


def _make_node(node_id: str, node_type: str, title: str,
               properties: dict[str, Any] | None = None,
               trace_to: list[str] | None = None) -> types.SimpleNamespace:
    """Create a minimal node object."""
    return types.SimpleNamespace(
        node_id=node_id,
        node_type=node_type,
        title=title,
        content="",
        parent_id=None,
        trace_to=trace_to or [],
        properties=dict(properties) if properties else {},
    )


def _make_graph(nodes: list[types.SimpleNamespace]) -> MagicMock:
    """Create a mock graph that supports all_nodes and node_sync."""
    node_map = {n.node_id: n for n in nodes}
    graph = MagicMock()
    graph.all_nodes.return_value = list(nodes)
    graph.node_sync.side_effect = lambda nid: node_map.get(nid)
    graph.update_node = AsyncMock()
    return graph


# ── Tests ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_persists_existing_source(tmp_path: Path) -> None:
    """Source file on disk gets traces persisted to its DESIGN node."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    (src_dir / "planner.py").write_text(
        '@traces("LLR-001")\ndef plan():\n    pass\n'
    )
    (tmp_path / "tests").mkdir()

    node = _make_node("DESIGN-001", "DESIGN", "Planner")
    graph = _make_graph([node])

    result = await sync_traces(graph, tmp_path)

    assert result["src"] == 1
    assert result["traces"] >= 1
    # update_node called to persist traces
    graph.update_node.assert_called()


@pytest.mark.asyncio
async def test_sync_persists_test_file(tmp_path: Path) -> None:
    """Test file on disk gets traces persisted to its CASE node."""
    (tmp_path / "src").mkdir()
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_planner.py").write_text(
        '@traces("LLR-001")\ndef test_plan():\n    assert True\n'
    )

    node = _make_node("CASE_HLR-001", "CASE_HLR", "Planner")
    graph = _make_graph([node])

    result = await sync_traces(graph, tmp_path)

    assert result["test"] == 1
    assert result["traces"] >= 1


@pytest.mark.asyncio
async def test_sync_clears_stale_node(tmp_path: Path) -> None:
    """CASE node with file_path from previous run gets cleaned up."""
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    # No test file on disk for this node

    stale = _make_node("CASE_LLR-OLD", "CASE_LLR", "Old Test", {
        "file_path": "tests/test_old_test.py",
        "line_traces": [{"start": 1, "end": 3, "llr_ids": ["LLR-001"]}],
        "trace_coverage": {"total": 1, "traced": 1},
    })
    graph = _make_graph([stale])

    result = await sync_traces(graph, tmp_path)

    assert result["test"] == 0
    # Stale node should have trace props cleared
    cleared_call = [
        c for c in graph.update_node.call_args_list
        if "stale" in (c.kwargs.get("change_reason") or c[1].get("change_reason", "")).lower()
    ]
    assert len(cleared_call) == 1


@pytest.mark.asyncio
async def test_sync_empty_workspace(tmp_path: Path) -> None:
    """Empty workspace returns zero counts."""
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()

    graph = _make_graph([])

    result = await sync_traces(graph, tmp_path)

    assert result["src"] == 0
    assert result["test"] == 0
    assert result["traces"] == 0
