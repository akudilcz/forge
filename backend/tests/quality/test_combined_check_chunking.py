"""Chunked judging for the combined quality check (design/01 §7.4).

Live defect (priority_queue wave-2, phase 3): 81 HLRs judged in ONE call
truncated after ~19 verdicts at the provider output-token limit; the retry
re-sent all 62 unjudged nodes in one call and truncated identically →
UnjudgedQualityError halted the phase. Loud was right, economics were wrong:
`run_combined_quality_check` must split candidates into chunks of
`LLMConfig.quality_judge_batch_size` and judge each chunk independently.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.quality.checks import run_combined_quality_check
from backend.quality.combined_check import quality_pass_key


def _node(nid: str, title: str, content: str) -> SimpleNamespace:
    return SimpleNamespace(
        node_id=nid, node_type="HLR", parent_id="",
        title=title, content=content, trace_to=[], properties={},
    )


def _hlr_nodes(count: int) -> list[SimpleNamespace]:
    return [
        _node(f"HLR-{i:04d}", f"Title {i}", f"The system shall do thing {i}.")
        for i in range(1, count + 1)
    ]


def _flow(nodes: list[SimpleNamespace], batch_size: int) -> MagicMock:
    flow = MagicMock()
    flow.graph = MagicMock()
    flow.graph.all_nodes = MagicMock(return_value=nodes)
    flow._batch_new_node_ids = None
    flow.config = MagicMock()
    flow.config.llm.quality_judge_batch_size = batch_size
    flow._quality_verdict_cache = {}
    return flow


def _patched_checker(checker: AsyncMock) -> Any:
    return patch(
        "backend.quality.combined_check.create_combined_quality_checker",
        return_value=checker,
    )


def _gap(nid: str) -> Gap:
    return Gap(
        type=GapType.NON_ATOMIC_REQUIREMENT,
        priority=GapPriority.MAINTENANCE,
        node_id=nid,
        description=f"{nid} not atomic",
    )


# ── Chunk splitting at the checks.py level (checker mocked) ─────────────────


@pytest.mark.asyncio
async def test_large_set_is_judged_in_chunks_no_larger_than_batch_size() -> None:
    """81 nodes with batch size 25 → 4 checker calls of sizes 25/25/25/6."""
    flow = _flow(_hlr_nodes(81), batch_size=25)
    checker = AsyncMock(return_value=[])
    with patch("backend.agents.factory.build_llm", return_value=MagicMock()), \
            _patched_checker(checker):
        gaps = await run_combined_quality_check(flow, phase=3)

    assert gaps == []
    sizes = [len(c.args[0]) for c in checker.await_args_list]
    assert sizes == [25, 25, 25, 6]
    sent_ids = [it[0] for c in checker.await_args_list for it in c.args[0]]
    assert sent_ids == [n.node_id for n in _hlr_nodes(81)]  # order preserved


@pytest.mark.asyncio
async def test_gaps_accumulate_across_chunks() -> None:
    """Failures found in different chunks all appear in the returned list."""
    flow = _flow(_hlr_nodes(4), batch_size=2)
    checker = AsyncMock(side_effect=[[_gap("HLR-0002")], [_gap("HLR-0003")]])
    with patch("backend.agents.factory.build_llm", return_value=MagicMock()), \
            _patched_checker(checker):
        gaps = await run_combined_quality_check(flow, phase=3)

    assert checker.await_count == 2
    assert sorted(g.node_id for g in gaps) == ["HLR-0002", "HLR-0003"]


@pytest.mark.asyncio
async def test_cached_pass_nodes_are_excluded_before_chunking() -> None:
    """4 nodes, 2 with sticky PASS, batch size 3 → one call with the 2 others."""
    nodes = _hlr_nodes(4)
    flow = _flow(nodes, batch_size=3)
    for n in nodes[:2]:
        flow._quality_verdict_cache[
            quality_pass_key(n.node_id, n.title, n.content)
        ] = "PASS"
    checker = AsyncMock(return_value=[])
    with patch("backend.agents.factory.build_llm", return_value=MagicMock()), \
            _patched_checker(checker):
        await run_combined_quality_check(flow, phase=3)

    assert checker.await_count == 1
    sent = [it[0] for it in checker.await_args_list[0].args[0]]
    assert sent == ["HLR-0003", "HLR-0004"]


@pytest.mark.asyncio
async def test_earlier_chunk_passes_are_cached_when_later_chunk_raises() -> None:
    """A later chunk's failure never discards a fully judged earlier chunk."""
    from backend.quality.combined_check import UnjudgedQualityError

    flow = _flow(_hlr_nodes(4), batch_size=2)
    checker = AsyncMock(
        side_effect=[[], UnjudgedQualityError({"HLR-0003": {"EARS"}})]
    )
    with patch("backend.agents.factory.build_llm", return_value=MagicMock()), \
            _patched_checker(checker):
        with pytest.raises(UnjudgedQualityError):
            await run_combined_quality_check(flow, phase=3)

    cached_ids = {key[0] for key in flow._quality_verdict_cache}
    assert cached_ids == {"HLR-0001", "HLR-0002"}


# ── Single-call contract and per-chunk retry (real checker, fake LLM) ───────


def _resp(text: str) -> SimpleNamespace:
    return SimpleNamespace(content=text)


def _fake_llm(*texts: str) -> AsyncMock:
    llm = AsyncMock()
    llm.ainvoke = AsyncMock(side_effect=[_resp(t) for t in texts])
    return llm


def _all_pass(nids: list[str]) -> str:
    return "\n".join(
        f"{nid}: ATOMIC=PASS EARS=PASS MATCH=PASS SPECIFIC=PASS" for nid in nids
    )


@pytest.mark.asyncio
async def test_small_set_is_a_single_llm_call() -> None:
    """<= batch size keeps today's one-call contract — pinned."""
    flow = _flow(_hlr_nodes(3), batch_size=25)
    llm = _fake_llm(_all_pass(["HLR-0001", "HLR-0002", "HLR-0003"]))
    with patch("backend.agents.factory.build_llm", return_value=llm):
        gaps = await run_combined_quality_check(flow, phase=3)

    assert gaps == []
    assert llm.ainvoke.await_count == 1


@pytest.mark.asyncio
async def test_truncated_chunk_retries_only_that_chunks_unjudged_nodes() -> None:
    """5 nodes, batch 3: chunk 1 truncates after one verdict → the retry
    re-asks only chunk 1's two unjudged nodes, never chunk 2's."""
    flow = _flow(_hlr_nodes(5), batch_size=3)
    llm = _fake_llm(
        _all_pass(["HLR-0001"]),                # chunk 1 truncated
        _all_pass(["HLR-0002", "HLR-0003"]),    # chunk 1 retry — unjudged only
        _all_pass(["HLR-0004", "HLR-0005"]),    # chunk 2
    )
    with patch("backend.agents.factory.build_llm", return_value=llm):
        gaps = await run_combined_quality_check(flow, phase=3)

    assert gaps == []
    assert llm.ainvoke.await_count == 3
    retry_payload = llm.ainvoke.await_args_list[1].args[0][1].content
    assert "HLR-0002" in retry_payload
    assert "HLR-0003" in retry_payload
    assert "HLR-0001" not in retry_payload
    assert "HLR-0004" not in retry_payload
    assert "HLR-0005" not in retry_payload
