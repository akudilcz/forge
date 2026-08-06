"""Tests for the case trace coverage checker (plain text LLM)."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.quality.case_trace_check import (
    _remove_bad_traces,
    create_case_trace_checker,
)

# ── Fixtures ──────────────────────────────────────────────────────────────────


def _make_node(
    node_id: str,
    node_type: str,
    content: str = "test content",
    trace_to: list[str] | None = None,
) -> MagicMock:
    node = MagicMock()
    node.node_id = node_id
    node.node_type = node_type
    node.content = content
    node.trace_to = trace_to or []
    return node


def _make_graph(nodes: list[MagicMock]) -> MagicMock:
    graph = MagicMock()
    graph.all_nodes.return_value = nodes
    node_map = {n.node_id: n for n in nodes}
    graph.node_sync.side_effect = lambda nid: node_map.get(nid)
    graph.delete_node = AsyncMock()
    graph.update_node = AsyncMock()
    return graph


def _make_llm(response_text: str) -> MagicMock:
    llm = MagicMock()
    resp = MagicMock()
    resp.content = response_text
    llm.ainvoke = AsyncMock(return_value=resp)
    return llm


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_remove_bad_traces_deletes_empty_case() -> None:
    case = _make_node("C-1", "CASE_HLR", trace_to=["HLR-1"])
    sibling = _make_node("C-2", "CASE_HLR", trace_to=["HLR-1"])
    graph = _make_graph([case, sibling])
    removed = await _remove_bad_traces(graph, case, ["HLR-1"])
    assert removed == 1
    graph.delete_node.assert_awaited_once_with("C-1")


@pytest.mark.asyncio
async def test_remove_bad_traces_keeps_case_with_remaining() -> None:
    case = _make_node("C-1", "CASE_HLR", trace_to=["HLR-1", "HLR-2"])
    sibling = _make_node("C-2", "CASE_HLR", trace_to=["HLR-1"])
    graph = _make_graph([case, sibling])
    removed = await _remove_bad_traces(graph, case, ["HLR-1"])
    assert removed == 1
    graph.update_node.assert_awaited_once()
    graph.delete_node.assert_not_awaited()


@pytest.mark.asyncio
async def test_remove_bad_traces_skips_sole_coverage() -> None:
    """Guard: if CASE is the only one tracing to a req, keep the trace
    to avoid an UNTESTED→create→delete infinite cycle."""
    case = _make_node("C-1", "CASE_HLR", trace_to=["HLR-1"])
    graph = _make_graph([case])
    removed = await _remove_bad_traces(graph, case, ["HLR-1"])
    assert removed == 0
    graph.delete_node.assert_not_awaited()
    graph.update_node.assert_not_awaited()


@pytest.mark.asyncio
async def test_checker_no_cases_returns_zero() -> None:
    graph = _make_graph([])
    llm = _make_llm("COVERS")
    checker = create_case_trace_checker(llm, graph)
    result = await checker()
    assert result == 0


@pytest.mark.asyncio
async def test_checker_end_to_end() -> None:
    hlr = _make_node("HLR-0001", "HLR", "The system shall log events.")
    case = _make_node("CASE_HLR-0001", "CASE_HLR", "Vague test", trace_to=["HLR-0001"])
    sibling = _make_node("CASE_HLR-0002", "CASE_HLR", "Other test", trace_to=["HLR-0001"])
    graph = _make_graph([hlr, case, sibling])

    llm = _make_llm("HLR-0001: NO_COVERAGE - test is too vague")
    checker = create_case_trace_checker(llm, graph)
    # Only check the first case so the sibling remains the other-cover guard.
    result = await checker(only_ids={"CASE_HLR-0001"})

    assert result == 1
    graph.delete_node.assert_awaited_once_with("CASE_HLR-0001")


@pytest.mark.asyncio
async def test_checker_covers_keeps_trace() -> None:
    hlr = _make_node("HLR-0001", "HLR", "The system shall log events.")
    case = _make_node("CASE_HLR-0001", "CASE_HLR", "Verify logging", trace_to=["HLR-0001"])
    graph = _make_graph([hlr, case])

    llm = _make_llm("HLR-0001: COVERS - test exercises logging path")
    checker = create_case_trace_checker(llm, graph)
    result = await checker()

    assert result == 0
    graph.delete_node.assert_not_awaited()


# ── Empty/missing-verdict resilience (live: union_find phase 10) ─────────────


class _FlakyLLM:
    """First call returns empty text (provider flake), second a full verdict."""

    def __init__(self, good_text: str) -> None:
        self.calls = 0
        self._good = good_text

    async def ainvoke(self, _msgs: object) -> object:
        self.calls += 1
        from types import SimpleNamespace
        return SimpleNamespace(content="" if self.calls == 1 else self._good)


class _AlwaysEmptyLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def ainvoke(self, _msgs: object) -> object:
        self.calls += 1
        from types import SimpleNamespace
        return SimpleNamespace(content="")


def _case_and_graph() -> tuple[MagicMock, MagicMock]:
    case = MagicMock()
    case.node_id = "CASE_HLR-0074"
    case.node_type = "CASE_HLR"
    case.content = "verify union"
    req = MagicMock()
    req.node_id = "HLR-0080"
    req.node_type = "HLR"
    req.content = "The system shall union sets."
    graph = MagicMock()
    graph.node_sync.return_value = req
    return case, graph


@pytest.mark.asyncio
async def test_empty_verdict_response_retries_once_then_succeeds() -> None:
    """Live failure: empty provider response crashed phase 10. One retry."""
    from backend.quality.case_trace_check import _check_case_traces
    case, graph = _case_and_graph()
    llm = _FlakyLLM("HLR-0080: COVERS - direct")
    bad = await _check_case_traces(llm, graph, case, ["HLR-0080"])
    assert bad == []
    assert llm.calls == 2


@pytest.mark.asyncio
async def test_verdict_still_missing_after_retry_keeps_trace_and_warns() -> None:
    """Double failure: unverified trace is KEPT (no destructive action on
    absent evidence) and reported loudly — the phase continues."""
    from backend.quality.case_trace_check import _check_case_traces
    case, graph = _case_and_graph()
    llm = _AlwaysEmptyLLM()
    bad = await _check_case_traces(llm, graph, case, ["HLR-0080"])
    assert bad == []
    assert llm.calls == 2
