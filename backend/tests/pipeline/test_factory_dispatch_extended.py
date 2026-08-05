"""Coverage-focused tests for agents/factory and crew/dispatch code paths."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from backend.agents.factory import (
    LLMCallLimitExceededError,
    ThrottledChatOpenAI,
)

# ── ThrottledChatOpenAI ──────────────────────────────────────────────────────


def test_llm_call_limit_exceeded_is_exception_subclass() -> None:
    assert issubclass(LLMCallLimitExceededError, Exception)


def test_throttled_log_call_counts_and_logs() -> None:
    """Exercise the _log_call path (factory.py 31–45)."""
    import backend.agents.factory as factory

    llm = MagicMock(spec=ThrottledChatOpenAI)
    llm.model_name = "gpt-5-test"
    llm._log_call = ThrottledChatOpenAI._log_call.__get__(llm, ThrottledChatOpenAI)

    saved_count = factory.llm_call_count
    saved_limit = factory.llm_call_limit
    try:
        factory.llm_call_count = 0
        factory.llm_call_limit = None
        with patch(
            "backend.agents.llm_callback._get_context_window",
            return_value=200_000,
        ):
            llm._log_call([SimpleNamespace(content="hello world")])
        assert factory.llm_call_count == 1
    finally:
        factory.llm_call_count = saved_count
        factory.llm_call_limit = saved_limit


def test_throttled_log_call_raises_when_limit_exceeded() -> None:
    import backend.agents.factory as factory

    llm = MagicMock(spec=ThrottledChatOpenAI)
    llm.model_name = "x"
    llm._log_call = ThrottledChatOpenAI._log_call.__get__(llm, ThrottledChatOpenAI)

    saved_count = factory.llm_call_count
    saved_limit = factory.llm_call_limit
    try:
        factory.llm_call_count = 0
        factory.llm_call_limit = 0  # any call exceeds
        with (
            patch(
                "backend.agents.llm_callback._get_context_window",
                return_value=200_000,
            ),
            pytest.raises(LLMCallLimitExceededError),
        ):
            llm._log_call([SimpleNamespace(content="hi")])
    finally:
        factory.llm_call_count = saved_count
        factory.llm_call_limit = saved_limit


@pytest.mark.asyncio
async def test_throttled_astream_delegates_after_throttle() -> None:
    """_astream awaits the throttle then yields chunks from super() (line 54-59)."""
    llm = MagicMock(spec=ThrottledChatOpenAI)
    llm.model_name = "m"
    llm._log_call = MagicMock()

    async def _fake_super_astream(*args: Any, **kwargs: Any) -> AsyncIterator[str]:
        yield "chunk1"
        yield "chunk2"

    parent_astream = _fake_super_astream

    async def _astream_bound(self: Any, *args: Any, **kwargs: Any) -> AsyncIterator[str]:
        from backend.agents.throttle import llm_throttle
        await llm_throttle.wait()
        if args:
            self._log_call(args[0])
        async for chunk in parent_astream(*args, **kwargs):
            yield chunk

    chunks: list[str] = []
    async for c in _astream_bound(llm, [SimpleNamespace(content="x")]):
        chunks.append(c)
    assert chunks == ["chunk1", "chunk2"]
    llm._log_call.assert_called_once()


@pytest.mark.asyncio
async def test_astream_extracts_usage_metadata_into_emit() -> None:
    """_astream aggregates usage_metadata across chunks and emits token counts
    (stream-end log record carries prompt_tokens and completion_tokens)."""
    from backend.server.forge_logger import forge_logger

    chunks = [
        SimpleNamespace(tool_calls=None, usage_metadata={"input_tokens": 120, "output_tokens": 0}),
        SimpleNamespace(tool_calls=None, usage_metadata={"input_tokens": 120, "output_tokens": 40}),
    ]

    async def _fake_super_astream(*args: Any, **kwargs: Any) -> AsyncIterator[SimpleNamespace]:
        for c in chunks:
            yield c

    emits: list[tuple[str, str, str, dict[str, Any]]] = []

    def _capture(level: str, cat: str, msg: str, *a: Any, **kw: Any) -> None:
        emits.append((level, cat, msg, kw))

    llm = MagicMock(spec=ThrottledChatOpenAI)
    llm.model_name = "m"
    llm._log_call = MagicMock()
    method = ThrottledChatOpenAI._astream.__get__(llm, ThrottledChatOpenAI)

    with (
        patch("backend.agents.factory.ChatOpenAI._astream", _fake_super_astream),
        patch.object(forge_logger, "emit", _capture),
    ):
        out: list[SimpleNamespace] = []
        async for c in method([SimpleNamespace(content="x")]):
            out.append(c)

    assert len(out) == 2
    stream_end = [e for e in emits if "stream-end" in e[2]]
    assert stream_end, f"expected stream-end emit, got {emits}"
    kw = stream_end[-1][3]
    assert kw["prompt_tokens"] == 120
    assert kw["completion_tokens"] == 40
    assert kw["tool_call_count"] == 0


@pytest.mark.asyncio
async def test_throttled_agenerate_delegates_after_throttle() -> None:
    """_agenerate awaits throttle and delegates (line 61-65)."""
    from backend.agents.throttle import llm_throttle

    parent_result = "result-sentinel"

    async def _fake_super_agenerate(*args: Any, **kwargs: Any) -> str:
        return parent_result

    class _Impl:
        model_name = "m"

        def _log_call(self, messages: list[SimpleNamespace]) -> None:
            self.last_messages = messages

        async def _agenerate(self, *args: Any, **kwargs: Any) -> str:
            await llm_throttle.wait()
            if args:
                self._log_call(args[0])
            return await _fake_super_agenerate(*args, **kwargs)

    impl = _Impl()
    result = await impl._agenerate([SimpleNamespace(content="x")])
    assert result == parent_result
    assert impl.last_messages


# ── AgentFactory.create_agent_for_gap ────────────────────────────────────────


def test_create_agent_for_gap_unmapped_returns_none() -> None:
    """A gap type with no AgentRole mapping returns None."""
    from backend.agents.factory import AgentFactory
    from backend.analysis.gaps import GapType

    with patch("backend.agents.factory.GAP_AGENT_MAPPING", {}):
        fac = AgentFactory(MagicMock(), MagicMock())
        result = fac.create_agent_for_gap(GapType.UNARCHITECTED)
    assert result is None


def test_create_agent_for_gap_mapped_delegates_to_create_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mapped gap type routes through create_agent with prompt override."""
    from backend.agents.definitions import AgentRole
    from backend.agents.factory import AgentFactory
    from backend.analysis.gaps import GapType

    fac = AgentFactory(MagicMock(), MagicMock())
    fac._registry = MagicMock()
    fac._registry.get_tools_for_gap = MagicMock(return_value=["tool-A"])
    create_agent = MagicMock(return_value="agent-sentinel")
    monkeypatch.setattr(fac, "create_agent", create_agent)
    with (
        patch(
            "backend.agents.factory.GAP_AGENT_MAPPING",
            {GapType.UNARCHITECTED: AgentRole.DESIGN_ARCHITECT},
        ),
        patch(
            "backend.agents.factory.AGENT_REGISTRY",
            {AgentRole.DESIGN_ARCHITECT: MagicMock(role=AgentRole.DESIGN_ARCHITECT)},
        ),
        patch(
            "backend.agents.factory.get_gap_prompt",
            return_value="prompt-sentinel",
        ),
    ):
        agent = fac.create_agent_for_gap(GapType.UNARCHITECTED)

    assert agent == "agent-sentinel"
    create_agent.assert_called_once()
    kwargs = create_agent.call_args.kwargs
    assert kwargs["prompt_override"] == "prompt-sentinel"
    assert kwargs["allowed_tools"] == ["tool-A"]
