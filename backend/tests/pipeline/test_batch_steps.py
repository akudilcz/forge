"""Tests for batch dispatch steps."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.pipeline.batch_steps import (
    _node_to_dict,
    _run_batch_agent,
    batch_phase3,
    batch_phase5,
    batch_phase8,
    batch_phase10,
)
from backend.prompting.batch_prompts import (
    _format_node_list,
    _format_para_list,
    build_batch_phase3_prompt,
    build_batch_phase5_prompt,
    build_batch_phase7_prompt,
    build_batch_phase8_prompt,
    build_batch_phase10_prompt,
)

if TYPE_CHECKING:
    from backend.analysis.gaps import Gap

# ── Prompt formatting ───────────────────────────────────────────────────────


def test_format_node_list_empty() -> None:
    assert _format_node_list([], ["node_id"]) == "  (none)"


def test_format_node_list_basic() -> None:
    nodes = [{"node_id": "HLR-0001", "title": "Test"}]
    result = _format_node_list(nodes, ["node_id", "title"])
    assert "HLR-0001" in result
    assert "Test" in result


def test_format_node_list_sends_full_content() -> None:
    """Zero-truncation policy: full node content is always preserved."""
    nodes = [{"node_id": "X", "content": "a" * 200}]
    result = _format_node_list(nodes, ["node_id", "content"])
    assert "..." not in result
    assert "a" * 200 in result


def test_format_para_list_shows_full_content() -> None:
    paras = [{"node_id": "P-1", "content": "The algorithm proceeds as follows"}]
    result = _format_para_list(paras)
    assert "The algorithm proceeds as follows" in result
    assert "P-1" in result


def test_format_para_list_sends_full_content_no_truncation() -> None:
    """Zero-truncation policy: the full PARA text is the derivation source."""
    paras = [{"node_id": "P-1", "content": "x" * 600}]
    result = _format_para_list(paras)
    assert "..." not in result
    assert "x" * 600 in result


# ── Batch prompt builders ───────────────────────────────────────────────────


def test_phase3_prompt_includes_all_paras_and_hlrs() -> None:
    paras = [
        {"node_id": "PARA-001", "content": "Algorithm steps"},
        {"node_id": "PARA-002", "content": "NumPy arrays"},
    ]
    hlrs = [
        {"node_id": "HLR-001", "parent_id": "PARA-003", "title": "Existing",
         "content": "The system shall..."},
    ]
    prompt = build_batch_phase3_prompt(paras, hlrs)
    assert "PARA-001" in prompt
    assert "PARA-002" in prompt
    assert "HLR-001" in prompt
    assert "derive_requirement" in prompt


def test_phase5_prompt_includes_hlrs_and_modules() -> None:
    hlrs = [{"node_id": "HLR-001", "title": "Test", "content": "shall..."}]
    mods = [{"node_id": "MOD-001", "title": "Engine", "trace_to": ["HLR-002"]}]
    arch = {"node_id": "ARCH-001", "content": "Architecture doc"}
    prompt = build_batch_phase5_prompt(hlrs, mods, arch)
    assert "HLR-001" in prompt
    assert "MOD-001" in prompt


def test_phase7_prompt_includes_hlrs_and_llrs() -> None:
    hlrs = [{"node_id": "HLR-001", "title": "T", "content": "shall..."}]
    llrs = [{"node_id": "LLR-001", "parent_id": "HLR-002", "title": "L",
             "content": "s"}]
    mc = [{"node_id": "MOD-001", "node_type": "MODULE", "title": "M",
           "trace_to": []}]
    prompt = build_batch_phase7_prompt(hlrs, llrs, mc)
    assert "HLR-001" in prompt
    assert "LLR-001" in prompt


def test_phase8_prompt_includes_module_context() -> None:
    mod = {"node_id": "MOD-001", "title": "Engine", "content": "class plan"}
    con = {"node_id": "CON-001", "content": "interface spec"}
    llrs = [{"node_id": "LLR-001", "title": "T", "content": "shall..."}]
    designs = [{"node_id": "DES-001", "title": "D", "trace_to": ["LLR-002"]}]
    prompt = build_batch_phase8_prompt(mod, con, llrs, designs)
    assert "MOD-001" in prompt
    assert "LLR-001" in prompt


# ── Batch step functions ────────────────────────────────────────────────────


def _mock_node(
    node_id: str, node_type: str = "PARA", title: str = "", content: str = "",
    parent_id: str | None = None, trace_to: list[str] | None = None,
) -> MagicMock:
    n = MagicMock()
    n.node_id = node_id
    n.node_type = node_type
    n.title = title
    n.content = content
    n.parent_id = parent_id
    n.trace_to = trace_to or []
    return n


def _make_flow(
    nodes: list[MagicMock] | None = None, gaps: list[Gap] | None = None
) -> MagicMock:
    flow = MagicMock()
    flow.graph.all_nodes.return_value = nodes or []
    flow.graph.node_sync.side_effect = lambda nid: next(
        (n for n in (nodes or []) if n.node_id == nid), None,
    )
    flow._collect_phase_gaps.return_value = gaps or []
    flow._graph_state_count.return_value = 0
    flow.state.current_phase = 3
    flow.config.llm.model_for_phase.return_value = "test-model"
    flow.config.llm.context_window_for_model.return_value = 128000
    flow.config.llm.batch_author_chunk_size = 20
    flow._run_structural_loop = AsyncMock()

    agent = AsyncMock()

    async def fake_stream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        if False:  # noqa: SIM223
            yield

    agent.astream_events = fake_stream
    flow.pool.get_agent_for_gap.return_value = agent
    return flow


@pytest.mark.asyncio
async def test_batch_phase3_no_gaps_returns_early() -> None:
    flow = _make_flow(gaps=[])
    result = await batch_phase3(flow, 3)
    assert result["step_name"] == "batch_phase3"
    assert result["deletions"] == 0
    flow.pool.get_agent_for_gap.assert_not_called()


@pytest.mark.asyncio
async def test_batch_phase3_invokes_agent() -> None:
    from backend.analysis.gaps import Gap, GapPriority, GapType

    para1 = _mock_node("PARA-001", "PARA", content="Algorithm steps")
    hlr1 = _mock_node("HLR-001", "HLR", title="Existing", parent_id="PARA-003")

    gaps = [
        Gap(type=GapType.UNCOVERED_PARA, priority=GapPriority.REQUIREMENTS_HLR,
            node_id="PARA-001", description="test"),
    ]

    flow = _make_flow(nodes=[para1, hlr1], gaps=gaps)
    # After first batch call, simulate gaps resolved
    flow._collect_phase_gaps.side_effect = [gaps, []]

    result = await batch_phase3(flow, 3)
    assert result["step_name"] == "batch_phase3"
    flow.pool.get_agent_for_gap.assert_called_once_with(GapType.UNCOVERED_PARA)


@pytest.mark.asyncio
async def test_batch_phase3_retries_on_unresolved_gaps() -> None:
    from backend.analysis.gaps import Gap, GapPriority, GapType

    para1 = _mock_node("PARA-001", "PARA", content="Algorithm steps")
    hlr1 = _mock_node("HLR-001", "HLR", title="Existing")

    gap = Gap(type=GapType.UNCOVERED_PARA, priority=GapPriority.REQUIREMENTS_HLR,
              node_id="PARA-001", description="test")

    flow = _make_flow(nodes=[para1, hlr1])
    # Gap persists for 2 attempts, then resolves
    flow._collect_phase_gaps.side_effect = [[gap], [gap], []]

    await batch_phase3(flow, 3)
    # Agent called twice (attempts 1 and 2)
    assert flow.pool.get_agent_for_gap.call_count == 2


@pytest.mark.asyncio
async def test_batch_phase5_no_gaps_returns_early() -> None:
    flow = _make_flow(gaps=[])
    result = await batch_phase5(flow, 5)
    assert result["step_name"] == "batch_phase5"
    flow.pool.get_agent_for_gap.assert_not_called()


# ── build_batch_phase10_prompt ──────────────────────────────────────────────


def test_phase10_prompt_includes_untested_reqs_and_cases() -> None:
    """Prompt carries the untested HLRs/LLRs, SUITE strategy and existing CASEs."""
    hlrs = [{"node_id": "HLR-001", "title": "Load input", "content": "The system shall load"}]
    llrs = [{"node_id": "LLR-001", "parent_id": "HLR-001", "title": "Parse rows",
             "content": "The system shall parse"}]
    suite = {"node_id": "SUITE-001", "content": "risk-based strategy"}
    cases = [{"node_id": "CASE-001", "node_type": "CASE_HLR",
              "trace_to": ["HLR-002"], "title": "Existing coverage"}]

    prompt = build_batch_phase10_prompt(hlrs, llrs, suite, cases, [])

    assert "HLR-001" in prompt
    assert "LLR-001" in prompt
    assert "SUITE-001" in prompt
    assert "risk-based strategy" in prompt
    assert "CASE-001" in prompt
    assert "DO NOT duplicate" in prompt
    assert "multi_graph_write" in prompt


def test_phase10_prompt_without_suite_or_cases() -> None:
    """No SUITE and no existing CASEs: blocks are omitted, empty lists say (none)."""
    hlrs = [{"node_id": "HLR-001", "title": "T", "content": "shall"}]

    prompt = build_batch_phase10_prompt(hlrs, [], None, [], [])

    assert "HLR-001" in prompt
    assert "SUITE [" not in prompt
    assert "EXISTING CASES" not in prompt
    assert "(none)" in prompt  # empty LLR list


# ── batch_phase10 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_phase10_no_gaps_returns_early() -> None:
    flow = _make_flow(gaps=[])
    result = await batch_phase10(flow, 10)
    assert result["step_name"] == "batch_phase10"
    assert result["deletions"] == 0
    flow.pool.get_agent_for_gap.assert_not_called()
    assert flow._batch_new_node_ids == set()


@pytest.mark.asyncio
async def test_batch_phase10_invokes_agent() -> None:
    from backend.analysis.gaps import Gap, GapPriority, GapType

    hlr = _mock_node("HLR-1", "HLR", content="The system shall load")
    llr = _mock_node("LLR-1", "LLR", parent_id="HLR-1", content="The system shall parse")
    suite = _mock_node("SUITE-1", "SUITE", content="strategy")
    gap = Gap(type=GapType.UNTESTED_HLR, priority=GapPriority.TEST_HLR,
              node_id="HLR-1", description="test")

    flow = _make_flow(nodes=[hlr, llr, suite], gaps=[gap])
    flow._collect_phase_gaps.side_effect = [[gap], []]

    result = await batch_phase10(flow, 10)
    assert result["step_name"] == "batch_phase10"
    flow.pool.get_agent_for_gap.assert_called_once_with(GapType.UNTESTED_HLR)


@pytest.mark.asyncio
async def test_batch_phase10_retries_up_to_max_attempts() -> None:
    from backend.analysis.gaps import Gap, GapPriority, GapType
    from backend.pipeline.batch_steps import _MAX_BATCH_ATTEMPTS

    hlr = _mock_node("HLR-1", "HLR", content="shall")
    suite = _mock_node("SUITE-1", "SUITE", content="strategy")
    gap = Gap(type=GapType.UNTESTED_HLR, priority=GapPriority.TEST_HLR,
              node_id="HLR-1", description="test")

    flow = _make_flow(nodes=[hlr, suite])
    # Gap never resolves — retry loop must stop at the attempt cap.
    flow._collect_phase_gaps.return_value = [gap]

    with patch(
        "backend.pipeline.batch_steps._run_batch_agent", new_callable=AsyncMock, return_value=1
    ) as mock_run:
        await batch_phase10(flow, 10)
    assert mock_run.await_count == _MAX_BATCH_ATTEMPTS


@pytest.mark.asyncio
async def test_batch_phase10_passes_union_allow_gap_types() -> None:
    from backend.analysis.gaps import Gap, GapPriority, GapType

    hlr = _mock_node("HLR-1", "HLR", content="shall")
    gap = Gap(type=GapType.UNTESTED_HLR, priority=GapPriority.TEST_HLR,
              node_id="HLR-1", description="test")

    flow = _make_flow(nodes=[hlr], gaps=[gap])
    flow._collect_phase_gaps.side_effect = [[gap], []]

    with patch(
        "backend.pipeline.batch_steps._run_batch_agent", new_callable=AsyncMock, return_value=1
    ) as mock_run:
        await batch_phase10(flow, 10)

    mock_run.assert_awaited_once()
    call = mock_run.await_args
    assert call is not None
    assert call.args[1] == GapType.UNTESTED_HLR
    assert call.kwargs["allow_gap_types"] == [GapType.UNTESTED_HLR, GapType.UNTESTED_LLR]


@pytest.mark.asyncio
async def test_batch_phase10_tracks_new_case_node_ids() -> None:
    from backend.analysis.gaps import Gap, GapPriority, GapType

    hlr = _mock_node("HLR-1", "HLR", content="shall")
    suite = _mock_node("SUITE-1", "SUITE", content="strategy")
    nodes = [hlr, suite]
    gap = Gap(type=GapType.UNTESTED_HLR, priority=GapPriority.TEST_HLR,
              node_id="HLR-1", description="test")

    flow = _make_flow(nodes=nodes, gaps=[gap])
    flow.graph.all_nodes.side_effect = lambda: list(nodes)  # live view of the graph
    flow._collect_phase_gaps.side_effect = [[gap], []]

    async def fake_agent(*args: Any, **kwargs: Any) -> int:
        nodes.append(_mock_node("CASE-NEW", "CASE_HLR", trace_to=["HLR-1"]))
        return 1

    with patch(
        "backend.pipeline.batch_steps._run_batch_agent",
        new_callable=AsyncMock, side_effect=fake_agent,
    ):
        await batch_phase10(flow, 10)

    assert flow._batch_new_node_ids == {"CASE-NEW"}


@pytest.mark.asyncio
async def test_batch_phase10_exception_falls_back_to_structural() -> None:
    from backend.analysis.gaps import Gap, GapPriority, GapType

    hlr = _mock_node("HLR-1", "HLR", content="shall")
    gap = Gap(type=GapType.UNTESTED_HLR, priority=GapPriority.TEST_HLR,
              node_id="HLR-1", description="test")
    flow = _make_flow(nodes=[hlr], gaps=[gap])

    with (
        patch(
            "backend.pipeline.batch_steps._run_batch_agent",
            new_callable=AsyncMock, side_effect=RuntimeError("boom"),
        ),
        patch(
            "backend.pipeline.batch_steps._fallback_structural",
            new_callable=AsyncMock,
            return_value={"step_name": "structural", "deletions": 0},
        ) as mock_fb,
    ):
        result = await batch_phase10(flow, 10)
    mock_fb.assert_awaited_once_with(flow, 10)
    assert result["step_name"] == "structural"


@pytest.mark.asyncio
async def test_batch_phase10_skips_agent_when_all_requirements_tested() -> None:
    """Gaps reported but every HLR/LLR already has a CASE: no agent dispatch."""
    from backend.analysis.gaps import Gap, GapPriority, GapType

    hlr = _mock_node("HLR-1", "HLR", content="shall")
    llr = _mock_node("LLR-1", "LLR", parent_id="HLR-1", content="shall")
    case_hlr = _mock_node("CASE-1", "CASE_HLR", trace_to=["HLR-1"])
    case_llr = _mock_node("CASE-2", "CASE_LLR", trace_to=["LLR-1"])
    gap = Gap(type=GapType.UNTESTED_HLR, priority=GapPriority.TEST_HLR,
              node_id="HLR-1", description="stale gap")

    flow = _make_flow(nodes=[hlr, llr, case_hlr, case_llr], gaps=[gap])
    flow._collect_phase_gaps.return_value = [gap]

    with patch(
        "backend.pipeline.batch_steps._run_batch_agent", new_callable=AsyncMock
    ) as mock_run:
        result = await batch_phase10(flow, 10)
    mock_run.assert_not_awaited()
    assert result["step_name"] == "batch_phase10"


# ── batch_phase5 happy path ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_phase5_invokes_agent() -> None:
    from backend.analysis.gaps import Gap, GapPriority, GapType

    hlr = _mock_node("HLR-1", "HLR", title="T", content="The system shall...")
    mod = _mock_node("MOD-1", "MODULE", title="Engine", content="class plan")
    arch = _mock_node("ARCH-1", "ARCHITECTURE", content="architecture doc")
    con = _mock_node("CON-1", "CONTRACT", content="interface spec")

    gap = Gap(type=GapType.UNMODULARISED, priority=GapPriority.MODULARISATION,
              node_id="HLR-1", description="test")

    flow = _make_flow(nodes=[hlr, mod, arch, con], gaps=[gap])
    flow._collect_phase_gaps.side_effect = [[gap], []]

    result = await batch_phase5(flow, 5)
    assert result["step_name"] == "batch_phase5"
    flow.pool.get_agent_for_gap.assert_called_once_with(GapType.UNMODULARISED)


# ── batch_phase8 exception fallback ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch_phase8_exception_falls_back_to_structural() -> None:
    from backend.analysis.gaps import Gap, GapPriority, GapType

    llr = _mock_node("LLR-1", "LLR", parent_id="HLR-1", content="spec")
    mod = _mock_node("MOD-1", "MODULE", content="module plan")
    gap = Gap(type=GapType.UNDESIGNED, priority=GapPriority.DESIGN,
              node_id="LLR-1", description="test")

    flow = _make_flow(nodes=[llr, mod], gaps=[gap])
    flow.graph.nodes_tracing_to = MagicMock(return_value=["MOD-1"])
    flow.graph.children_sync.return_value = []
    flow._collect_phase_gaps.return_value = [gap]

    with (
        patch(
            "backend.pipeline.batch_steps._run_fast_traces",
            new_callable=AsyncMock, return_value=0,
        ),
        patch(
            "backend.pipeline.batch_steps._run_batch_agent",
            new_callable=AsyncMock, side_effect=RuntimeError("boom"),
        ),
        patch(
            "backend.pipeline.batch_steps._fallback_structural",
            new_callable=AsyncMock,
            return_value={"step_name": "structural", "deletions": 0},
        ) as mock_fb,
    ):
        result = await batch_phase8(flow, 8)
    mock_fb.assert_awaited_once_with(flow, 8)
    assert result["step_name"] == "structural"


# ── _run_batch_agent ────────────────────────────────────────────────────────


class _PlainMessage:
    """Message stub without a ``message`` attribute (unlike MagicMock)."""

    def __init__(self, tool_calls: list[Any]) -> None:
        self.tool_calls = tool_calls


class _ObjectToolCall:
    """Object-shaped tool call (attribute access, not dict)."""

    def __init__(self, name: str, args: dict[str, Any]) -> None:
        self.name = name
        self.args = args


def _flow_with_streaming_agent(events: list[dict[str, Any]]) -> MagicMock:
    """Flow whose agent streams the given events from astream_events."""
    flow = _make_flow()

    async def stream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        for event in events:
            yield event

    agent = MagicMock()
    agent.astream_events = stream
    flow.pool.get_agent_for_gap.return_value = agent
    return flow


@pytest.mark.asyncio
async def test_run_batch_agent_no_agent_returns_zero() -> None:
    from backend.analysis.gaps import GapType

    flow = _make_flow()
    flow.pool.get_agent_for_gap.return_value = None

    result = await _run_batch_agent(flow, GapType.UNCOVERED_PARA, "prompt", 3)
    assert result == 0


@pytest.mark.asyncio
async def test_run_batch_agent_counts_dict_and_object_tool_calls() -> None:
    from backend.analysis.gaps import GapType

    msg = _PlainMessage(tool_calls=[
        {"name": "graph_add_node", "args": {"node_type": "HLR"}},
        _ObjectToolCall("graph_add_traces", {"node_id": "MOD-1"}),
    ])
    events: list[dict[str, Any]] = [
        {"event": "on_chat_model_stream"},                    # ignored
        {"event": "on_chat_model_end", "data": {"output": msg}},
        {"event": "on_chat_model_end", "data": {}},           # no output: skipped
    ]
    flow = _flow_with_streaming_agent(events)

    result = await _run_batch_agent(flow, GapType.UNCOVERED_PARA, "prompt", 3)
    assert result == 2


@pytest.mark.asyncio
async def test_run_batch_agent_resets_constraints_when_stream_raises() -> None:
    from backend.analysis.gaps import GapType

    flow = _make_flow()

    async def broken_stream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        raise RuntimeError("stream died")
        yield  # pragma: no cover — makes this an async generator

    agent = MagicMock()
    agent.astream_events = broken_stream
    flow.pool.get_agent_for_gap.return_value = agent

    token = object()
    with (
        patch("backend.pipeline.batch_steps.set_phase_constraints", return_value=token),
        patch("backend.pipeline.batch_steps.reset_phase_constraints") as mock_reset,
    ):
        with pytest.raises(RuntimeError, match="stream died"):
            await _run_batch_agent(flow, GapType.UNCOVERED_PARA, "prompt", 3)
    mock_reset.assert_called_once_with(token)


@pytest.mark.asyncio
async def test_run_batch_agent_uses_union_constraints_for_allow_gap_types() -> None:
    from backend.analysis.gaps import GapType

    flow = _flow_with_streaming_agent([])
    token = object()
    with (
        patch(
            "backend.pipeline.batch_steps.set_phase_constraints_union", return_value=token
        ) as mock_union,
        patch("backend.pipeline.batch_steps.set_phase_constraints") as mock_single,
        patch("backend.pipeline.batch_steps.reset_phase_constraints") as mock_reset,
    ):
        result = await _run_batch_agent(
            flow,
            GapType.UNTESTED_HLR,
            "prompt",
            10,
            allow_gap_types=[GapType.UNTESTED_HLR, GapType.UNTESTED_LLR],
        )
    assert result == 0
    mock_union.assert_called_once_with([GapType.UNTESTED_HLR, GapType.UNTESTED_LLR])
    mock_single.assert_not_called()
    mock_reset.assert_called_once_with(token)


# ── Chunked batch authoring (specs/13 §Batch prompts) ───────────
#
# Live defect: one whole-batch phase-3 call had to author HLRs for 46+ PARAs
# in a single response; the response hit the provider output-token limit, the
# last PARAs never got HLRs, and after _MAX_BATCH_ATTEMPTS the phase ended
# awaiting_approval with UNCOVERED_PARA gaps and zero per-gap dispatches
# (trace.1614841.jsonl: PARA-0183 / PARA-0185).


def _para_gap(node_id: str) -> Gap:
    from backend.analysis.gaps import Gap, GapPriority, GapType

    return Gap(type=GapType.UNCOVERED_PARA, priority=GapPriority.REQUIREMENTS_HLR,
               node_id=node_id, description="uncovered")


def _chunking_flow(n_paras: int, chunk_size: int) -> tuple[MagicMock, set[str]]:
    """Flow with ``n_paras`` uncovered PARAs whose gaps track ``pending``."""
    paras = [
        _mock_node(f"PARA-{i:04d}", "PARA", content=f"paragraph text {i}")
        for i in range(n_paras)
    ]
    hlr = _mock_node("HLR-0001", "HLR", title="Existing",
                     content="The system shall exist.", parent_id="PARA-9999")
    flow = _make_flow(nodes=[*paras, hlr])
    flow.config.llm.batch_author_chunk_size = chunk_size
    pending = {p.node_id for p in paras}

    def collect(phase: int, skipped: set[str]) -> list[Gap]:
        return [_para_gap(pid) for pid in sorted(pending)]

    flow._collect_phase_gaps.side_effect = collect
    return flow, pending


def _dynamic_para_ids(prompt: str, pending: set[str] | None = None) -> set[str]:
    """PARA ids listed in the prompt's dynamic (chunk) section."""
    dynamic = prompt.split("UNCOVERED PARAGRAPHS")[1]
    universe = pending if pending is not None else {
        part.split("]")[0] for part in dynamic.split("[")[1:] if part.startswith("PARA-")
    }
    return {pid for pid in universe if f"[{pid}]" in dynamic}


