"""Console router — natural-language ad-hoc agent endpoint with conversation history."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from langchain_core.messages import BaseMessage
from langgraph.errors import GraphRecursionError
from pydantic import BaseModel

from backend.agents.definitions import AgentRole
from backend.agents.pool import AgentPool
from backend.config.models import ForgeConfig
from backend.console.history import console_history
from backend.server.dependencies import get_agent_pool, get_broadcaster
from backend.server.forge_logger import forge_logger
from backend.server.websocket.broadcaster import EventBroadcaster

router = APIRouter(prefix="/console", tags=["console"])

_CONSOLE_ROLE = AgentRole.CONSOLE.value


class ConsoleRequest(BaseModel):
    """Request body for the /console/run endpoint; wraps the natural-language instruction."""

    request: str


@router.post("/run")
async def run_console(
    body: ConsoleRequest,
    request: Request,
    pool: AgentPool | None = Depends(get_agent_pool),
    broadcaster: EventBroadcaster | None = Depends(get_broadcaster),
) -> dict[str, str]:
    """Accept a natural-language request and run the Console agent with history."""
    text = body.request.strip()
    if not text:
        raise HTTPException(status_code=422, detail="request cannot be blank")
    if pool is None:
        raise HTTPException(status_code=503, detail="Agent pool not ready")

    # Update context window from config if available
    _sync_context_window(request)

    console_model = _get_console_model(request)
    request_id = str(uuid.uuid4())
    task = asyncio.create_task(
        _run_console_agent(text, request_id, pool, broadcaster, console_model)
    )
    request.app.state.console_task = task
    return {"status": "started", "request_id": request_id}


@router.post("/clear")
async def clear_console() -> dict[str, str]:
    """Clear the console conversation history."""
    console_history.clear()
    forge_logger.emit("INFO", "CONS ", "Conversation history cleared")
    return {"status": "cleared"}


@router.get("/history")
async def get_history() -> dict[str, Any]:
    """Return conversation metadata (not full content — just counts)."""
    return {
        "message_count": console_history.message_count,
        "context_window": console_history.context_window,
    }


def _sync_context_window(request: Request) -> None:
    """Update the console history's context window from the current config."""
    config: ForgeConfig | None = getattr(request.app.state, "config", None)
    if config is None:
        return
    console_model = config.llm.agents.get(AgentRole.CONSOLE.value, "")
    ctx_window = config.llm.context_window_for_model(console_model)
    console_history.set_context_window(ctx_window)


async def _run_console_agent(
    text: str,
    request_id: str,
    pool: AgentPool,
    broadcaster: EventBroadcaster | None,
    console_model: str = "",
) -> None:
    """Run the Console agent in the background with conversation history."""
    short_id = request_id[:8]
    forge_logger.emit("INFO", "CONS ", f"[{short_id}] {text}")

    # Add user message to history
    console_history.add_user_message(text)

    try:
        agent = pool.get(_CONSOLE_ROLE)
        history = console_history.get_messages_for_agent()
        response = await _stream_agent(agent, history, console_model)

        # Add the agent's response to history for future context
        if response:
            console_history.add_ai_message(response)

        forge_logger.emit("INFO", "CONS ", f"[{short_id}] done")
    except Exception as exc:  # noqa: BLE001
        import traceback

        forge_logger.emit("ERROR", "CONS ", f"[{short_id}] error: {exc}")
        forge_logger.emit("ERROR", "CONS ", f"[{short_id}] traceback:\n{traceback.format_exc()}")


def _get_console_model(request: Request) -> str:
    """Get the configured model name for the console agent from live config."""
    config: ForgeConfig | None = getattr(request.app.state, "config", None)
    if config is None:
        return ""
    return config.llm.agents.get(AgentRole.CONSOLE.value, "")


_CONSOLE_STEP_LIMIT = 250
_MAX_RETRIES = 3
_RETRY_BASE_SECS = 2


def _is_transient(exc: Exception) -> bool:
    """Return True if exc is a transient API/network error worth retrying."""
    try:
        import openai
        if isinstance(exc, openai.APIError):
            code = getattr(exc, "status_code", None)
            return code is None or code >= 500
    except ImportError:
        pass
    return isinstance(exc, (ConnectionError, TimeoutError, OSError))


async def _stream_agent(
    agent: Any, messages: list[BaseMessage], console_model: str = "",
) -> str:
    """Stream agent events with transient-error retries."""
    from backend.agents.streaming import iter_agent_turns

    for attempt in range(_MAX_RETRIES):
        raw = ""
        try:
            async for turn in iter_agent_turns(
                agent, messages,
                recursion_limit=_CONSOLE_STEP_LIMIT,
                label="console", model=console_model,
            ):
                if not turn.tool_calls:
                    raw = turn.text_content
            forge_logger.crew_finish(raw)
            return raw
        except GraphRecursionError:
            forge_logger.emit(
                "WARN", "CONS ", "Step limit reached — send a follow-up to continue."
            )
            forge_logger.crew_finish("Step limit reached.")
            return "Step limit reached. Send a follow-up instruction to continue."
        except Exception as exc:  # noqa: BLE001
            if _is_transient(exc) and attempt < _MAX_RETRIES - 1:
                wait = _RETRY_BASE_SECS ** attempt
                forge_logger.emit(
                    "WARN", "CONS ",
                    f"Transient API error, retrying in {wait}s… ({exc})",
                )
                await asyncio.sleep(wait)
            else:
                raise
    return ""
