"""Tests for AgentFactory and AgentPool."""

import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from backend.agents import factory as factory_mod
from backend.agents.definitions import AGENT_REGISTRY, GAP_AGENT_MAPPING, AgentRole
from backend.agents.factory import (
    LLMCallLimitExceededError,
    ThrottledChatOpenAI,
    _extract_thinking,
)
from backend.agents.throttle import llm_throttle
from backend.analysis.gaps import GapType


def test_gap_agent_mapping_covers_all_gap_types() -> None:
    # UNSYNCED_DESIGN/TEST are handled by workspace_sync (no agent)
    # EMPTY_TRACE/CIRCULAR_TRACE are structural validations (no agent)
    programmatic = {
        GapType.UNSYNCED_DESIGN,
        GapType.UNSYNCED_TEST,
        GapType.EMPTY_TRACE,
        GapType.CIRCULAR_TRACE,
    }
    for gap_type in GapType:
        if gap_type in programmatic:
            continue
        assert gap_type in GAP_AGENT_MAPPING, f"GapType.{gap_type.name} has no agent mapping"


def test_all_roles_in_registry() -> None:
    for role in AgentRole:
        assert role in AGENT_REGISTRY, f"AgentRole.{role.name} not in AGENT_REGISTRY"


def test_key_agent_tool_permissions() -> None:
    """Tool permissions are defined in ToolRegistry, not AgentDefinition."""
    from backend.tools.registry import ToolRegistry

    registry = ToolRegistry()
    qa_tools = registry._role_permissions[AgentRole.QUALITY_AUDITOR]
    da_tools = registry._role_permissions[AgentRole.DESIGN_ARCHITECT]
    assert "file_read" in qa_tools
    assert "graph_read" in da_tools
    assert "graph_add_node" in da_tools


def test_agent_factory_creates_langgraph_agent_with_config() -> None:
    from backend.agents.factory import AgentFactory

    mock_config = MagicMock()
    mock_config.llm.base_url = "http://localhost:11434/v1"
    mock_config.llm.keyless = True  # explicit keyless local endpoint
    mock_config.llm.api_key_env = ""
    mock_config.llm.request_timeout = 120
    mock_config.llm.options.temperature = 0.8
    mock_config.llm.agents = {"Software Engineer": "test-model-123"}

    mock_registry = MagicMock()
    mock_registry.get_tools_for_role.return_value = []

    factory = AgentFactory(mock_registry, mock_config)
    defn = AGENT_REGISTRY[AgentRole.SOFTWARE_ENGINEER]
    mock_graph = MagicMock()

    with (
        patch("backend.agents.factory.ThrottledChatOpenAI") as mock_llm,
        patch("backend.agents.factory.create_react_agent", return_value=mock_graph) as mock_cra,
    ):
        result = factory.create_agent(defn)

        llm_kwargs = mock_llm.call_args[1]
        assert llm_kwargs["base_url"] == "http://localhost:11434/v1"
        # timeout is an httpx.Timeout with read=config value
        import httpx

        assert isinstance(llm_kwargs["timeout"], httpx.Timeout)
        assert llm_kwargs["timeout"].read == 120.0
        model = llm_kwargs["model"]
        assert model == "test-model-123"
        assert mock_cra.called
        assert result is mock_graph


# ── Shared helpers for ThrottledChatOpenAI tests ─────────────────────────────


def _make_llm() -> ThrottledChatOpenAI:
    """Build a minimal ThrottledChatOpenAI for direct method testing."""
    return ThrottledChatOpenAI(
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://localhost:1/v1",
    )


def _human_message(content: str) -> SimpleNamespace:
    """Build a fake human message with the given content."""
    return SimpleNamespace(content=content, type="human", role="user")


@pytest.fixture
def _llm_counters() -> Iterator[None]:
    """Save and restore the global LLM call counter and limit."""
    saved_count = factory_mod.llm_call_count
    saved_limit = factory_mod.llm_call_limit
    yield
    factory_mod.llm_call_count = saved_count
    factory_mod.llm_call_limit = saved_limit


# ── _extract_thinking ────────────────────────────────────────────────────────


def test_extract_thinking_none_input_returns_none() -> None:
    """None message yields no thinking snippet."""
    assert _extract_thinking(None) is None


def test_extract_thinking_string_content_returns_none() -> None:
    """Plain string content has no thinking blocks."""
    msg = SimpleNamespace(content="just text")
    assert _extract_thinking(msg) is None


