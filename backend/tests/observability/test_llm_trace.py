"""Tests for the full LLM call trace (backend/observability/llm_trace.py).

Covers the writer itself (repo-root path anchoring, append-only JSONL) and
its wiring into ThrottledChatOpenAI._agenerate / ._astream, mirroring the
superclass-mocking approach of backend/tests/agents/test_agents_factory.py.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from backend.agents import factory as factory_mod
from backend.agents.factory import ThrottledChatOpenAI
from backend.agents.throttle import llm_throttle
from backend.observability import log_context
from backend.observability.llm_trace import LLMTraceWriter, resolve_trace_path

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def _llm_counters() -> Iterator[None]:
    """Save and restore the global LLM call counter and limit."""
    saved_count = factory_mod.llm_call_count
    saved_limit = factory_mod.llm_call_limit
    factory_mod.llm_call_limit = None
    yield
    factory_mod.llm_call_count = saved_count
    factory_mod.llm_call_limit = saved_limit


def _human_message(content: str) -> SimpleNamespace:
    return SimpleNamespace(content=content, type="human", role="user")


def _make_llm(writer: LLMTraceWriter | None) -> ThrottledChatOpenAI:
    return ThrottledChatOpenAI(
        model="test-model",
        api_key=SecretStr("test-key"),
        base_url="http://localhost:1/v1",
        temperature=0.3,
        trace_writer=writer,
    )


def _read_records(path: Path) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


# ── resolve_trace_path ───────────────────────────────────────────────────────


def test_resolve_trace_path_relative_anchors_to_repo_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative trace_dir resolves against the repo root even after chdir."""
    monkeypatch.chdir(tmp_path)
    path = resolve_trace_path(".forge/llm_trace")
    assert path == REPO_ROOT / ".forge" / "llm_trace" / f"trace.{os.getpid()}.jsonl"


def test_resolve_trace_path_absolute_used_as_is(tmp_path: Path) -> None:
    """An absolute trace_dir is used unchanged."""
    path = resolve_trace_path(str(tmp_path / "traces"))
    assert path == tmp_path / "traces" / f"trace.{os.getpid()}.jsonl"


# ── LLMTraceWriter ───────────────────────────────────────────────────────────


def test_writer_appends_one_json_line_per_record(tmp_path: Path) -> None:
    """Each record() call appends exactly one parseable JSON line."""
    writer = LLMTraceWriter(tmp_path / "sub" / "trace.jsonl")
    for text in ("first", "second"):
        writer.record(
            call_id="call-abc",
            model="m",
            temperature=0.1,
            messages=[_human_message("hi")],
            tools=None,
            response_text=text,
            tool_calls=[],
            prompt_tokens=1,
            completion_tokens=2,
            duration_ms=5,
            streamed=False,
            error=None,
            context={"phase": 3},
        )
    records = _read_records(tmp_path / "sub" / "trace.jsonl")
    assert [r["response"]["text"] for r in records] == ["first", "second"]
    assert records[0]["request"]["messages"] == [{"role": "human", "content": "hi"}]
    assert records[0]["context"] == {"phase": 3}
    assert records[0]["ts_ms"] > 0


def test_writer_construction_performs_no_io(tmp_path: Path) -> None:
    """Constructing a writer must not create the directory or file."""
    target = tmp_path / "lazy" / "trace.jsonl"
    LLMTraceWriter(target)
    assert not target.parent.exists()


# ── _agenerate wiring ────────────────────────────────────────────────────────


async def test_agenerate_writes_complete_trace_record(
    tmp_path: Path, _llm_counters: None
) -> None:
    """A successful _agenerate call writes a full request/response record."""
    message = SimpleNamespace(
        content="hello world",
        tool_calls=[{"name": "graph_read", "args": {"q": 1}, "id": "t1"}],
        usage_metadata={"input_tokens": 3, "output_tokens": 2},
        response_metadata={},
    )
    result = SimpleNamespace(generations=[SimpleNamespace(message=message)])
    trace_file = tmp_path / "trace.jsonl"

    with (
        patch("backend.agents.factory.forge_logger"),
        patch("langchain_openai.ChatOpenAI._agenerate", AsyncMock(return_value=result)),
        patch.object(llm_throttle, "wait", AsyncMock()),
    ):
        llm = _make_llm(LLMTraceWriter(trace_file))
        with log_context(phase=3, gap_type="UNCOVERED_PARA", gap_id="PARA-1", agent_id="agent-x"):
            out = await llm._agenerate(
                [_human_message("go")], tools=[{"type": "function", "function": {"name": "f"}}]
            )

    assert out is result
    (record,) = _read_records(trace_file)
    assert record["call_id"].startswith("call-")
    assert record["model"] == "test-model"
    assert record["temperature"] == 0.3
    assert record["streamed"] is False
    assert record["error"] is None
    assert record["prompt_tokens"] == 3
    assert record["completion_tokens"] == 2
    assert record["duration_ms"] >= 0
    assert record["request"]["messages"] == [{"role": "human", "content": "go"}]
    assert record["request"]["tools"] == [{"type": "function", "function": {"name": "f"}}]
    assert record["response"]["text"] == "hello world"
    assert record["response"]["tool_calls"] == [{"name": "graph_read", "args": {"q": 1}, "id": "t1"}]
    assert record["context"]["phase"] == 3
    assert record["context"]["gap_type"] == "UNCOVERED_PARA"
    assert record["context"]["gap_id"] == "PARA-1"
    assert record["context"]["agent_id"] == "agent-x"


