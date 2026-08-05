"""Tests for batch dispatch steps."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.crew.batch_prompts import (
    _format_node_list,
    _format_para_list,
    build_batch_phase3_prompt,
    build_batch_phase5_prompt,
    build_batch_phase7_prompt,
    build_batch_phase8_prompt,
    build_batch_phase10_prompt,
)
from backend.crew.batch_steps import (
    _node_to_dict,
    _run_batch_agent,
    batch_phase3,
    batch_phase5,
    batch_phase8,
    batch_phase10,
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

    prompt = build_batch_phase10_prompt(hlrs, llrs, suite, cases)

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

    prompt = build_batch_phase10_prompt(hlrs, [], None, [])

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
    from backend.crew.batch_steps import _MAX_BATCH_ATTEMPTS

    hlr = _mock_node("HLR-1", "HLR", content="shall")
    suite = _mock_node("SUITE-1", "SUITE", content="strategy")
    gap = Gap(type=GapType.UNTESTED_HLR, priority=GapPriority.TEST_HLR,
              node_id="HLR-1", description="test")

    flow = _make_flow(nodes=[hlr, suite])
    # Gap never resolves — retry loop must stop at the attempt cap.
    flow._collect_phase_gaps.return_value = [gap]

    with patch(
        "backend.crew.batch_steps._run_batch_agent", new_callable=AsyncMock, return_value=1
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
        "backend.crew.batch_steps._run_batch_agent", new_callable=AsyncMock, return_value=1
    ) as mock_run:
        await batch_phase10(flow, 10)

    mock_run.assert_awaited_once()
    call = mock_run.await_args
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
        "backend.crew.batch_steps._run_batch_agent",
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
            "backend.crew.batch_steps._run_batch_agent",
            new_callable=AsyncMock, side_effect=RuntimeError("boom"),
        ),
        patch(
            "backend.crew.batch_steps._fallback_structural",
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
        "backend.crew.batch_steps._run_batch_agent", new_callable=AsyncMock
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
            "backend.crew.batch_steps._run_fast_traces",
            new_callable=AsyncMock, return_value=0,
        ),
        patch(
            "backend.crew.batch_steps._run_batch_agent",
            new_callable=AsyncMock, side_effect=RuntimeError("boom"),
        ),
        patch(
            "backend.crew.batch_steps._fallback_structural",
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
    events = [
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
        patch("backend.crew.batch_steps.set_phase_constraints", return_value=token),
        patch("backend.crew.batch_steps.reset_phase_constraints") as mock_reset,
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
            "backend.crew.batch_steps.set_phase_constraints_union", return_value=token
        ) as mock_union,
        patch("backend.crew.batch_steps.set_phase_constraints") as mock_single,
        patch("backend.crew.batch_steps.reset_phase_constraints") as mock_reset,
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


# ── _node_to_dict ───────────────────────────────────────────────────────────


def test_node_to_dict_extracts_fields() -> None:
    node = _mock_node("HLR-001", "HLR", title="Test", content="body",
                       parent_id="PARA-001", trace_to=["LLR-001"])
    d = _node_to_dict(node)
    assert d["node_id"] == "HLR-001"
    assert d["node_type"] == "HLR"
    assert d["title"] == "Test"
    assert d["trace_to"] == ["LLR-001"]