def test_extract_thinking_returns_truncated_snippet() -> None:
    """A thinking block is truncated to 300 chars with newlines replaced."""
    long_text = "line1\nline2" + "x" * 400
    msg = SimpleNamespace(content=[
        {"type": "text", "text": "answer"},
        {"type": "thinking", "thinking": long_text},
    ])
    snippet = _extract_thinking(msg)
    assert snippet is not None
    assert snippet == long_text[:300].replace("\n", " ↵ ")
    assert "\n" not in snippet
    assert snippet.startswith("line1 ↵ line2")


def test_extract_thinking_empty_block_returns_none() -> None:
    """A thinking block with no text is skipped."""
    msg = SimpleNamespace(content=[{"type": "thinking", "thinking": ""}])
    assert _extract_thinking(msg) is None


# ── ThrottledChatOpenAI._log_call ────────────────────────────────────────────


def test_log_call_emits_prompt_snippet(_llm_counters: None) -> None:
    """The outbound record carries model, token estimate, and prompt snippet."""
    factory_mod.llm_call_limit = None
    llm = _make_llm()
    msg = _human_message("do the\nthing")

    with patch("backend.agents.factory.forge_logger") as mock_log:
        llm._log_call([msg])

    assert mock_log.emit.called
    kwargs = mock_log.emit.call_args[1]
    assert kwargs["model"] == "test-model"
    assert kwargs["prompt_snippet"] == "do the ↵ thing"


def test_log_call_without_human_message_has_no_snippet(_llm_counters: None) -> None:
    """No human/user message means prompt_snippet is None, not empty string."""
    factory_mod.llm_call_limit = None
    llm = _make_llm()
    msg = SimpleNamespace(content="system stuff", type="system", role="system")

    with patch("backend.agents.factory.forge_logger") as mock_log:
        llm._log_call([msg])

    assert mock_log.emit.call_args[1]["prompt_snippet"] is None


def test_log_call_raises_when_limit_exceeded(_llm_counters: None) -> None:
    """Exceeding the global call limit raises LLMCallLimitExceededError."""
    factory_mod.llm_call_count = 5
    factory_mod.llm_call_limit = 5
    llm = _make_llm()

    with pytest.raises(LLMCallLimitExceededError, match="6 > 5"):
        llm._log_call([_human_message("hi")])


# ── ThrottledChatOpenAI._astream ─────────────────────────────────────────────


async def test_astream_yields_chunks_and_logs_stream_end(_llm_counters: None) -> None:
    """Chunks pass through; stream-end carries usage, tool calls, and thinking."""
    factory_mod.llm_call_limit = None
    chunk1 = SimpleNamespace(
        usage_metadata={"input_tokens": 10, "output_tokens": 0},
        content=[{"type": "thinking", "thinking": "pondering"}],
        tool_calls=[],
        response_metadata={},
    )
    chunk2 = SimpleNamespace(
        usage_metadata={"input_tokens": 10, "output_tokens": 7},
        content="",
        tool_calls=[{"name": "file_read"}],
        response_metadata={},
    )

    async def fake_stream(self: Any, *args: Any, **kwargs: Any) -> Any:
        yield chunk1
        yield chunk2

    with (
        patch("backend.agents.factory.forge_logger") as mock_log,
        patch("langchain_openai.ChatOpenAI._astream", fake_stream),
        patch.object(llm_throttle, "wait", AsyncMock()),
    ):
        llm = _make_llm()
        out = [c async for c in llm._astream([_human_message("go")])]

    assert out == [chunk1, chunk2]
    stream_end = next(
        c for c in mock_log.emit.call_args_list if "stream-end" in c[0][2]
    )
    assert stream_end[1]["prompt_tokens"] == 10
    assert stream_end[1]["completion_tokens"] == 7
    assert stream_end[1]["tool_call_count"] == 1
    assert stream_end[1]["thinking"] == "pondering"


