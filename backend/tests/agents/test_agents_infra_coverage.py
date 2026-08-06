"""Coverage tests for agent/infra modules: streaming, factory, llm_callback, pool, config/loader, forge_logger."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. streaming.py
# ---------------------------------------------------------------------------
from backend.agents.streaming import AgentTurn, iter_agent_turns


class TestAgentTurnDataclass:
    def test_defaults(self) -> None:
        turn = AgentTurn()
        assert turn.message is None
        assert turn.tool_calls == []
        assert turn.text_content == ""

    def test_tool_calls_not_shared(self) -> None:
        t1 = AgentTurn()
        t2 = AgentTurn()
        t1.tool_calls.append({"name": "x"})
        assert t2.tool_calls == []


def _make_agent(events: list[dict[str, Any]]) -> MagicMock:
    """Create a mock agent whose astream_events returns an async iterator."""
    agent = MagicMock()
    agent.astream_events = MagicMock(return_value=_async_iter(events))
    return agent


@pytest.mark.asyncio
async def test_iter_agent_turns_skips_non_chat_model_end() -> None:
    """Events that are not on_chat_model_end should be skipped entirely."""
    agent = _make_agent(
        [
            {"event": "on_chain_start", "data": {}},
            {"event": "on_tool_start", "data": {}},
        ]
    )
    turns = [t async for t in iter_agent_turns(agent, [])]
    assert turns == []


@pytest.mark.asyncio
async def test_iter_agent_turns_skips_none_output() -> None:
    """on_chat_model_end with output=None should be skipped."""
    agent = _make_agent(
        [
            {"event": "on_chat_model_end", "data": {"output": None}},
        ]
    )
    turns = [t async for t in iter_agent_turns(agent, [])]
    assert turns == []


@pytest.mark.asyncio
async def test_iter_agent_turns_tool_calls_dict() -> None:
    """Tool calls supplied as dicts are captured."""
    from langchain_core.messages import AIMessage

    msg = AIMessage(content="")
    msg.tool_calls = [{"name": "read_file", "args": {"path": "/a"}, "id": "call-1"}]

    agent = _make_agent(
        [
            {"event": "on_chat_model_end", "data": {"output": msg}},
        ]
    )

    with patch("backend.agents.streaming.forge_logger"):
        turns = [t async for t in iter_agent_turns(agent, [])]
    assert len(turns) == 1
    assert turns[0].tool_calls == [{"name": "read_file", "args": {"path": "/a"}}]
    assert turns[0].text_content == ""


@pytest.mark.asyncio
async def test_iter_agent_turns_tool_calls_object() -> None:
    """Tool calls supplied as objects with attrs are captured."""
    from langchain_core.messages import AIMessage

    tc = SimpleNamespace(name="write_file", args={"content": "hi"})
    msg = AIMessage(content="")
    # Deliberately non-conforming: exercises the attribute-based (non-dict)
    # branch of iter_agent_turns' tool-call extraction.
    msg.tool_calls = [tc]  # type: ignore[list-item]

    agent = _make_agent(
        [
            {"event": "on_chat_model_end", "data": {"output": msg}},
        ]
    )

    with patch("backend.agents.streaming.forge_logger"):
        turns = [t async for t in iter_agent_turns(agent, [])]
    assert len(turns) == 1
    assert turns[0].tool_calls[0]["name"] == "write_file"


@pytest.mark.asyncio
async def test_iter_agent_turns_text_content() -> None:
    """When no tool calls, text_content is filled from msg.content."""
    from langchain_core.messages import AIMessage

    msg = AIMessage(content="Hello world")
    # AIMessage has tool_calls=[] by default, which is falsy

    agent = _make_agent(
        [
            {"event": "on_chat_model_end", "data": {"output": msg}},
        ]
    )

    with patch("backend.agents.streaming.forge_logger"):
        turns = [t async for t in iter_agent_turns(agent, [])]
    assert len(turns) == 1
    assert turns[0].text_content == "Hello world"
    assert turns[0].tool_calls == []


@pytest.mark.asyncio
async def test_iter_agent_turns_unwraps_message_wrapper() -> None:
    """If output has a .message attribute, it should be unwrapped."""
    from langchain_core.messages import AIMessage

    inner = AIMessage(content="unwrapped")
    wrapper = SimpleNamespace(message=inner)

    agent = _make_agent(
        [
            {"event": "on_chat_model_end", "data": {"output": wrapper}},
        ]
    )

    with patch("backend.agents.streaming.forge_logger"):
        turns = [t async for t in iter_agent_turns(agent, [])]
    assert len(turns) == 1
    assert turns[0].message is inner
    assert turns[0].text_content == "unwrapped"


@pytest.mark.asyncio
async def test_iter_agent_turns_with_model_label_logs() -> None:
    """When model is provided, logging path runs including usage metadata."""
    from langchain_core.messages import AIMessage
    from langchain_core.messages.ai import UsageMetadata

    msg = AIMessage(content="ok")
    usage: UsageMetadata = {"input_tokens": 500, "output_tokens": 50, "total_tokens": 550}
    msg.usage_metadata = usage

    agent = _make_agent(
        [
            {"event": "on_chat_model_end", "data": {"output": msg}},
        ]
    )

    mock_logger = MagicMock()
    with (
        patch("backend.agents.streaming.forge_logger", mock_logger),
        patch("backend.agents.llm_callback._get_context_window", return_value=128000),
    ):
        turns = [t async for t in iter_agent_turns(agent, [], model="gpt-4", label="test")]
    assert len(turns) == 1
    # The first emit call should have model metadata
    mock_logger.emit.assert_called()


@pytest.mark.asyncio
async def test_iter_agent_turns_thread_id_sets_configurable() -> None:
    """When thread_id is set, config should include configurable."""
    agent = _make_agent([])

    with patch("backend.agents.streaming.forge_logger"):
        _ = [t async for t in iter_agent_turns(agent, [], thread_id="t-1")]

    call_args = agent.astream_events.call_args
    config = call_args.kwargs.get("config")
    assert config is not None
    assert config["configurable"]["thread_id"] == "t-1"


# ---------------------------------------------------------------------------
# 2. factory.py — prompt management
# ---------------------------------------------------------------------------
from backend.agents.factory import (
    _GAP_PROMPTS,
    _ROLE_PROMPTS,
    build_llm,
    gap_inherits_from_role,
    get_gap_prompt,
    get_prompt,
    is_default_gap_prompt,
    is_default_prompt,
    reset_gap_prompt,
    reset_prompt,
    set_gap_prompt,
    set_prompt,
)


@pytest.fixture(autouse=True)
def _clear_prompt_stores() -> Iterator[None]:
    """Ensure prompt stores are clean for every test."""
    _ROLE_PROMPTS.clear()
    _GAP_PROMPTS.clear()
    yield
    _ROLE_PROMPTS.clear()
    _GAP_PROMPTS.clear()


class TestRolePrompts:
    def test_get_prompt_default(self) -> None:
        with patch("backend.prompting.loader.render", return_value="rendered") as mock_render:
            result = get_prompt("Console")
        assert result == "rendered"
        mock_render.assert_called_once_with("roles/console.j2")

    def test_get_prompt_unknown_role_fallback(self) -> None:
        result = get_prompt("Unknown Role")
        assert "Unknown Role" in result

    def test_set_and_get_prompt(self) -> None:
        set_prompt("Console", "custom prompt")
        assert get_prompt("Console") == "custom prompt"

    def test_is_default_prompt(self) -> None:
        assert is_default_prompt("Console") is True
        set_prompt("Console", "custom")
        assert is_default_prompt("Console") is False

    def test_reset_prompt(self) -> None:
        set_prompt("Console", "custom")
        reset_prompt("Console")
        assert is_default_prompt("Console") is True

    def test_reset_prompt_noop_when_not_set(self) -> None:
        reset_prompt("NonExistent")  # Should not raise


class TestGapPrompts:
    def test_gap_prompt_falls_through_to_role(self) -> None:
        """When no gap override and no built-in gap prompt, falls to role prompt."""
        set_prompt("Requirements Engineer", "role override")
        with patch("backend.agents.factory.get_default_gap_prompt", return_value=None):
            result = get_gap_prompt("UNCOVERED_PARA", "Requirements Engineer")
        assert result == "role override"

    def test_gap_prompt_user_override(self) -> None:
        set_gap_prompt("UNCOVERED_PARA", "user gap override")
        result = get_gap_prompt("UNCOVERED_PARA", "Requirements Engineer")
        assert result == "user gap override"

    def test_gap_prompt_builtin_default(self) -> None:
        """Built-in gap default takes precedence over role prompt."""
        with patch("backend.agents.factory.get_default_gap_prompt", return_value="builtin gap"):
            result = get_gap_prompt("UNCOVERED_PARA", "Requirements Engineer")
        assert result == "builtin gap"

    def test_set_and_reset_gap_prompt(self) -> None:
        set_gap_prompt("UNCOVERED_PARA", "override")
        assert is_default_gap_prompt("UNCOVERED_PARA") is False
        reset_gap_prompt("UNCOVERED_PARA")
        assert is_default_gap_prompt("UNCOVERED_PARA") is True

    def test_gap_inherits_from_role_true(self) -> None:
        with patch("backend.agents.factory.has_default_gap_prompt", return_value=False):
            assert gap_inherits_from_role("UNCOVERED_PARA") is True

    def test_gap_inherits_from_role_false_when_builtin(self) -> None:
        with patch("backend.agents.factory.has_default_gap_prompt", return_value=True):
            assert gap_inherits_from_role("UNCOVERED_PARA") is False

    def test_gap_inherits_from_role_false_when_user_override(self) -> None:
        set_gap_prompt("UNCOVERED_PARA", "user")
        with patch("backend.agents.factory.has_default_gap_prompt", return_value=False):
            assert gap_inherits_from_role("UNCOVERED_PARA") is False


def _llm_config(
    *, keyless: bool, api_key_env: str, cache_enabled: bool, cache_dir: str
) -> MagicMock:
    config = MagicMock()
    config.llm.keyless = keyless
    config.llm.api_key_env = api_key_env
    config.llm.agents = {"Quality Auditor": "qwen-72b"}
    config.llm.base_url = "http://localhost:11434/v1"
    config.llm.options.temperature = 0.7
    config.llm.request_timeout = 120
    config.llm.cache_enabled = cache_enabled
    config.llm.cache_dir = cache_dir
    return config


class TestBuildLLM:
    def test_build_llm_defaults(self) -> None:
        config = _llm_config(
            keyless=False, api_key_env="MY_KEY", cache_enabled=False, cache_dir=".cache"
        )

        with patch.dict("os.environ", {"MY_KEY": "test-key"}):
            with patch("backend.agents.factory.ThrottledChatOpenAI") as mock_llm:
                build_llm(config, cacheable=True)
                kw = mock_llm.call_args[1]
                assert kw["model"] == "qwen-72b"
                assert kw["api_key"] == "test-key"
                assert kw["temperature"] == 0.7

    def test_build_llm_enables_stream_usage(self) -> None:
        """Streamed calls must request the final usage chunk — without
        stream_options.include_usage every streamed call records 0 tokens."""
        config = _llm_config(
            keyless=True, api_key_env="", cache_enabled=False, cache_dir=".cache"
        )

        with patch("backend.agents.factory.ThrottledChatOpenAI") as mock_llm:
            build_llm(config, cacheable=True)
            assert mock_llm.call_args[1]["stream_usage"] is True

    def test_build_llm_retries_transient_failures(self) -> None:
        """One transient network error must not kill a check outright."""
        config = _llm_config(
            keyless=False, api_key_env="MY_KEY", cache_enabled=False, cache_dir=".cache"
        )

        with patch.dict("os.environ", {"MY_KEY": "test-key"}):
            with patch("backend.agents.factory.ThrottledChatOpenAI") as mock_llm:
                build_llm(config, cacheable=True)
                assert mock_llm.call_args[1]["max_retries"] >= 2

    def test_build_llm_explicit_model_and_temp(self) -> None:
        config = _llm_config(
            keyless=False, api_key_env="MY_KEY", cache_enabled=False, cache_dir=".cache"
        )

        with patch.dict("os.environ", {"MY_KEY": "test-key"}):
            with patch("backend.agents.factory.ThrottledChatOpenAI") as mock_llm:
                build_llm(config, model="custom-model", temperature=0.2, cacheable=True)
                kw = mock_llm.call_args[1]
                assert kw["model"] == "custom-model"
                assert kw["temperature"] == 0.2

    def test_build_llm_raises_when_api_key_env_unset(self) -> None:
        """A missing key is a configuration error at construction — never a
        silent 'ollama' fallback that surfaces as mid-run 401s."""
        config = _llm_config(
            keyless=False, api_key_env="MISSING_KEY_XYZ", cache_enabled=False, cache_dir=".cache"
        )

        with patch.dict("os.environ", {}, clear=True):
            with patch("backend.agents.factory.ThrottledChatOpenAI"):
                with pytest.raises(RuntimeError, match="MISSING_KEY_XYZ"):
                    build_llm(config, cacheable=True)

    def test_build_llm_raises_when_api_key_env_empty_string(self) -> None:
        config = _llm_config(
            keyless=False, api_key_env="EMPTY_KEY", cache_enabled=False, cache_dir=".cache"
        )

        with patch.dict("os.environ", {"EMPTY_KEY": ""}):
            with patch("backend.agents.factory.ThrottledChatOpenAI"):
                with pytest.raises(RuntimeError, match="EMPTY_KEY"):
                    build_llm(config, cacheable=True)

    def test_build_llm_raises_when_api_key_env_name_blank(self) -> None:
        config = _llm_config(
            keyless=False, api_key_env="", cache_enabled=False, cache_dir=".cache"
        )

        with patch("backend.agents.factory.ThrottledChatOpenAI"):
            with pytest.raises(RuntimeError, match="api_key_env"):
                build_llm(config, cacheable=True)

    def test_build_llm_keyless_endpoint_needs_no_key(self) -> None:
        """llm.keyless = true is the explicit opt-in for local keyless
        endpoints (e.g. Ollama) — a placeholder key is used."""
        config = _llm_config(
            keyless=True, api_key_env="", cache_enabled=False, cache_dir=".cache"
        )

        with patch.dict("os.environ", {}, clear=True):
            with patch("backend.agents.factory.ThrottledChatOpenAI") as mock_llm:
                build_llm(config, cacheable=True)
                assert mock_llm.call_args[1]["api_key"] == "ollama"

    def test_build_llm_cacheable_requires_explicit_argument(self) -> None:
        """cacheable has no default — every construction site must state its
        cache participation explicitly (independence exemption, §7.4)."""
        config = _llm_config(
            keyless=True, api_key_env="", cache_enabled=True, cache_dir=".cache"
        )

        with patch("backend.agents.factory.ThrottledChatOpenAI"):
            with pytest.raises(TypeError, match="cacheable"):
                build_llm(config)  # type: ignore[call-arg]

    def test_build_llm_cacheable_true_gets_sqlite_cache(self, tmp_path: Path) -> None:
        from backend.agents.llm_cache import SQLiteLLMCache

        config = _llm_config(
            keyless=True, api_key_env="", cache_enabled=True, cache_dir=str(tmp_path)
        )

        with patch("backend.agents.factory.ThrottledChatOpenAI") as mock_llm:
            build_llm(config, cacheable=True)
            cache = mock_llm.call_args[1]["cache"]
            assert isinstance(cache, SQLiteLLMCache)
            assert cache.db_path == tmp_path / "llm_cache.db"

    def test_build_llm_relative_cache_dir_resolves_to_repo_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A relative cache_dir must not land in the per-test cwd — repeated
        runs would each get a cold cache in a throwaway directory."""
        from pathlib import Path

        from backend.agents.llm_cache import SQLiteLLMCache

        config = _llm_config(
            keyless=True, api_key_env="", cache_enabled=True, cache_dir=".cache"
        )
        monkeypatch.chdir(tmp_path)

        with patch("backend.agents.factory.ThrottledChatOpenAI") as mock_llm:
            build_llm(config, cacheable=True)
            cache = mock_llm.call_args[1]["cache"]
            assert isinstance(cache, SQLiteLLMCache)
            assert cache.db_path.is_absolute()
            assert not cache.db_path.is_relative_to(tmp_path)
            import backend
            repo_root = Path(backend.__file__).resolve().parent.parent
            assert cache.db_path == repo_root / ".cache" / "llm_cache.db"

    def test_build_llm_cacheable_false_constructs_uncached_model(self, tmp_path: Path) -> None:
        """cacheable=False must yield cache=False even with caching enabled —
        the dedup double-confirmation depends on genuinely independent calls."""
        config = _llm_config(
            keyless=True, api_key_env="", cache_enabled=True, cache_dir=str(tmp_path)
        )

        with patch("backend.agents.factory.ThrottledChatOpenAI") as mock_llm:
            build_llm(config, cacheable=False)
            assert mock_llm.call_args[1]["cache"] is False

    def test_build_llm_cache_enabled_false_disables_caching(self, tmp_path: Path) -> None:
        config = _llm_config(
            keyless=True, api_key_env="", cache_enabled=False, cache_dir=str(tmp_path)
        )

        with patch("backend.agents.factory.ThrottledChatOpenAI") as mock_llm:
            build_llm(config, cacheable=True)
            assert mock_llm.call_args[1]["cache"] is False

    def test_llm_config_cache_defaults(self) -> None:
        from backend.config.models import LLMConfig

        llm = LLMConfig()
        assert llm.cache_enabled is True
        assert llm.cache_dir == ".cache"