@pytest.mark.asyncio
async def test_batch_phase3_chunks_large_para_set_with_static_prefix() -> None:
    """45 PARAs, chunk size 20 → 3 calls of ≤20 PARAs, identical static prefix."""
    flow, pending = _chunking_flow(45, 20)
    prompts: list[str] = []

    async def fake_agent(*args: Any, **kwargs: Any) -> int:
        prompt = args[2]
        prompts.append(prompt)
        pending.difference_update(_dynamic_para_ids(prompt, pending))
        return 1

    with patch(
        "backend.pipeline.batch_steps._run_batch_agent",
        new_callable=AsyncMock, side_effect=fake_agent,
    ):
        result = await batch_phase3(flow, 3)

    assert result["step_name"] == "batch_phase3"
    assert len(prompts) == 3
    sizes = [len(_dynamic_para_ids(p)) for p in prompts]
    assert sizes == [20, 20, 5]
    static_prefixes = {p.split("UNCOVERED PARAGRAPHS")[0] for p in prompts}
    assert len(static_prefixes) == 1  # byte-identical static prefix per specs/13
    flow._run_structural_loop.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_phase3_chunk_failure_retries_only_that_chunk() -> None:
    """A stuck chunk retries with only its own items; resolved chunks don't repeat."""
    from backend.pipeline.batch_steps import _MAX_BATCH_ATTEMPTS

    flow, pending = _chunking_flow(25, 20)
    chunk1 = {f"PARA-{i:04d}" for i in range(20)}
    chunk2 = {f"PARA-{i:04d}" for i in range(20, 25)}
    calls: list[set[str]] = []

    async def fake_agent(*args: Any, **kwargs: Any) -> int:
        ids = _dynamic_para_ids(args[2], pending)
        calls.append(ids)
        # Only chunk-1 items ever resolve; chunk 2 stays stuck (truncation).
        pending.difference_update(ids & chunk1)
        return 1

    with (
        patch(
            "backend.pipeline.batch_steps._run_batch_agent",
            new_callable=AsyncMock, side_effect=fake_agent,
        ),
        patch(
            "backend.pipeline.batch_steps._fallback_structural",
            new_callable=AsyncMock,
            return_value={"step_name": "structural", "deletions": 0},
        ) as mock_fb,
    ):
        await batch_phase3(flow, 3)

    assert len(calls) == 1 + _MAX_BATCH_ATTEMPTS
    assert calls[0] == chunk1
    for retry in calls[1:]:
        assert retry == chunk2  # retries carry only the stuck chunk's items
    mock_fb.assert_awaited_once_with(flow, 3)


