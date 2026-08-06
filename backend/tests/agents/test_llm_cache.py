"""Tests for backend/agents/llm_cache.py — SQLite-backed LLM response cache.

Behavioural coverage per specs/12-artifact-model-and-traceability.md §7.4:
- cache DB file (and its parent directory) created on first use;
- a second identical ``.ainvoke`` is served from cache (no second model call);
- distinct prompts are never cross-served;
- ``clear()`` empties the cache.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from backend.agents.llm_cache import SQLiteLLMCache


class CountingChatModel(BaseChatModel):
    """Deterministic fake chat model that counts real generations."""

    calls: int = 0

    def _generate(
        self,
        messages: list[Any],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        self.calls += 1
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content=f"reply-{self.calls}"))]
        )

    @property
    def _llm_type(self) -> str:
        return "counting-fake"


def _make_generations(text: str) -> list[ChatGeneration]:
    return [ChatGeneration(message=AIMessage(content=text))]


class TestSQLiteLLMCacheDirect:
    def test_lookup_miss_returns_none(self, tmp_path: Path) -> None:
        cache = SQLiteLLMCache(tmp_path / "sub" / "llm_cache.db")
        assert cache.lookup("prompt", "llm-string") is None

    def test_update_then_lookup_round_trip(self, tmp_path: Path) -> None:
        cache = SQLiteLLMCache(tmp_path / "llm_cache.db")
        cache.update("prompt", "llm-string", _make_generations("cached-answer"))

        result = cache.lookup("prompt", "llm-string")

        assert result is not None
        first = result[0]
        assert isinstance(first, ChatGeneration)
        assert first.message.content == "cached-answer"

    def test_directory_and_db_file_created_on_first_use(self, tmp_path: Path) -> None:
        db_path = tmp_path / ".cache" / "llm_cache.db"
        assert not db_path.parent.exists()

        cache = SQLiteLLMCache(db_path)
        cache.update("p", "l", _make_generations("x"))

        assert db_path.parent.is_dir()
        assert db_path.is_file()

    def test_distinct_llm_strings_do_not_collide(self, tmp_path: Path) -> None:
        cache = SQLiteLLMCache(tmp_path / "llm_cache.db")
        cache.update("prompt", "model-a", _make_generations("a"))

        assert cache.lookup("prompt", "model-b") is None

    def test_clear_empties_cache(self, tmp_path: Path) -> None:
        cache = SQLiteLLMCache(tmp_path / "llm_cache.db")
        cache.update("prompt", "llm-string", _make_generations("x"))

        cache.clear()

        assert cache.lookup("prompt", "llm-string") is None


class TestCacheServesAinvoke:
    @pytest.mark.asyncio
    async def test_second_identical_ainvoke_served_from_cache(self, tmp_path: Path) -> None:
        cache = SQLiteLLMCache(tmp_path / "llm_cache.db")
        model = CountingChatModel(cache=cache)

        first = await model.ainvoke("same prompt")
        second = await model.ainvoke("same prompt")

        assert model.calls == 1
        assert first.content == "reply-1"
        assert second.content == "reply-1"

    @pytest.mark.asyncio
    async def test_distinct_prompts_each_hit_model(self, tmp_path: Path) -> None:
        cache = SQLiteLLMCache(tmp_path / "llm_cache.db")
        model = CountingChatModel(cache=cache)

        await model.ainvoke("prompt one")
        await model.ainvoke("prompt two")

        assert model.calls == 2

    @pytest.mark.asyncio
    async def test_no_cache_means_every_call_hits_model(self) -> None:
        model = CountingChatModel(cache=False)

        await model.ainvoke("same prompt")
        await model.ainvoke("same prompt")

        assert model.calls == 2

    @pytest.mark.asyncio
    async def test_cache_persists_across_instances(self, tmp_path: Path) -> None:
        db_path = tmp_path / "llm_cache.db"
        model_a = CountingChatModel(cache=SQLiteLLMCache(db_path))
        await model_a.ainvoke("same prompt")

        model_b = CountingChatModel(cache=SQLiteLLMCache(db_path))
        result = await model_b.ainvoke("same prompt")

        assert model_b.calls == 0
        assert result.content == "reply-1"


class TestResolveCacheDbPath:
    """A relative ``llm.cache_dir`` resolves against the repo root, never the
    process cwd — integration tests chdir into throwaway workspaces and must
    still share the warm repo-level cache (design §7.4)."""

    def test_relative_dir_resolves_under_repo_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from backend.agents.llm_cache import resolve_cache_db_path

        monkeypatch.chdir(tmp_path)  # throwaway cwd must not matter
        resolved = resolve_cache_db_path(".cache")

        import backend
        repo_root = Path(backend.__file__).resolve().parent.parent
        assert resolved == repo_root / ".cache" / "llm_cache.db"
        assert resolved.is_absolute()

    def test_absolute_dir_used_as_is(self, tmp_path: Path) -> None:
        from backend.agents.llm_cache import resolve_cache_db_path

        resolved = resolve_cache_db_path(str(tmp_path / "custom"))

        assert resolved == tmp_path / "custom" / "llm_cache.db"


class TestCacheKeyIncludesModelParams:
    """The cache key is ``(prompt, llm_string)``; langchain-core's
    ``llm_string`` serializes the model configuration, so a change to any
    generation-affecting parameter (model name, temperature, base_url)
    yields a distinct key. Pinned empirically against the real model class
    used by build_llm — two calls with the same prompt but different model
    settings must never share a cache entry (design §7.4)."""

    @staticmethod
    def _model(**overrides: Any) -> Any:
        from backend.agents.factory import ThrottledChatOpenAI

        params: dict[str, Any] = {
            "model": "model-a",
            "base_url": "http://host-a/v1",
            "api_key": "test-key",
            "temperature": 0.5,
        }
        params.update(overrides)
        return ThrottledChatOpenAI(**params)

    def test_llm_string_distinct_per_generation_param(self) -> None:
        ref = self._model()._get_llm_string(stop=None)

        assert self._model()._get_llm_string(stop=None) == ref
        assert self._model(model="model-b")._get_llm_string(stop=None) != ref
        assert self._model(temperature=0.9)._get_llm_string(stop=None) != ref
        assert self._model(base_url="http://host-b/v1")._get_llm_string(stop=None) != ref

    def test_same_prompt_different_temperature_never_cross_served(
        self, tmp_path: Path
    ) -> None:
        import sqlite3

        db_path = tmp_path / "llm_cache.db"
        cache = SQLiteLLMCache(db_path)
        key_a = self._model(temperature=0.1)._get_llm_string(stop=None)
        key_b = self._model(temperature=0.9)._get_llm_string(stop=None)

        cache.update("same prompt", key_a, _make_generations("answer-at-0.1"))

        # Temperature B must miss — never replay temperature A's response.
        assert cache.lookup("same prompt", key_b) is None

        cache.update("same prompt", key_b, _make_generations("answer-at-0.9"))
        with sqlite3.connect(db_path) as conn:
            row_count = conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]
        assert row_count == 2
        hit = cache.lookup("same prompt", key_a)
        assert hit is not None
        first = hit[0]
        assert isinstance(first, ChatGeneration)
        assert first.message.content == "answer-at-0.1"