# ---------------------------------------------------------------------------
# 3. llm_callback.py
# ---------------------------------------------------------------------------
from backend.agents.llm_callback import (
    ForgeLLMCallback,
    _get_context_window,
    set_context_window,
)


class TestContextWindow:
    def setup_method(self) -> None:
        # Reset to default
        set_context_window(128_000)

    def test_set_and_get(self) -> None:
        set_context_window(64_000)
        assert _get_context_window() == 64_000

    def test_clamps_to_min(self) -> None:
        set_context_window(100)
        assert _get_context_window() == 4096

    def test_clamps_zero(self) -> None:
        set_context_window(0)
        assert _get_context_window() == 4096

    def test_negative(self) -> None:
        set_context_window(-1000)
        assert _get_context_window() == 4096

    def test_exactly_4096(self) -> None:
        set_context_window(4096)
        assert _get_context_window() == 4096

    def teardown_method(self) -> None:
        set_context_window(128_000)


class TestForgeLLMCallbackPreApiCall:
    def test_log_pre_api_call(self) -> None:
        cb = ForgeLLMCallback()
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello world"},
        ]
        kwargs = {"metadata": {"agent": "TestAgent"}}

        with patch("backend.server.forge_logger.forge_logger") as mock_logger:
            cb.log_pre_api_call("gpt-4", messages, kwargs)
            mock_logger.llm_call.assert_called_once()
            call_args = mock_logger.llm_call.call_args
            assert call_args[0][0] == "gpt-4"
            assert "TestAgent" in call_args[0][1]
            mock_logger.llm_prompt.assert_called_once_with("gpt-4", messages)

    def test_log_pre_api_call_empty_metadata(self) -> None:
        cb = ForgeLLMCallback()
        messages = [{"role": "user", "content": "hi"}]
        kwargs: dict[str, Any] = {}

        with patch("backend.server.forge_logger.forge_logger") as mock_logger:
            cb.log_pre_api_call("model-x", messages, kwargs)
            mock_logger.llm_call.assert_called_once()
            # agent_hint should be "?" when no metadata
            assert "?" in mock_logger.llm_call.call_args[0][1]

    def test_log_pre_api_call_task_description(self) -> None:
        cb = ForgeLLMCallback()
        messages = [{"role": "user", "content": "x"}]
        kwargs = {"metadata": {"task_description": "Resolve gap"}}

        with patch("backend.server.forge_logger.forge_logger") as mock_logger:
            cb.log_pre_api_call("model", messages, kwargs)
            assert "Resolve gap" in mock_logger.llm_call.call_args[0][1]