@pytest.mark.asyncio
async def test_batch_phase3_stragglers_dispatch_per_gap() -> None:
    """Attempts exhausted with unresolved gaps → per-gap structural dispatch."""
    from backend.pipeline.batch_steps import _MAX_BATCH_ATTEMPTS

    flow, _pending = _chunking_flow(5, 20)

    with patch(
        "backend.pipeline.batch_steps._run_batch_agent",
        new_callable=AsyncMock, return_value=1,
    ) as mock_run:
        result = await batch_phase3(flow, 3)

    assert mock_run.await_count == _MAX_BATCH_ATTEMPTS
    # The per-gap structural dispatch path actually runs for the stragglers.
    flow._run_structural_loop.assert_awaited_once_with(3, skip_approval=True)
    assert result["step_name"] == "structural"


@pytest.mark.asyncio
async def test_batch_phase3_small_set_single_call_unchanged() -> None:
    """A set below the chunk size is one call, no fallback — behaviour unchanged."""
    flow, pending = _chunking_flow(3, 20)

    async def fake_agent(*args: Any, **kwargs: Any) -> int:
        pending.clear()
        return 1

    with patch(
        "backend.pipeline.batch_steps._run_batch_agent",
        new_callable=AsyncMock, side_effect=fake_agent,
    ) as mock_run:
        result = await batch_phase3(flow, 3)

    assert mock_run.await_count == 1
    assert result["step_name"] == "batch_phase3"
    flow._run_structural_loop.assert_not_awaited()


