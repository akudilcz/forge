"""Unit-test network guard for build_llm (defence in depth).

Unit suites used to make hundreds of real 401 HTTP calls per run because
tests carried default config (GPT-OSS-120B-CS → api.poe.com) into
``build_llm`` without stubbing the LLM seam. The root test conftest sets
``FORGE_UNIT_LLM_GUARD=1`` for the whole unit session; with it set,
``build_llm`` must refuse — loudly — to construct a client aimed at a
default routable provider endpoint. Integration tests clear the sentinel
in their own conftest and are exempt.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from backend.agents.factory import UNIT_LLM_GUARD_ENV, build_llm
from backend.config.models import ForgeConfig


def test_conftest_sets_the_guard_for_unit_sessions() -> None:
    """The root conftest arms the guard for every unit test."""
    assert os.environ.get(UNIT_LLM_GUARD_ENV) == "1"


def test_default_config_llm_construction_fails_loudly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unstubbed default-config build_llm raises with a clear message —
    it would otherwise really dial api.poe.com."""
    monkeypatch.setenv(UNIT_LLM_GUARD_ENV, "1")
    monkeypatch.setenv("POE_API_KEY", "any-key")
    config = ForgeConfig()  # default: base_url https://api.poe.com/v1

    with pytest.raises(RuntimeError, match="stub the LLM seam"):
        build_llm(config, cacheable=True)


def test_guard_triggers_even_for_keyless_default_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """keyless=True does not stop a real dial-out to a routable default —
    that was exactly the ~430-call 401 pattern."""
    monkeypatch.setenv(UNIT_LLM_GUARD_ENV, "1")
    config = ForgeConfig()
    config.llm.keyless = True

    with pytest.raises(RuntimeError, match="network guard"):
        build_llm(config, cacheable=True)


def test_openrouter_default_endpoint_is_also_guarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(UNIT_LLM_GUARD_ENV, "1")
    config = ForgeConfig()
    config.llm.base_url = "https://openrouter.ai/api/v1"
    config.llm.keyless = True

    with pytest.raises(RuntimeError, match="network guard"):
        build_llm(config, cacheable=True)


def test_non_routable_base_url_constructs_normally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A config explicitly pointed at a local endpoint is not a forgotten
    stub — construction proceeds."""
    monkeypatch.setenv(UNIT_LLM_GUARD_ENV, "1")
    config = ForgeConfig()
    config.llm.base_url = "http://localhost:11434/v1"
    config.llm.keyless = True
    config.llm.cache_enabled = False
    config.llm.trace_enabled = False

    with patch("backend.agents.factory.ThrottledChatOpenAI") as mock_llm:
        build_llm(config, cacheable=True)
        assert mock_llm.call_args[1]["base_url"] == "http://localhost:11434/v1"


def test_guard_cleared_allows_default_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the sentinel (the integration-test arrangement) the default
    endpoint constructs normally — no silent skip in either direction."""
    monkeypatch.delenv(UNIT_LLM_GUARD_ENV, raising=False)
    monkeypatch.setenv("POE_API_KEY", "integration-key")
    config = ForgeConfig()
    config.llm.cache_enabled = False
    config.llm.trace_enabled = False

    with patch("backend.agents.factory.ThrottledChatOpenAI") as mock_llm:
        build_llm(config, cacheable=True)
        assert mock_llm.call_args[1]["base_url"] == "https://api.poe.com/v1"
