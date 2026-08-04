"""High-risk path coverage for workspace_sync — the drift and refresh paths.

These are the cases where a bad implementation would silently launder
drift as compliance or fail to surface a real problem.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.analysis.gaps import Gap, GapType
from backend.crew.workspace_sync import (
    _find_child_of_type,
    _hash,
    _sync_code_nodes,
    _sync_test_nodes,
    workspace_sync,
)


def _node(nid: str, ntype: str, **kw: Any) -> SimpleNamespace:
    return SimpleNamespace(
        node_id=nid, node_type=ntype,
        parent_id=kw.get("parent_id", ""),
        title=kw.get("title", ""),
        content=kw.get("content", ""),
        trace_to=kw.get("trace_to", []),
        properties=kw.get("properties", {}),
    )


class _Graph:
    def __init__(self, nodes: list[SimpleNamespace]) -> None:
        self._by_id = {n.node_id: n for n in nodes}
        self._alloc = 0
        self.add_node = AsyncMock()
        self.update_node = AsyncMock()

    def all_nodes(self) -> list[SimpleNamespace]:
        return list(self._by_id.values())

    def node_sync(self, nid: str) -> SimpleNamespace | None:
        return self._by_id.get(nid)

    def children_sync(self, pid: str) -> list[SimpleNamespace]:
        return [n for n in self._by_id.values() if n.parent_id == pid]

    async def allocate_node_id(self, prefix: str) -> str:
        self._alloc += 1
        return f"{prefix}-{self._alloc:04d}"


@pytest.mark.asyncio
async def test_refresh_updates_when_file_content_changed(tmp_path: Path) -> None:
    """Previously-generated CODE node refreshes when on-disk content diverges."""
    design = _node(
        "DES-1", "DESIGN",
        properties={"file_path": "src/m.py"},
        title="M",
    )
    existing_code = _node(
        "CODE-1", "CODE",
        parent_id="DES-1",
        properties={
            "file_path": "src/m.py",
            "file_content": "OLD",
            "file_hash": _hash("OLD"),
        },
    )
    graph = _Graph([design, existing_code])

    src = tmp_path / "src" / "m.py"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("NEW", encoding="utf-8")

    gaps: list[Gap] = []
    created, refreshed = await _sync_code_nodes(graph, tmp_path, gaps)

    assert created == 0
    assert refreshed == 1
    assert gaps == []
    # update_node was called with new file_content
    kwargs = graph.update_node.call_args.kwargs
    assert kwargs["properties"]["file_content"] == "NEW"
    assert kwargs["properties"]["file_hash"] == _hash("NEW")


@pytest.mark.asyncio
async def test_no_refresh_when_content_unchanged(tmp_path: Path) -> None:
    design = _node(
        "DES-1", "DESIGN",
        properties={"file_path": "src/m.py"},
        title="M",
    )
    existing_code = _node(
        "CODE-1", "CODE",
        parent_id="DES-1",
        properties={"file_path": "src/m.py", "file_content": "SAME"},
    )
    graph = _Graph([design, existing_code])

    src = tmp_path / "src" / "m.py"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("SAME", encoding="utf-8")

    gaps: list[Gap] = []
    created, refreshed = await _sync_code_nodes(graph, tmp_path, gaps)
    assert created == 0
    assert refreshed == 0
    graph.update_node.assert_not_called()


@pytest.mark.asyncio
async def test_missing_file_emits_gap_and_skips(tmp_path: Path) -> None:
    design = _node(
        "DES-1", "DESIGN",
        properties={"file_path": "src/gone.py"},
        title="G",
    )
    graph = _Graph([design])
    gaps: list[Gap] = []
    created, refreshed = await _sync_code_nodes(graph, tmp_path, gaps)
    assert created == 0
    assert refreshed == 0
    assert len(gaps) == 1
    assert gaps[0].type == GapType.MISSING_CODE
    assert gaps[0].context["file_path"] == "src/gone.py"
    graph.add_node.assert_not_called()


@pytest.mark.asyncio
async def test_test_sync_refreshes_and_updates_test_functions(tmp_path: Path) -> None:
    case = _node(
        "CASE-1", "CASE_HLR",
        properties={"file_path": "tests/test_x.py"},
        title="X",
    )
    existing_test = _node(
        "TEST-1", "TEST",
        parent_id="CASE-1",
        properties={
            "file_path": "tests/test_x.py",
            "file_content": "old content",
            "test_functions": ["test_old"],
        },
    )
    graph = _Graph([case, existing_test])

    f = tmp_path / "tests" / "test_x.py"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("def test_new(): pass", encoding="utf-8")

    gaps: list[Gap] = []
    with patch("backend.crew.workspace_sync.analyse_traces") as at:
        at.return_value = MagicMock(traces=[MagicMock(symbol="test_new")])
        created, refreshed = await _sync_test_nodes(graph, tmp_path, gaps)
    assert created == 0
    assert refreshed == 1
    new_props = graph.update_node.call_args.kwargs["properties"]
    assert new_props["test_functions"] == ["test_new"]
    assert new_props["file_content"] == "def test_new(): pass"


@pytest.mark.asyncio
async def test_test_missing_file_emits_gap(tmp_path: Path) -> None:
    case = _node("CASE-1", "CASE_LLR", properties={"file_path": "tests/gone.py"})
    graph = _Graph([case])
    gaps: list[Gap] = []
    count, refreshed = await _sync_test_nodes(graph, tmp_path, gaps)
    assert count == 0
    assert refreshed == 0
    assert len(gaps) == 1
    assert gaps[0].type == GapType.MISSING_CODE


@pytest.mark.asyncio
async def test_workspace_sync_attaches_detected_gaps_to_flow(tmp_path: Path) -> None:
    design = _node(
        "DES-1", "DESIGN", properties={"file_path": "src/gone.py"}
    )
    graph = _Graph([design])
    flow = MagicMock()
    flow.graph = graph
    flow._workspace = tmp_path

    await workspace_sync(flow, phase=13)
    assert hasattr(flow, "_workspace_sync_gaps")
    assert len(flow._workspace_sync_gaps) == 1
    assert flow._workspace_sync_gaps[0].type == GapType.MISSING_CODE


def test_find_child_returns_first_match_of_type() -> None:
    parent = _node("P1", "DESIGN")
    c1 = _node("C1", "CODE", parent_id="P1")
    c2 = _node("C2", "TEST", parent_id="P1")
    graph = _Graph([parent, c1, c2])
    assert _find_child_of_type(graph, "P1", "CODE") is c1
    assert _find_child_of_type(graph, "P1", "TEST") is c2
    assert _find_child_of_type(graph, "P1", "NONE") is None