@pytest.mark.asyncio
async def test_batch_phase5_chunks_unassigned_hlrs() -> None:
    """Phase 5 chunks its unassigned-HLR list; 25 HLRs, chunk 10 → 3 calls."""
    from backend.analysis.gaps import Gap, GapPriority, GapType

    hlrs = [
        _mock_node(f"HLR-{i:04d}", "HLR", title=f"T{i}", content=f"The system shall {i}.")
        for i in range(25)
    ]
    mod = _mock_node("MOD-1", "MODULE", title="Engine", content="class plan")
    flow = _make_flow(nodes=[*hlrs, mod])
    flow.config.llm.batch_author_chunk_size = 10
    pending = {h.node_id for h in hlrs}

    def collect(phase: int, skipped: set[str]) -> list[Gap]:
        return [
            Gap(type=GapType.UNMODULARISED, priority=GapPriority.MODULARISATION,
                node_id=hid, description="unassigned")
            for hid in sorted(pending)
        ]

    flow._collect_phase_gaps.side_effect = collect
    batch_sizes: list[int] = []

    def fake_build(unassigned: list[dict[str, Any]], *args: Any, **kwargs: Any) -> str:
        batch_sizes.append(len(unassigned))
        fake_build.last_ids = [h["node_id"] for h in unassigned]  # type: ignore[attr-defined]
        return "PROMPT"

    async def fake_agent(*args: Any, **kwargs: Any) -> int:
        pending.difference_update(fake_build.last_ids)  # type: ignore[attr-defined]
        return 1

    with (
        patch("backend.pipeline.batch_steps.build_batch_phase5_prompt", side_effect=fake_build),
        patch(
            "backend.pipeline.batch_steps._run_batch_agent",
            new_callable=AsyncMock, side_effect=fake_agent,
        ),
    ):
        result = await batch_phase5(flow, 5)

    assert result["step_name"] == "batch_phase5"
    assert batch_sizes == [10, 10, 5]