class TestForgeLLMCallbackSuccessEvent:
    def _make_response(
        self,
        content: str = "ok",
        tool_calls: list[SimpleNamespace] | None = None,
        prompt_tokens: int = 100,
        completion_tokens: int = 50,
    ) -> SimpleNamespace:
        choice_msg = SimpleNamespace(content=content, tool_calls=tool_calls or [])
        choice = SimpleNamespace(message=choice_msg)
        usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
        return SimpleNamespace(choices=[choice], usage=usage)

    def test_log_success_basic(self) -> None:
        cb = ForgeLLMCallback()
        resp = self._make_response()
        start = datetime(2026, 1, 1, 0, 0, 0)
        end = datetime(2026, 1, 1, 0, 0, 1)

        with patch("backend.server.forge_logger.forge_logger") as mock_logger:
            cb.log_success_event({"model": "gpt-4"}, resp, start, end)
            mock_logger.llm_response.assert_called_once()
            call_kw = mock_logger.llm_response.call_args
            assert call_kw[0][0] == "gpt-4"  # model
            assert call_kw[0][1] == 50  # completion_tokens
            assert call_kw[0][2] == pytest.approx(1000.0)  # elapsed_ms
            assert call_kw[0][3] is None  # no tool
            mock_logger.llm_content.assert_called_once()

    def test_log_success_with_tool_calls(self) -> None:
        cb = ForgeLLMCallback()
        fn = SimpleNamespace(name="read_file", arguments='{"path": "/a"}')
        tc = SimpleNamespace(function=fn)
        resp = self._make_response(content="", tool_calls=[tc])
        start = datetime(2026, 1, 1, 0, 0, 0)
        end = datetime(2026, 1, 1, 0, 0, 0, 500000)

        with patch("backend.server.forge_logger.forge_logger") as mock_logger:
            cb.log_success_event({"model": "m"}, resp, start, end)
            resp_call = mock_logger.llm_response.call_args
            assert resp_call[0][3] == "read_file"  # first_tool name

    def test_log_success_no_choices(self) -> None:
        cb = ForgeLLMCallback()
        resp = SimpleNamespace(
            choices=[], usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0)
        )
        start = datetime(2026, 1, 1, 0, 0, 0)
        end = datetime(2026, 1, 1, 0, 0, 0)

        with patch("backend.server.forge_logger.forge_logger") as mock_logger:
            cb.log_success_event({"model": "m"}, resp, start, end)
            mock_logger.llm_response.assert_called_once()

    def test_log_success_missing_model(self) -> None:
        cb = ForgeLLMCallback()
        resp = self._make_response()
        start = datetime(2026, 1, 1, 0, 0, 0)
        end = datetime(2026, 1, 1, 0, 0, 0)

        with patch("backend.server.forge_logger.forge_logger") as mock_logger:
            cb.log_success_event({}, resp, start, end)
            assert mock_logger.llm_response.call_args[0][0] == "?"


