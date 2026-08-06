"""Durable full-body trace of every LLM call.

The logs DB (``log_sinks.py``) records per-call *metadata* (tokens,
duration, snippets); this module records the *complete* request and
response bodies, one JSON record per call, appended to a per-process JSONL
file. Records join to the logs DB via ``call_id``.

Location: ``<llm.trace_dir>/trace.<pid>.jsonl``. A relative ``trace_dir``
resolves against the **repo root** — the same anchoring rule as the
response cache (``backend/agents/llm_cache.py``) — because per-phase
integration tests chdir into throwaway workspaces and must still write to
the repo-level directory.

See ``design/25_observability.md`` §"LLM call trace".
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

#: Repo root, derived from this file's location
#: (backend/observability/llm_trace.py) — stable regardless of process cwd.
_REPO_ROOT = Path(__file__).resolve().parents[2]


def resolve_trace_path(trace_dir: str) -> Path:
    """Return the per-process trace file path for ``llm.trace_dir``.

    An absolute *trace_dir* is used as-is. A relative one resolves against
    the repo root (the directory containing ``backend/``), never the
    process cwd.
    """
    directory = Path(trace_dir)
    if not directory.is_absolute():
        directory = _REPO_ROOT / directory
    return directory / f"trace.{os.getpid()}.jsonl"


def serialize_messages(messages: list[Any]) -> list[dict[str, Any]]:
    """Convert LangChain messages into plain role/content dicts.

    Tool calls and tool_call_id are included when present so tool loops
    are fully reconstructable from the trace.
    """
    records: list[dict[str, Any]] = []
    for message in messages:
        role = (
            getattr(message, "type", None)
            or getattr(message, "role", None)
            or type(message).__name__
        )
        record: dict[str, Any] = {"role": role, "content": getattr(message, "content", None)}
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            record["tool_calls"] = tool_calls
        tool_call_id = getattr(message, "tool_call_id", None)
        if tool_call_id:
            record["tool_call_id"] = tool_call_id
        records.append(record)
    return records


def extract_chunk_text(chunk: Any) -> str:
    """Return the text contribution of one streamed chunk.

    Chunk content is either a plain string or a list of content blocks
    (Anthropic-style); only ``text`` blocks contribute.
    """
    content = getattr(chunk, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            str(block.get("text") or "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "".join(parts)
    return ""


class LLMTraceWriter:
    """Append-only JSONL writer for full LLM request/response records.

    Construction performs no I/O; the parent directory and file are
    created lazily on the first :meth:`record`. Each record is appended,
    flushed, and fsynced in one operation so a crash loses at most the
    in-flight call.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def path(self) -> Path:
        """Location of the JSONL trace file."""
        return self._path

    def record(
        self,
        *,
        call_id: str,
        model: str,
        temperature: float | None,
        messages: list[Any],
        tools: Any,
        response_text: str,
        tool_calls: list[Any],
        prompt_tokens: int,
        completion_tokens: int,
        tokens_estimated: bool,
        duration_ms: int,
        streamed: bool,
        error: str | None,
        context: dict[str, Any],
    ) -> None:
        """Append one complete call record to the trace file.

        ``tokens_estimated`` marks records whose token counts were
        tiktoken-estimated locally because the provider emitted no usage —
        analysis must never mistake an estimate for provider-reported truth.
        """
        payload = {
            "ts_ms": int(time.time() * 1000),
            "call_id": call_id,
            "model": model,
            "temperature": temperature,
            "streamed": streamed,
            "duration_ms": duration_ms,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "tokens_estimated": tokens_estimated,
            "error": error,
            "request": {"messages": serialize_messages(messages), "tools": tools},
            "response": {"text": response_text, "tool_calls": tool_calls},
            "context": context,
        }
        line = json.dumps(payload, ensure_ascii=False, default=repr)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