@pytest.mark.asyncio
async def test_batch_phase7_chunks_unrefined_hlrs() -> None:
    """Phase 7 chunks its unrefined-HLR list; 12 HLRs, chunk 5 → 3 calls."""
    from backend.analysis.gaps import Gap, GapPriority, GapType
    from backend.pipeline.batch_steps import batch_phase7

    hlrs = [
        _mock_node(f"HLR-{i:04d}", "HLR", title=f"T{i}", content=f"The system shall {i}.")
        for i in range(12)
    ]
    flow = _make_flow(nodes=list(hlrs))
    flow.config.llm.batch_author_chunk_size = 5
    pending = {h.node_id for h in hlrs}

    def collect(phase: int, skipped: set[str]) -> list[Gap]:
        return [
            Gap(type=GapType.UNREFINED_HLR, priority=GapPriority.REQUIREMENTS_LLR,
                node_id=hid, description="unrefined")
            for hid in sorted(pending)
        ]

    flow._collect_phase_gaps.side_effect = collect
    batch_sizes: list[int] = []

    def fake_build(unrefined: list[dict[str, Any]], *args: Any, **kwargs: Any) -> str:
        batch_sizes.append(len(unrefined))
        fake_build.last_ids = [h["node_id"] for h in unrefined]  # type: ignore[attr-defined]
        return "PROMPT"

    async def fake_agent(*args: Any, **kwargs: Any) -> int:
        pending.difference_update(fake_build.last_ids)  # type: ignore[attr-defined]
        return 1

    with (
        patch("backend.pipeline.batch_steps.build_batch_phase7_prompt", side_effect=fake_build),
        patch(
            "backend.pipeline.batch_steps._run_batch_agent",
            new_callable=AsyncMock, side_effect=fake_agent,
        ),
    ):
        result = await batch_phase7(flow, 7)

    assert result["step_name"] == "batch_phase7"
    assert batch_sizes == [5, 5, 2]