class TestForgeLLMCallbackFailureEvent:
    def test_log_failure(self) -> None:
        cb = ForgeLLMCallback()
        start = datetime(2026, 1, 1, 0, 0, 0)
        end = datetime(2026, 1, 1, 0, 0, 1)

        with patch("backend.server.forge_logger.forge_logger") as mock_logger:
            cb.log_failure_event(
                {"model": "gpt-4"},
                "Connection timeout",
                start,
                end,
            )
            mock_logger.llm_error.assert_called_once_with("gpt-4", "Connection timeout")

    def test_log_failure_missing_model(self) -> None:
        cb = ForgeLLMCallback()
        start = datetime(2026, 1, 1, 0, 0, 0)
        end = datetime(2026, 1, 1, 0, 0, 0)

        with patch("backend.server.forge_logger.forge_logger") as mock_logger:
            cb.log_failure_event({}, "err", start, end)
            assert mock_logger.llm_error.call_args[0][0] == "?"


# ---------------------------------------------------------------------------
# 4. pool.py
# ---------------------------------------------------------------------------
from backend.agents.pool import AgentPool


class TestAgentPoolInitialise:
    @pytest.mark.asyncio
    async def test_initialise_creates_agents(self) -> None:
        broadcaster = MagicMock()
        pool = AgentPool(broadcaster)
        factory = MagicMock()
        factory.create_agent.return_value = MagicMock()
        factory.create_agent_for_gap.return_value = MagicMock()
        config = MagicMock()

        with patch("backend.agents.pool.phase_context") as mock_pc:
            mock_pc.get_checkpointer.return_value = None
            await pool.initialise(factory, config)

        # At least one agent registered per role
        assert len(pool.all_ids()) > 0

    @pytest.mark.asyncio
    async def test_initialise_registers_gap_agents(self) -> None:
        broadcaster = MagicMock()
        pool = AgentPool(broadcaster)
        factory = MagicMock()
        factory.create_agent.return_value = MagicMock()
        factory.create_agent_for_gap.return_value = MagicMock()
        config = MagicMock()

        with patch("backend.agents.pool.phase_context") as mock_pc:
            mock_pc.get_checkpointer.return_value = None
            await pool.initialise(factory, config)

        assert len(pool._gap_agents) > 0

    @pytest.mark.asyncio
    async def test_initialise_skips_none_gap_agents(self) -> None:
        broadcaster = MagicMock()
        pool = AgentPool(broadcaster)
        factory = MagicMock()
        factory.create_agent.return_value = MagicMock()
        factory.create_agent_for_gap.return_value = None
        config = MagicMock()

        with patch("backend.agents.pool.phase_context") as mock_pc:
            mock_pc.get_checkpointer.return_value = None
            await pool.initialise(factory, config)

        assert len(pool._gap_agents) == 0


