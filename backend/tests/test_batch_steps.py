"""Tests for batch dispatch steps."""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.crew.batch_prompts import (
    _format_node_list,
    _format_para_list,
    build_batch_phase3_prompt,
    build_batch_phase5_prompt,
    build_batch_phase7_prompt,
    build_batch_phase8_prompt,
)
from backend.crew.batch_steps import _node_to_dict, batch_phase3, batch_phase5

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


# ── _node_to_dict ───────────────────────────────────────────────────────────


def test_node_to_dict_extracts_fields() -> None:
    node = _mock_node("HLR-001", "HLR", title="Test", content="body",
                       parent_id="PARA-001", trace_to=["LLR-001"])
    d = _node_to_dict(node)
    assert d["node_id"] == "HLR-001"
    assert d["node_type"] == "HLR"
    assert d["title"] == "Test"
    assert d["trace_to"] == ["LLR-001"]