@pytest.mark.asyncio
async def test_batch_phase10_chunks_untested_requirements() -> None:
    """Phase 10 chunks untested HLRs/LLRs; 25 untested, chunk 10 → 3 calls."""
    from backend.analysis.gaps import Gap, GapPriority, GapType

    hlrs = [_mock_node(f"HLR-{i:04d}", "HLR", content=f"shall {i}") for i in range(25)]
    suite = _mock_node("SUITE-1", "SUITE", content="strategy")
    nodes: list[MagicMock] = [*hlrs, suite]
    flow = _make_flow(nodes=nodes)
    flow.graph.all_nodes.side_effect = lambda: list(nodes)
    flow.config.llm.batch_author_chunk_size = 10
    gap = Gap(type=GapType.UNTESTED_HLR, priority=GapPriority.TEST_HLR,
              node_id="HLR-0000", description="untested")
    flow._collect_phase_gaps.return_value = [gap]

    batch_sizes: list[int] = []

    def fake_build(
        untested_hlrs: list[dict[str, Any]], untested_llrs: list[dict[str, Any]],
        *args: Any, **kwargs: Any,
    ) -> str:
        batch_sizes.append(len(untested_hlrs) + len(untested_llrs))
        fake_build.last_ids = [h["node_id"] for h in untested_hlrs]  # type: ignore[attr-defined]
        return "PROMPT"

    async def fake_agent(*args: Any, **kwargs: Any) -> int:
        for hid in fake_build.last_ids:  # type: ignore[attr-defined]
            nodes.append(_mock_node(f"CASE-{hid}", "CASE_HLR", trace_to=[hid]))
        return 1

    with (
        patch("backend.pipeline.batch_steps.build_batch_phase10_prompt", side_effect=fake_build),
        patch(
            "backend.pipeline.batch_steps._run_batch_agent",
            new_callable=AsyncMock, side_effect=fake_agent,
        ),
    ):
        result = await batch_phase10(flow, 10)

    assert result["step_name"] == "batch_phase10"
    assert batch_sizes == [10, 10, 5]
    assert len(flow._batch_new_node_ids) == 25