class TestAgentPoolRebuild:
    def test_rebuild_without_prior_init_noop(self) -> None:
        broadcaster = MagicMock()
        pool = AgentPool(broadcaster)
        pool.rebuild()  # Should not raise

    def test_rebuild_recreates_agents(self) -> None:
        broadcaster = MagicMock()
        pool = AgentPool(broadcaster)
        factory = MagicMock()
        factory.create_agent.return_value = MagicMock()
        factory.create_agent_for_gap.return_value = MagicMock()
        pool._factory = factory

        new_config = MagicMock()
        with patch("backend.agents.pool.phase_context") as mock_pc:
            mock_pc.get_checkpointer.return_value = None
            pool.rebuild(config=new_config)

        assert factory._config is new_config
        assert factory.create_agent.called
        mock_pc.reset_all.assert_called_once()

    def test_rebuild_preserves_factory_config_when_none(self) -> None:
        broadcaster = MagicMock()
        pool = AgentPool(broadcaster)
        factory = MagicMock()
        factory.create_agent.return_value = MagicMock()
        factory.create_agent_for_gap.return_value = MagicMock()
        original_config = factory._config
        pool._factory = factory

        with patch("backend.agents.pool.phase_context") as mock_pc:
            mock_pc.get_checkpointer.return_value = None
            pool.rebuild(config=None)

        assert factory._config is original_config


