"""ForgeLLMCallback — LiteLLM CustomLogger that feeds ForgeLogger.

Captures every LLM API call with full prompt/response detail so the
diagnostic log shows exactly what the model was sent and what it returned.

Inherits from ``litellm.integrations.custom_logger.CustomLogger`` — required
for LiteLLM's callback dispatch to call the sync event methods.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

# Inherit from CustomLogger so LiteLLM's dispatch code recognises this class.
# Fall back to plain object if litellm is not installed (tests / dev without it).
try:
    from litellm.integrations.custom_logger import CustomLogger as _Base
except ImportError:  # pragma: no cover
    _Base = object  # type: ignore[assignment,misc]


# Module-level context window — set by server startup or console router.
_context_window: int = 128_000


def set_context_window(tokens: int) -> None:
    """Update the context window size. Called when config changes."""
    global _context_window  # noqa: PLW0603
    _context_window = max(tokens, 4096)


def _get_context_window() -> int:
    """Return the configured context window size (tokens)."""
    return _context_window


class ForgeLLMCallback(_Base):
    """Diagnostic callback registered in ``litellm.callbacks``."""

    def log_pre_api_call(
        self, model: str, messages: list[dict[str, Any]], kwargs: dict[str, Any]
    ) -> None:
        """Log the outgoing prompt and estimated token count before the API call."""
        from backend.server.forge_logger import forge_logger

        # Estimate prompt tokens (rough: 1 token ≈ 4 chars)
        prompt_chars = sum(len(m.get("content") or "") for m in messages)
        prompt_tokens = max(1, prompt_chars // 4)

        meta = kwargs.get("metadata") or {}
        agent_hint = str(meta.get("task_description") or meta.get("agent") or "?")[:40]

        ctx_window = _get_context_window()
        forge_logger.llm_call(model, agent_hint, prompt_tokens, ctx_window)
        forge_logger.llm_prompt(model, messages)

    def log_success_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        """Log the LLM response, completion token count, latency, and any tool calls."""
        from backend.server.forge_logger import forge_logger

        model = kwargs.get("model") or "?"
        elapsed_ms = (end_time - start_time).total_seconds() * 1000

        usage = getattr(response_obj, "usage", None)
        prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        total_tokens = prompt_tokens + completion_tokens

        # Extract content and tool calls from the first choice
        content = ""
        raw_tool_calls: list[dict[str, Any]] = []
        choices = getattr(response_obj, "choices", []) or []
        if choices:
            msg = getattr(choices[0], "message", None)
            content = getattr(msg, "content", "") or ""
            tc_objs = getattr(msg, "tool_calls", None) or []
            for tc in tc_objs:
                fn = getattr(tc, "function", None)
                raw_tool_calls.append({
                    "function": {
                        "name": getattr(fn, "name", "?") if fn else "?",
                        "arguments": getattr(fn, "arguments", "") if fn else "",
                    }
                })

        # Summary line with context window usage
        first_tool = raw_tool_calls[0]["function"]["name"] if raw_tool_calls else None
        ctx_window = _get_context_window()
        forge_logger.llm_response(
            model, completion_tokens, elapsed_ms, first_tool,
            prompt_tokens=prompt_tokens, total_tokens=total_tokens,
            context_window=ctx_window,
        )
        # Detailed content / tool calls
        forge_logger.llm_content(model, content, raw_tool_calls)

    def log_failure_event(
        self,
        kwargs: dict[str, Any],
        response_obj: Any,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        """Log an LLM API error to ForgeLogger."""
        from backend.server.forge_logger import forge_logger

        model = kwargs.get("model") or "?"
        forge_logger.llm_error(model, str(response_obj))