@pytest.mark.asyncio
async def test_batch_phase8_falls_back_when_attempts_exhaust_with_gaps() -> None:
    """Phase 8: gaps remaining after all batch attempts → per-gap dispatch."""
    from backend.analysis.gaps import Gap, GapPriority, GapType
    from backend.pipeline.batch_steps import _MAX_BATCH_ATTEMPTS, batch_phase8

    llr = _mock_node("LLR-1", "LLR", parent_id="HLR-1", content="spec")
    mod = _mock_node("MOD-1", "MODULE", content="module plan")
    gap = Gap(type=GapType.UNDESIGNED, priority=GapPriority.DESIGN,
              node_id="LLR-1", description="undesigned")
    flow = _make_flow(nodes=[llr, mod])
    flow.graph.nodes_tracing_to = MagicMock(return_value=["MOD-1"])
    flow.graph.children_sync.return_value = []
    flow._collect_phase_gaps.return_value = [gap]  # never resolves

    with (
        patch(
            "backend.pipeline.batch_steps._run_fast_traces",
            new_callable=AsyncMock, return_value=0,
        ),
        patch(
            "backend.pipeline.batch_steps._run_batch_agent",
            new_callable=AsyncMock, return_value=1,  # non-zero: no zero-call path
        ) as mock_run,
        patch(
            "backend.pipeline.batch_steps._fallback_structural",
            new_callable=AsyncMock,
            return_value={"step_name": "structural", "deletions": 0},
        ) as mock_fb,
    ):
        result = await batch_phase8(flow, 8)

    assert mock_run.await_count == _MAX_BATCH_ATTEMPTS
    mock_fb.assert_awaited_once_with(flow, 8)
    assert result["step_name"] == "structural"