# ---------------------------------------------------------------------------
# 5. config/loader.py
# ---------------------------------------------------------------------------
from backend.config.loader import load_config, save_config
from backend.config.models import ForgeConfig


class TestLoadConfig:
    def test_load_none_path_returns_defaults(self) -> None:
        cfg = load_config(None)
        assert isinstance(cfg, ForgeConfig)

    def test_load_nonexistent_path_returns_defaults(self, tmp_path: Path) -> None:
        cfg = load_config(tmp_path / "missing.db")
        assert isinstance(cfg, ForgeConfig)

    def test_load_empty_db_returns_defaults(self, tmp_path: Path) -> None:
        db = tmp_path / "forge.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.commit()
        conn.close()

        cfg = load_config(db)
        assert isinstance(cfg, ForgeConfig)

    def test_load_valid_config(self, tmp_path: Path) -> None:
        db = tmp_path / "forge.db"
        data = ForgeConfig()
        data.project.name = "test-proj"
        json_str = json.dumps(data.model_dump(mode="json"))

        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("forge", json_str))
        conn.commit()
        conn.close()

        cfg = load_config(db)
        assert cfg.project.name == "test-proj"

    def test_load_corrupt_json_returns_defaults(self, tmp_path: Path) -> None:
        db = tmp_path / "forge.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)", ("forge", "not json"))
        conn.commit()
        conn.close()

        cfg = load_config(db)
        assert isinstance(cfg, ForgeConfig)


class TestSaveConfig:
    def test_save_creates_table_and_inserts(self, tmp_path: Path) -> None:
        db = tmp_path / "forge.db"
        config = ForgeConfig()
        config.project.name = "saved-proj"

        save_config(config, db)

        conn = sqlite3.connect(str(db))
        row = conn.execute("SELECT value FROM settings WHERE key='forge'").fetchone()
        conn.close()
        assert row is not None
        loaded = json.loads(row[0])
        assert loaded["project"]["name"] == "saved-proj"

    def test_save_upserts(self, tmp_path: Path) -> None:
        db = tmp_path / "forge.db"
        config1 = ForgeConfig()
        config1.project.name = "first"
        save_config(config1, db)

        config2 = ForgeConfig()
        config2.project.name = "second"
        save_config(config2, db)

        conn = sqlite3.connect(str(db))
        row = conn.execute("SELECT value FROM settings WHERE key='forge'").fetchone()
        count = conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0]
        conn.close()
        assert count == 1
        assert json.loads(row[0])["project"]["name"] == "second"

    def test_save_creates_parent_dirs(self, tmp_path: Path) -> None:
        db = tmp_path / "sub" / "dir" / "forge.db"
        save_config(ForgeConfig(), db)
        assert db.exists()

    def test_roundtrip(self, tmp_path: Path) -> None:
        db = tmp_path / "forge.db"
        original = ForgeConfig()
        original.project.name = "roundtrip"
        save_config(original, db)
        loaded = load_config(db)
        assert loaded.project.name == "roundtrip"


# ---------------------------------------------------------------------------
# 6. forge_logger.py
# ---------------------------------------------------------------------------
from backend.server.forge_logger import ForgeLogger


class TestForgeLoggerInitialise:
    def test_initialise_creates_file(self, tmp_path: Path) -> None:
        logger = ForgeLogger()
        log_path = tmp_path / "test.log"
        ws = MagicMock()

        logger.initialise(log_path, ws)
        assert logger._file_sink is not None
        assert logger._ws_sink is not None
        logger.close()

    def test_initialise_creates_parent_dirs(self, tmp_path: Path) -> None:
        logger = ForgeLogger()
        log_path = tmp_path / "sub" / "dir" / "test.log"
        logger.initialise(log_path, MagicMock())
        assert log_path.exists()
        logger.close()

    def test_close(self, tmp_path: Path) -> None:
        logger = ForgeLogger()
        logger.initialise(tmp_path / "test.log", MagicMock())
        logger.close()
        assert logger._file_sink is None
        assert logger._sqlite_sink is None
        assert logger._ws_sink is None


