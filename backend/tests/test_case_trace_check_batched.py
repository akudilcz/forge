"""High-risk coverage for case_trace_check batched verdict parsing.

The batched coverage judge sends all of a CASE's traces to the LLM in one
call and expects exactly one verdict line per requirement id. Robustness
against malformed responses is load-bearing for pipeline correctness.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.crew.case_trace_check import _check_case_traces


def _node(
    nid: str, ntype: str, content: str = "", trace_to: list[str] | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        node_id=nid, node_type=ntype, content=content,
        trace_to=trace_to or [], parent_id="", title="", properties={},
    )


class _Graph:
    def __init__(self, nodes: list[SimpleNamespace]):
        self._by_id = {n.node_id: n for n in nodes}

    def node_sync(self, nid: str) -> SimpleNamespace | None:
        return self._by_id.get(nid)


def _llm(response_text: str) -> MagicMock:
    llm = MagicMock()
    resp = MagicMock()
    resp.content = response_text
    llm.ainvoke = AsyncMock(return_value=resp)
    return llm


@pytest.mark.asyncio
async def test_missing_requirement_is_bad_without_llm_call() -> None:
    case = _node("CASE-1", "CASE_HLR", content="steps", trace_to=["GONE"])
    graph = _Graph([case])  # "GONE" not in graph
    llm = _llm("")
    bad = await _check_case_traces(llm, graph, case, ["GONE"])
    assert bad == ["GONE"]
    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_batched_parses_multiple_verdicts() -> None:
    case = _node("CASE-1", "CASE_HLR", content="steps", trace_to=["HLR-1", "HLR-2"])
    hlr1 = _node("HLR-1", "HLR", content="A")
    hlr2 = _node("HLR-2", "HLR", content="B")
    graph = _Graph([case, hlr1, hlr2])
    llm = _llm(
        "HLR-1: COVERS - yes it does\n"
        "HLR-2: NO_COVERAGE - misaligned"
    )
    bad = await _check_case_traces(llm, graph, case, ["HLR-1", "HLR-2"])
    assert bad == ["HLR-2"]


@pytest.mark.asyncio
async def test_batched_raises_when_verdict_missing_for_requirement() -> None:
    """LLM omits a verdict line — must raise rather than silently treat as covers."""
    case = _node("CASE-1", "CASE_HLR", content="steps", trace_to=["HLR-1", "HLR-2"])
    hlr1 = _node("HLR-1", "HLR", content="A")
    hlr2 = _node("HLR-2", "HLR", content="B")
    graph = _Graph([case, hlr1, hlr2])
    llm = _llm("HLR-1: COVERS - only one line")
    with pytest.raises(RuntimeError, match="HLR-2"):
        await _check_case_traces(llm, graph, case, ["HLR-1", "HLR-2"])


@pytest.mark.asyncio
async def test_batched_ignores_unknown_ids_in_llm_output() -> None:
    """Malformed extra lines with unknown ids don't confuse the parser."""
    case = _node("CASE-1", "CASE_HLR", content="steps", trace_to=["HLR-1"])
    hlr1 = _node("HLR-1", "HLR", content="A")
    graph = _Graph([case, hlr1])
    llm = _llm(
        "UNKNOWN: COVERS - garbage\n"
        "HLR-1: COVERS - real\n"
        "noise line"
    )
    bad = await _check_case_traces(llm, graph, case, ["HLR-1"])
    assert bad == []


@pytest.mark.asyncio
async def test_batched_empty_trace_list_returns_empty() -> None:
    case = _node("CASE-1", "CASE_HLR", content="steps", trace_to=[])
    graph = _Graph([case])
    llm = _llm("")
    bad = await _check_case_traces(llm, graph, case, [])
    assert bad == []
    llm.ainvoke.assert_not_awaited()