def test_llm_config_batch_author_chunk_size_default() -> None:
    """LLMConfig exposes the batch authoring chunk size (specs/13)."""
    from backend.config.models import LLMConfig

    assert LLMConfig().batch_author_chunk_size == 20


# ── _node_to_dict ───────────────────────────────────────────────────────────


def test_node_to_dict_extracts_fields() -> None:
    node = _mock_node("HLR-001", "HLR", title="Test", content="body",
                       parent_id="PARA-001", trace_to=["LLR-001"])
    d = _node_to_dict(node)
    assert d["node_id"] == "HLR-001"
    assert d["node_type"] == "HLR"
    assert d["title"] == "Test"
    assert d["trace_to"] == ["LLR-001"]


# ── U6: cover-or-classify prompt + marking-based resolution accounting ───────


def test_phase3_prompt_is_cover_or_classify() -> None:
    paras = [{"node_id": "PARA-001", "content": "Some background story."}]
    prompt = build_batch_phase3_prompt(paras, [], [])
    assert "COVER OR CLASSIFY" in prompt
    assert "graph_update_node" in prompt
    assert '"non_normative": true' in prompt
    assert "non_normative_rationale" in prompt
    for kind in (
        "background/context", "duplicate-of-<PARA-id>",
        "example/illustration", "meta/document-structure",
    ):
        assert kind in prompt, kind
    # Anti-duplication warning names the defect class.
    assert "defect" in prompt.lower()
    assert "near-duplicate" in prompt


@pytest.mark.asyncio
async def test_batch_phase3_marking_resolves_item_without_new_hlr() -> None:
    """An agent that classifies the PARA non_normative (no HLR created)
    resolves the coverage item: the analyser-backed collector no longer
    reports the gap, so no straggler fallback dispatch runs."""
    from backend.analysis.gaps import Gap, GapPriority, GapType

    para1 = _mock_node("PARA-001", "PARA", content="Background story.")
    gap = Gap(type=GapType.UNCOVERED_PARA, priority=GapPriority.REQUIREMENTS_HLR,
              node_id="PARA-001", description="test")

    flow = _make_flow(nodes=[para1], gaps=[gap])
    # First collection sees the gap; after the batch agent marks the PARA
    # non_normative the analyser emits nothing — certificate follows.
    flow._collect_phase_gaps.side_effect = [[gap], [gap], []]

    result = await batch_phase3(flow, 3)

    assert result["step_name"] == "batch_phase3"
    assert result["deletions"] == 0
    flow._run_structural_loop.assert_not_called()
