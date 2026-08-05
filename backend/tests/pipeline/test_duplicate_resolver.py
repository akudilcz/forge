"""Tests for the deterministic byte-identical duplicate resolver.

Byte-identical sibling duplicates (gap context carries ``duplicate_of``)
must be resolved without any LLM dispatch: younger node deleted, trace_to
merged into the canonical, loud log. Near-duplicates still dispatch.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.pipeline.duplicate_resolver import try_resolve_exact_duplicate


def _node(
    nid: str,
    parent: str,
    content: str,
    trace_to: list[str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        node_id=nid,
        node_type="LLR",
        parent_id=parent,
        content=content,
        trace_to=trace_to or [],
        title="t",
    )


class _FakeGraph:
    def __init__(self, nodes: list[SimpleNamespace]) -> None:
        self.nodes = {n.node_id: n for n in nodes}
        self.deleted: list[str] = []
        self.updates: list[tuple[str, list[str]]] = []
        self.fail_delete = False

    def node_sync(self, nid: str) -> Any:
        return self.nodes.get(nid)

    async def update_node(
        self,
        node_id: str,
        content: Any,
        properties: Any,
        changed_by: str,
        change_reason: str,
        trace_to: list[str] | None = None,
    ) -> Any:
        self.updates.append((node_id, list(trace_to or [])))
        if trace_to is not None:
            self.nodes[node_id].trace_to = trace_to
        return (self.nodes[node_id], None)

    async def delete_node(self, nid: str) -> None:
        self.deleted.append(nid)
        if not self.fail_delete:
            del self.nodes[nid]


def _dup_gap(node_id: str, canonical: str) -> Gap:
    return Gap(
        type=GapType.DUPLICATE_NODE,
        priority=GapPriority.MAINTENANCE,
        node_id=node_id,
        description="exact duplicate",
        context={"duplicate_of": canonical},
    )


def _flow(graph: _FakeGraph) -> SimpleNamespace:
    return SimpleNamespace(graph=graph)


# ── happy path ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_byte_identical_deleted_without_dispatch(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Byte-identical dup is deleted deterministically with a loud log."""
    graph = _FakeGraph(
        [
            _node("LLR-0001", "HLR-0001", "The system shall X.", ["HLR-0001"]),
            _node("LLR-0002", "HLR-0001", "The system shall X.", ["HLR-0009"]),
        ]
    )
    with caplog.at_level(logging.INFO, logger="backend.pipeline.duplicate_resolver"):
        resolved = await try_resolve_exact_duplicate(_flow(graph), _dup_gap("LLR-0002", "LLR-0001"))
    assert resolved is True
    assert graph.deleted == ["LLR-0002"]
    assert "LLR-0002" not in graph.nodes
    assert any("exact_duplicate_deleted" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_trace_to_merged_into_canonical() -> None:
    """The duplicate's unique trace_to refs are merged into the canonical."""
    graph = _FakeGraph(
        [
            _node("LLR-0001", "HLR-0001", "same content", ["HLR-0001"]),
            _node("LLR-0002", "HLR-0001", "Same Content  ", ["HLR-0001", "HLR-0009"]),
        ]
    )
    resolved = await try_resolve_exact_duplicate(_flow(graph), _dup_gap("LLR-0002", "LLR-0001"))
    assert resolved is True
    assert graph.nodes["LLR-0001"].trace_to == ["HLR-0001", "HLR-0009"]


@pytest.mark.asyncio
async def test_no_update_when_no_extra_traces() -> None:
    """No update_node call when the duplicate brings no new trace_to refs."""
    graph = _FakeGraph(
        [
            _node("LLR-0001", "HLR-0001", "same", ["HLR-0001"]),
            _node("LLR-0002", "HLR-0001", "same", ["HLR-0001"]),
        ]
    )
    resolved = await try_resolve_exact_duplicate(_flow(graph), _dup_gap("LLR-0002", "LLR-0001"))
    assert resolved is True
    assert graph.updates == []


@pytest.mark.asyncio
async def test_already_deleted_dup_is_resolved() -> None:
    """A dup node that no longer exists counts as resolved — no dispatch."""
    graph = _FakeGraph([_node("LLR-0001", "HLR-0001", "same")])
    resolved = await try_resolve_exact_duplicate(_flow(graph), _dup_gap("LLR-0002", "LLR-0001"))
    assert resolved is True
    assert graph.deleted == []


# ── LLM path preserved ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_near_identical_content_falls_through_to_llm() -> None:
    """Content no longer byte-identical at resolution time → LLM path."""
    graph = _FakeGraph(
        [
            _node("LLR-0001", "HLR-0001", "The system shall X."),
            _node("LLR-0002", "HLR-0001", "The system shall Y."),
        ]
    )
    resolved = await try_resolve_exact_duplicate(_flow(graph), _dup_gap("LLR-0002", "LLR-0001"))
    assert resolved is False
    assert graph.deleted == []


@pytest.mark.asyncio
async def test_gap_without_duplicate_of_falls_through() -> None:
    """Semantic (near-duplicate) gaps have no duplicate_of — LLM path."""
    graph = _FakeGraph(
        [
            _node("LLR-0001", "HLR-0001", "same"),
            _node("LLR-0002", "HLR-0001", "same"),
        ]
    )
    gap = Gap(
        type=GapType.DUPLICATE_NODE,
        priority=GapPriority.MAINTENANCE,
        node_id="LLR-0002",
        description="possible semantic duplicate",
    )
    resolved = await try_resolve_exact_duplicate(_flow(graph), gap)
    assert resolved is False
    assert graph.deleted == []


@pytest.mark.asyncio
async def test_non_duplicate_gap_type_falls_through() -> None:
    graph = _FakeGraph([_node("LLR-0001", "HLR-0001", "x")])
    gap = Gap(
        type=GapType.STALE_NODE,
        priority=GapPriority.MAINTENANCE,
        node_id="LLR-0001",
        description="stale",
        context={"duplicate_of": "LLR-0001"},
    )
    assert await try_resolve_exact_duplicate(_flow(graph), gap) is False


@pytest.mark.asyncio
async def test_missing_canonical_falls_through() -> None:
    """Canonical node gone → cannot verify byte-identity → LLM path."""
    graph = _FakeGraph([_node("LLR-0002", "HLR-0001", "same")])
    resolved = await try_resolve_exact_duplicate(_flow(graph), _dup_gap("LLR-0002", "LLR-0001"))
    assert resolved is False
    assert graph.deleted == []


@pytest.mark.asyncio
async def test_different_parent_falls_through() -> None:
    """Nodes reparented since gap emission are no longer siblings → LLM path."""
    graph = _FakeGraph(
        [
            _node("LLR-0001", "HLR-0001", "same"),
            _node("LLR-0002", "HLR-0002", "same"),
        ]
    )
    resolved = await try_resolve_exact_duplicate(_flow(graph), _dup_gap("LLR-0002", "LLR-0001"))
    assert resolved is False


# ── loud failure ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_failed_deletion_raises() -> None:
    """If delete_node leaves the node in place, the resolver raises loudly."""
    graph = _FakeGraph(
        [
            _node("LLR-0001", "HLR-0001", "same"),
            _node("LLR-0002", "HLR-0001", "same"),
        ]
    )
    graph.fail_delete = True
    with pytest.raises(RuntimeError):
        await try_resolve_exact_duplicate(_flow(graph), _dup_gap("LLR-0002", "LLR-0001"))


# ── dispatch integration ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_short_circuits_byte_identical_dup() -> None:
    """dispatch() resolves byte-identical dups without touching the agent pool."""
    from backend.pipeline.dispatch import dispatch

    graph = _FakeGraph(
        [
            _node("LLR-0001", "HLR-0001", "same"),
            _node("LLR-0002", "HLR-0001", "same"),
        ]
    )
    flow = SimpleNamespace(graph=graph, pool=MagicMock())
    out = await dispatch(flow, _dup_gap("LLR-0002", "LLR-0001"), attempt=1)
    assert out  # non-empty marker
    assert graph.deleted == ["LLR-0002"]
    flow.pool.get_agent_for_gap.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_still_dispatches_near_identical() -> None:
    """Near-identical dup gaps still go to the agent pool (LLM path)."""
    from backend.pipeline.dispatch import dispatch

    graph = _FakeGraph(
        [
            _node("LLR-0001", "HLR-0001", "The system shall X."),
            _node("LLR-0002", "HLR-0001", "The system shall Y."),
        ]
    )
    pool = MagicMock()
    pool.get_agent_for_gap.return_value = None  # stop after pool lookup
    flow = SimpleNamespace(graph=graph, pool=pool)
    out = await dispatch(flow, _dup_gap("LLR-0002", "LLR-0001"), attempt=1)
    assert out == ""
    pool.get_agent_for_gap.assert_called_once()
    assert graph.deleted == []