async def test_agenerate_error_writes_error_record(
    tmp_path: Path, _llm_counters: None
) -> None:
    """A failed _agenerate call still writes a record carrying the error."""
    trace_file = tmp_path / "trace.jsonl"

    with (
        patch("backend.agents.factory.forge_logger"),
        patch(
            "langchain_openai.ChatOpenAI._agenerate",
            AsyncMock(side_effect=RuntimeError("api down")),
        ),
        patch.object(llm_throttle, "wait", AsyncMock()),
    ):
        llm = _make_llm(LLMTraceWriter(trace_file))
        with pytest.raises(RuntimeError, match="api down"):
            await llm._agenerate([_human_message("go")])

    (record,) = _read_records(trace_file)
    assert record["error"] == "RuntimeError: api down"
    assert record["streamed"] is False
    assert record["response"]["text"] == ""
    assert record["request"]["messages"] == [{"role": "human", "content": "go"}]


# ── _astream wiring ──────────────────────────────────────────────────────────


async def test_astream_writes_assembled_text_record(
    tmp_path: Path, _llm_counters: None
) -> None:
    """Streaming chunks are assembled into the final response text."""
    chunk1 = SimpleNamespace(
        usage_metadata={"input_tokens": 10, "output_tokens": 0},
        content="hel",
        tool_calls=[],
        response_metadata={},
    )
    chunk2 = SimpleNamespace(
        usage_metadata={"input_tokens": 10, "output_tokens": 7},
        content=[{"type": "text", "text": "lo"}],
        tool_calls=[{"name": "file_read"}],
        response_metadata={},
    )

    async def fake_stream(self: Any, *args: Any, **kwargs: Any) -> Any:
        yield chunk1
        yield chunk2

    trace_file = tmp_path / "trace.jsonl"
    with (
        patch("backend.agents.factory.forge_logger"),
        patch("langchain_openai.ChatOpenAI._astream", fake_stream),
        patch.object(llm_throttle, "wait", AsyncMock()),
    ):
        llm = _make_llm(LLMTraceWriter(trace_file))
        out = [c async for c in llm._astream([_human_message("go")])]

    assert out == [chunk1, chunk2]
    (record,) = _read_records(trace_file)
    assert record["streamed"] is True
    assert record["error"] is None
    assert record["response"]["text"] == "hello"
    assert record["response"]["tool_calls"] == [{"name": "file_read"}]
    assert record["prompt_tokens"] == 10
    assert record["completion_tokens"] == 7


async def test_astream_error_writes_error_record(
    tmp_path: Path, _llm_counters: None
) -> None:
    """A streaming failure writes an error record before re-raising."""

    async def fake_stream(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise ValueError("boom")
        yield  # pragma: no cover

    trace_file = tmp_path / "trace.jsonl"
    with (
        patch("backend.agents.factory.forge_logger"),
        patch("langchain_openai.ChatOpenAI._astream", fake_stream),
        patch.object(llm_throttle, "wait", AsyncMock()),
    ):
        llm = _make_llm(LLMTraceWriter(trace_file))
        with pytest.raises(ValueError, match="boom"):
            _ = [c async for c in llm._astream([_human_message("go")])]

    (record,) = _read_records(trace_file)
    assert record["error"] == "ValueError: boom"
    assert record["streamed"] is True
    assert record["response"]["text"] == ""


# ── disabled path ────────────────────────────────────────────────────────────


async def test_trace_disabled_writes_no_file(
    tmp_path: Path, _llm_counters: None
) -> None:
    """trace_writer=None means no trace file is created anywhere."""
    result = SimpleNamespace(generations=[])
    with (
        patch("backend.agents.factory.forge_logger"),
        patch("langchain_openai.ChatOpenAI._agenerate", AsyncMock(return_value=result)),
        patch.object(llm_throttle, "wait", AsyncMock()),
    ):
        llm = _make_llm(None)
        await llm._agenerate([_human_message("go")])

    assert list(tmp_path.iterdir()) == []


# ── build_llm plumbing ───────────────────────────────────────────────────────


def _mock_config(*, trace_enabled: bool) -> MagicMock:
    config = MagicMock()
    config.llm.base_url = "http://localhost:11434/v1"
    config.llm.keyless = True
    config.llm.api_key_env = ""
    config.llm.request_timeout = 120
    config.llm.options.temperature = 0.8
    config.llm.cache_enabled = False
    config.llm.trace_enabled = trace_enabled
    config.llm.trace_dir = ".forge/llm_trace"
    config.llm.agents = {"Quality Auditor": "qa-model"}
    return config


def test_build_llm_passes_trace_writer_when_enabled() -> None:
    """llm.trace_enabled=True plumbs a repo-root-anchored writer into the LLM."""
    with patch("backend.agents.factory.ThrottledChatOpenAI") as mock_llm:
        factory_mod.build_llm(_mock_config(trace_enabled=True), cacheable=False)
    writer = mock_llm.call_args[1]["trace_writer"]
    assert isinstance(writer, LLMTraceWriter)
    assert writer.path == REPO_ROOT / ".forge" / "llm_trace" / f"trace.{os.getpid()}.jsonl"


def test_build_llm_passes_none_when_disabled() -> None:
    """llm.trace_enabled=False plumbs trace_writer=None (tracing off)."""
    with patch("backend.agents.factory.ThrottledChatOpenAI") as mock_llm:
        factory_mod.build_llm(_mock_config(trace_enabled=False), cacheable=False)
    assert mock_llm.call_args[1]["trace_writer"] is None