async def test_astream_falls_back_to_response_metadata_usage(
    _llm_counters: None,
) -> None:
    """Missing usage_metadata falls back to response_metadata token_usage."""
    factory_mod.llm_call_limit = None
    chunk = SimpleNamespace(
        usage_metadata=None,
        content="",
        tool_calls=[],
        response_metadata={"token_usage": {"prompt_tokens": 5, "completion_tokens": 3}},
    )

    async def fake_stream(self: Any, *args: Any, **kwargs: Any) -> Any:
        yield chunk

    with (
        patch("backend.agents.factory.forge_logger") as mock_log,
        patch("langchain_openai.ChatOpenAI._astream", fake_stream),
        patch.object(llm_throttle, "wait", AsyncMock()),
    ):
        llm = _make_llm()
        _ = [c async for c in llm._astream([_human_message("go")])]

    stream_end = next(
        c for c in mock_log.emit.call_args_list if "stream-end" in c[0][2]
    )
    assert stream_end[1]["prompt_tokens"] == 5
    assert stream_end[1]["completion_tokens"] == 3


async def test_astream_logs_and_reraises_errors(_llm_counters: None) -> None:
    """A streaming failure is logged via llm_error and re-raised."""
    factory_mod.llm_call_limit = None

    async def fake_stream(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise ValueError("boom")
        yield  # pragma: no cover

    with (
        patch("backend.agents.factory.forge_logger") as mock_log,
        patch("langchain_openai.ChatOpenAI._astream", fake_stream),
        patch.object(llm_throttle, "wait", AsyncMock()),
    ):
        llm = _make_llm()
        with pytest.raises(ValueError, match="boom"):
            _ = [c async for c in llm._astream([_human_message("go")])]

    mock_log.llm_error.assert_called_once()
    assert "ValueError: boom" in mock_log.llm_error.call_args[0][1]


# ── ThrottledChatOpenAI._agenerate ───────────────────────────────────────────


async def test_agenerate_logs_response_with_usage(_llm_counters: None) -> None:
    """The response record carries duration, tool calls, and token usage."""
    factory_mod.llm_call_limit = None
    message = SimpleNamespace(
        content="hello world",
        tool_calls=[{"name": "graph_read"}],
        usage_metadata={"input_tokens": 3, "output_tokens": 2},
        response_metadata={},
    )
    result = SimpleNamespace(generations=[SimpleNamespace(message=message)])

    with (
        patch("backend.agents.factory.forge_logger") as mock_log,
        patch("langchain_openai.ChatOpenAI._agenerate", AsyncMock(return_value=result)),
        patch.object(llm_throttle, "wait", AsyncMock()),
    ):
        llm = _make_llm()
        out = await llm._agenerate([_human_message("go")])

    assert out is result
    response = next(
        c for c in mock_log.emit.call_args_list if c[0][2].startswith("response")
    )
    assert response[1]["prompt_tokens"] == 3
    assert response[1]["completion_tokens"] == 2
    assert response[1]["tool_call_count"] == 1


async def test_agenerate_falls_back_to_response_metadata_usage(
    _llm_counters: None,
) -> None:
    """Missing usage_metadata falls back to response_metadata token_usage."""
    factory_mod.llm_call_limit = None
    message = SimpleNamespace(
        content="hi",
        tool_calls=[],
        usage_metadata={},
        response_metadata={"token_usage": {"prompt_tokens": 9, "completion_tokens": 4}},
    )
    result = SimpleNamespace(generations=[SimpleNamespace(message=message)])

    with (
        patch("backend.agents.factory.forge_logger") as mock_log,
        patch("langchain_openai.ChatOpenAI._agenerate", AsyncMock(return_value=result)),
        patch.object(llm_throttle, "wait", AsyncMock()),
    ):
        llm = _make_llm()
        await llm._agenerate([_human_message("go")])

    response = next(
        c for c in mock_log.emit.call_args_list if c[0][2].startswith("response")
    )
    assert response[1]["prompt_tokens"] == 9
    assert response[1]["completion_tokens"] == 4


async def test_agenerate_handles_empty_generations(_llm_counters: None) -> None:
    """A result with no generations logs zero counts without crashing."""
    factory_mod.llm_call_limit = None
    result = SimpleNamespace(generations=[])

    with (
        patch("backend.agents.factory.forge_logger") as mock_log,
        patch("langchain_openai.ChatOpenAI._agenerate", AsyncMock(return_value=result)),
        patch.object(llm_throttle, "wait", AsyncMock()),
    ):
        llm = _make_llm()
        out = await llm._agenerate([_human_message("go")])

    assert out is result
    response = next(
        c for c in mock_log.emit.call_args_list if c[0][2].startswith("response")
    )
    assert response[1]["tool_call_count"] == 0
    assert response[1]["prompt_tokens"] is None


async def test_agenerate_logs_and_reraises_errors(_llm_counters: None) -> None:
    """A generation failure is logged via llm_error and re-raised."""
    factory_mod.llm_call_limit = None

    with (
        patch("backend.agents.factory.forge_logger") as mock_log,
        patch(
            "langchain_openai.ChatOpenAI._agenerate",
            AsyncMock(side_effect=RuntimeError("api down")),
        ),
        patch.object(llm_throttle, "wait", AsyncMock()),
    ):
        llm = _make_llm()
        with pytest.raises(RuntimeError, match="api down"):
            await llm._agenerate([_human_message("go")])

    mock_log.llm_error.assert_called_once()
    assert "RuntimeError: api down" in mock_log.llm_error.call_args[0][1]


# ── AgentFactory.create_agent_for_gap ────────────────────────────────────────


def _gap_factory_config() -> MagicMock:
    """Build a mock ForgeConfig sufficient for gap-agent construction."""
    mock_config = MagicMock()
    mock_config.llm.base_url = "http://localhost:11434/v1"
    mock_config.llm.keyless = True
    mock_config.llm.api_key_env = ""
    mock_config.llm.request_timeout = 120
    mock_config.llm.options.temperature = 0.8
    mock_config.llm.cache_enabled = False
    mock_config.llm.agents = {"Requirements Engineer": "gap-model"}
    mock_config.llm.context_window_for_model.return_value = 8000
    return mock_config


def test_create_agent_for_gap_unmapped_returns_none() -> None:
    """Gap types handled programmatically (no agent mapping) return None."""
    from backend.agents.factory import AgentFactory

    factory = AgentFactory(MagicMock(), MagicMock())
    assert factory.create_agent_for_gap(GapType.UNSYNCED_DESIGN) is None


def test_create_agent_for_gap_builds_whitelisted_agent() -> None:
    """A mapped gap type builds an agent with the gap-specific tool whitelist."""
    from backend.agents.factory import AgentFactory

    mock_registry = MagicMock()
    gap_tools = [MagicMock(name="graph_add_node")]
    mock_registry.get_tools_for_gap.return_value = gap_tools
    factory = AgentFactory(mock_registry, _gap_factory_config())
    mock_graph = MagicMock()

    with (
        patch("backend.agents.factory.ThrottledChatOpenAI"),
        patch(
            "backend.agents.factory.create_react_agent", return_value=mock_graph
        ) as mock_cra,
    ):
        result = factory.create_agent_for_gap(GapType.UNCOVERED_PARA)

    assert result is mock_graph
    mock_registry.get_tools_for_gap.assert_called_once_with(GapType.UNCOVERED_PARA)
    mock_registry.get_tools_for_role.assert_not_called()
    assert mock_cra.call_args[1]["tools"] is gap_tools


class TestTransportJsonRetry:
    """A malformed provider body (json.JSONDecodeError from the openai client)
    is retried exactly once at the transport seam — every call site is
    covered without per-caller handling. Live evidence: two builds crashed
    at different call sites (dedup judge, case_trace_check) on the same
    flaky-body error class."""

    @staticmethod
    def _good_result() -> SimpleNamespace:
        message = SimpleNamespace(
            content="ok", tool_calls=[], usage_metadata={}, response_metadata={},
        )
        return SimpleNamespace(generations=[SimpleNamespace(message=message)])

    @pytest.mark.asyncio
    async def test_agenerate_retries_malformed_body_once(self) -> None:
        llm = _make_llm()
        good = self._good_result()
        calls = {"n": 0}

        async def flaky(*args: object, **kwargs: object) -> object:
            calls["n"] += 1
            if calls["n"] == 1:
                raise json.JSONDecodeError("Expecting value", "garbage-body", 0)
            return good

        with patch.object(ChatOpenAI, "_agenerate", new=flaky):
            result = await llm._agenerate([HumanMessage(content="hi")])
        assert result is good
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_agenerate_double_malformed_body_raises(self) -> None:
        llm = _make_llm()

        async def always_bad(*args: object, **kwargs: object) -> object:
            raise json.JSONDecodeError("Expecting value", "garbage-body", 0)

        with patch.object(ChatOpenAI, "_agenerate", new=always_bad), \
                pytest.raises(json.JSONDecodeError):
            await llm._agenerate([HumanMessage(content="hi")])
