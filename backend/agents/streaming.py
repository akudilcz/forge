"""Shared agent event streaming utilities.

Extracts the common astream_events parsing loop used by codegen agents
and the console agent so each call site focuses on domain logic.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage

from backend.server.forge_logger import forge_logger


@dataclass
class AgentTurn:
    """One LLM turn extracted from the event stream."""

    message: AIMessage | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    text_content: str = ""


async def iter_agent_turns(
    agent: Any,
    messages: list[Any],
    *,
    recursion_limit: int = 30,
    label: str = "",
    model: str = "",
    thread_id: str = "",
) -> AsyncIterator[AgentTurn]:
    """Stream agent events and yield structured ``AgentTurn`` objects.

    Handles the common boilerplate of filtering ``on_chat_model_end``
    events, unwrapping ``.message``, and logging tool calls.

    Args:
        agent: A compiled LangGraph agent.
        messages: Input messages list.
        recursion_limit: Max agent reasoning steps.
        label: Human-readable label for log lines.
        model: Model name for log attribution.
        thread_id: If set, enables LangGraph checkpointing so conversation
            state (including tool call / tool result pairs) is preserved
            across invocations.  Required for ``PersistentSliceAgent``.
    """
    config: dict[str, Any] = {"recursion_limit": recursion_limit}
    if thread_id:
        config["configurable"] = {"thread_id": thread_id}
    async for event in agent.astream_events(
        {"messages": messages},
        version="v2",
        config=config,
    ):
        if event.get("event") != "on_chat_model_end":
            continue

        if model:
            meta: dict[str, str] = {"model": model}
            # Extract usage info if available for context badge
            output = event.get("data", {}).get("output")
            usage = getattr(output, "usage_metadata", None) if output else None
            if usage:
                prompt_tokens = usage.get("input_tokens", 0)
                if prompt_tokens:
                    from backend.agents.llm_callback import _get_context_window
                    ctx_window = _get_context_window()
                    meta["prompt_tokens"] = str(prompt_tokens)
                    meta["context_window"] = str(ctx_window)
            forge_logger.emit("INFO", "AGNT", f"LLM → {label}", **meta)

        msg = event.get("data", {}).get("output")
        if msg is None:
            continue
        if hasattr(msg, "message"):
            msg = msg.message

        turn = AgentTurn()
        if isinstance(msg, AIMessage):
            turn.message = msg

        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            for tc in tool_calls:
                name = tc.get("name", "") if isinstance(tc, dict) else getattr(tc, "name", "")
                args = tc.get("args", {}) if isinstance(tc, dict) else getattr(tc, "args", {})
                turn.tool_calls.append({"name": str(name), "args": args})
                forge_logger.crew_tool_call(str(name), str(args))
        else:
            turn.text_content = getattr(msg, "content", "") or ""
            if turn.text_content:
                snippet = turn.text_content[:200].replace("\n", " ↵ ")
                forge_logger.emit(
                    "INFO", "AGNT",
                    f"[{label}] {snippet}",
                )

        yield turn