class TestForgeLoggerEmit:
    def test_emit_writes_to_file(self, tmp_path: Path) -> None:
        logger = ForgeLogger()
        log_path = tmp_path / "test.log"
        logger.initialise(log_path, None)

        logger.emit("INFO", "TEST", "hello")
        logger.close()

        content = log_path.read_text()
        assert "hello" in content
        assert "INFO" in content
        assert "TEST" in content

    def test_emit_with_detail(self, tmp_path: Path) -> None:
        logger = ForgeLogger()
        log_path = tmp_path / "test.log"
        logger.initialise(log_path, None)

        logger.emit("WARN", "CAT", "msg", detail="extra info")
        logger.close()

        content = log_path.read_text()
        assert "extra info" in content

    def test_emit_broadcasts_ws_event(self, tmp_path: Path) -> None:
        logger = ForgeLogger()
        ws = MagicMock()
        logger.initialise(tmp_path / "test.log", ws)

        logger.emit("INFO", "TEST", "broadcast msg")

        ws.broadcast_threadsafe.assert_called()
        event = ws.broadcast_threadsafe.call_args[0][0]
        assert event.payload["msg"] == "broadcast msg"
        assert event.payload["level"] == "INFO"
        assert event.payload["cat"] == "TEST"
        logger.close()

    def test_emit_no_file_no_ws(self) -> None:
        logger = ForgeLogger()
        # Should not raise even without initialisation
        logger.emit("INFO", "TEST", "orphan")

    def test_emit_stderr(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        logger = ForgeLogger()
        logger.initialise(tmp_path / "test.log", None)
        logger.enable_stderr()
        logger.emit("INFO", "TEST", "stderr msg")
        logger.disable_stderr()
        logger.close()
        # stderr output captured
        captured = capsys.readouterr()
        assert "stderr msg" in captured.err


class TestForgeLoggerBroadcast:
    """WS broadcast now flows through WSLogSink; these tests exercise that path."""

    def test_emit_broadcasts_to_ws_sink(self, tmp_path: Path) -> None:
        logger = ForgeLogger()
        ws = MagicMock()
        logger.initialise(tmp_path / "t.log", ws)
        ws.broadcast_threadsafe.reset_mock()
        logger.emit("INFO", "TEST", "msg", detail="extra")

        ws.broadcast_threadsafe.assert_called()
        event = ws.broadcast_threadsafe.call_args[0][0]
        assert event.payload["level"] == "INFO"
        assert event.payload["cat"] == "TEST"
        assert event.payload["msg"] == "msg"
        assert event.payload["detail"] == "extra"
        logger.close()

    def test_emit_broadcast_no_detail(self, tmp_path: Path) -> None:
        logger = ForgeLogger()
        ws = MagicMock()
        logger.initialise(tmp_path / "t.log", ws)
        ws.broadcast_threadsafe.reset_mock()
        logger.emit("WARN", "SYS", "warning")
        event = ws.broadcast_threadsafe.call_args[0][0]
        assert event.payload["detail"] is None
        logger.close()


class TestForgeLoggerConvenienceMethods:
    """Test the many convenience methods that delegate to emit."""

    @pytest.fixture(autouse=True)
    def _stub_emit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Replace ``emit`` with a recorder so delegation can be asserted."""
        self.logger = ForgeLogger()
        self.emit = MagicMock()
        monkeypatch.setattr(self.logger, "emit", self.emit)

    def test_llm_call(self) -> None:
        self.logger.llm_call("gpt-4", "agent1", 1000, 128000)
        self.emit.assert_called_once()
        args = self.emit.call_args[0]
        assert args[0] == "INFO"
        assert "gpt-4" in args[2]
        assert "ctx=" in args[2]

    def test_llm_call_no_context_window(self) -> None:
        self.logger.llm_call("gpt-4", "agent1", 1000, 0)
        msg = self.emit.call_args[0][2]
        assert "ctx=" not in msg

    def test_llm_response(self) -> None:
        self.logger.llm_response(
            "gpt-4",
            50,
            1200.0,
            "read_file",
            prompt_tokens=100,
            total_tokens=150,
            context_window=128000,
        )
        args = self.emit.call_args[0]
        assert "gpt-4" in args[2]
        assert "tool=read_file" in args[2]
        assert "ctx=" in args[2]

    def test_llm_response_no_tool(self) -> None:
        self.logger.llm_response("m", 10, 500.0, None)
        msg = self.emit.call_args[0][2]
        assert "tool=" not in msg

    def test_llm_error(self) -> None:
        self.logger.llm_error("m", "timeout")
        args = self.emit.call_args[0]
        assert args[0] == "ERROR"
        assert "timeout" in args[2]

    def test_llm_prompt(self) -> None:
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hello world"},
        ]
        self.logger.llm_prompt("m", msgs)
        msg = self.emit.call_args[0][2]
        assert "hello world" in msg

    def test_llm_prompt_no_user_message(self) -> None:
        self.logger.llm_prompt("m", [{"role": "system", "content": "sys"}])
        self.emit.assert_called_once()

    def test_llm_content_tool_calls(self) -> None:
        tc = [{"function": {"name": "read_file", "arguments": '{"path":"/a"}'}}]
        self.logger.llm_content("m", "text", tc)
        msg = self.emit.call_args[0][2]
        assert "read_file" in msg

    def test_llm_content_text_only(self) -> None:
        self.logger.llm_content("m", "some text", [])
        msg = self.emit.call_args[0][2]
        assert "text" in msg.lower()

    def test_llm_content_empty(self) -> None:
        self.logger.llm_content("m", "", [])
        args = self.emit.call_args[0]
        assert args[0] == "WARN"
        assert "empty" in args[2].lower()

    def test_crew_thought(self) -> None:
        self.logger.crew_thought("I should read the file")
        msg = self.emit.call_args[0][2]
        assert "thought" in msg

    def test_crew_tool_call(self) -> None:
        self.logger.crew_tool_call("write_file", '{"content": "hi"}')
        msg = self.emit.call_args[0][2]
        assert "write_file" in msg

    def test_crew_tool_result(self) -> None:
        self.logger.crew_tool_result("write_file", "Success")
        msg = self.emit.call_args[0][2]
        assert "write_file" in msg

    def test_crew_finish(self) -> None:
        self.logger.crew_finish("All done")
        msg = self.emit.call_args[0][2]
        assert "finish" in msg

    def test_tool_call(self) -> None:
        self.logger.tool_call("file_read", "Software Engineer")
        msg = self.emit.call_args[0][2]
        assert "file_read" in msg

    def test_tool_result_success(self) -> None:
        self.logger.tool_result("file_read", True, "200 bytes")
        args = self.emit.call_args[0]
        assert args[0] == "INFO"
        assert "ok" in args[2]

    def test_tool_result_failure(self) -> None:
        self.logger.tool_result("file_read", False, "not found")
        args = self.emit.call_args[0]
        assert args[0] == "WARN"
        assert "err" in args[2]

    def test_tool_result_no_snippet(self) -> None:
        self.logger.tool_result("file_read", True)
        msg = self.emit.call_args[0][2]
        assert "→" not in msg

    def test_user_action(self) -> None:
        self.logger.user_action("click", "button-A")
        msg = self.emit.call_args[0][2]
        assert "click" in msg
        assert "button-A" in msg

    def test_user_action_no_detail(self) -> None:
        self.logger.user_action("navigate")
        msg = self.emit.call_args[0][2]
        assert "navigate" in msg

    def test_loop_start(self) -> None:
        self.logger.loop_start()
        msg = self.emit.call_args[0][2]
        assert "started" in msg.lower()

    def test_loop_complete(self) -> None:
        self.logger.loop_complete()
        msg = self.emit.call_args[0][2]
        assert "complete" in msg.lower()

    def test_loop_cancelled(self) -> None:
        self.logger.loop_cancelled()
        msg = self.emit.call_args[0][2]
        assert "cancelled" in msg.lower()

    def test_loop_stop(self) -> None:
        self.logger.loop_stop()
        msg = self.emit.call_args[0][2]
        assert "stopped" in msg.lower()

    def test_loop_error(self) -> None:
        self.logger.loop_error("boom")
        args = self.emit.call_args[0]
        assert args[0] == "ERROR"
        assert "boom" in args[2]

    def test_phase_start(self) -> None:
        self.logger.phase_start(3)
        msg = self.emit.call_args[0][2]
        assert "3" in msg

    def test_phase_complete(self) -> None:
        self.logger.phase_complete(5)
        msg = self.emit.call_args[0][2]
        assert "5" in msg

    def test_phase_no_gaps(self) -> None:
        self.logger.phase_no_gaps(2, 1, 3)
        msg = self.emit.call_args[0][2]
        assert "2" in msg

    def test_gap_dispatch(self) -> None:
        self.logger.gap_dispatch("UNCOVERED_PARA", "node-1", 3)
        msg = self.emit.call_args[0][2]
        assert "UNCOVERED_PARA" in msg

    def test_gap_no_progress(self) -> None:
        self.logger.gap_no_progress("UNDESIGNED", "n1", 2)
        args = self.emit.call_args[0]
        assert args[0] == "WARN"

    def test_gap_resolved(self) -> None:
        self.logger.gap_resolved("UNDESIGNED", "n1")
        msg = self.emit.call_args[0][2]
        assert "Resolved" in msg

    def test_agent_dispatch(self) -> None:
        self.logger.agent_dispatch("SE", "UNDESIGNED", "n1")
        msg = self.emit.call_args[0][2]
        assert "SE" in msg

    def test_agent_done(self) -> None:
        self.logger.agent_done("SE", 1500.0)
        msg = self.emit.call_args[0][2]
        assert "1500" in msg

    def test_agent_error(self) -> None:
        self.logger.agent_error("SE", "crash")
        args = self.emit.call_args[0]
        assert args[0] == "ERROR"

    def test_no_agent_for_gap(self) -> None:
        self.logger.no_agent_for_gap("UNKNOWN")
        args = self.emit.call_args[0]
        assert args[0] == "WARN"


# ---------------------------------------------------------------------------
# Async helper
# ---------------------------------------------------------------------------


async def _async_iter(items: list[Any]) -> AsyncIterator[Any]:
    for item in items:
        yield item
