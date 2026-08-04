"""ForgeTool — base class for all FORGE agent tools.

Every tool invocation is instrumented uniformly here: the call runs
inside a ``log_context(tool_name=...)`` so nested log emissions inherit
the tool_name, and a structured record captures the duration + result
snippet + any exception. Subclasses don't need to emit logs themselves.
"""

from __future__ import annotations

import time
import traceback
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import ConfigDict

_ARG_SNIPPET_MAX = 200


class ToolPermissionError(Exception):
    """Raised when an agent attempts to use a tool it is not permitted to call."""


class ForgeTool(BaseTool):
    """
    Base class for all FORGE tools.

    Built on LangChain BaseTool so LangGraph ToolNode can invoke tools via
    JSON function calling. Every call goes through _execute so graph state
    changes are always written and detectable by the flow.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    # ``BaseTool`` declares name/description as required construction fields, so
    # a bare ``super().__init__()`` in a concrete tool is not statically
    # satisfiable even though every tool supplies them as class-level defaults.
    # Defaulting them here is what makes that legal. Six tool modules each
    # carried a private ``_DefaultedTool`` shim doing exactly this before it was
    # centralised; the values are always overridden by the concrete tool.
    name: str = ""
    description: str = ""

    def _run(self, *args: Any, **kwargs: Any) -> str:
        """Sync LangChain entry point — delegates to _execute.

        LangChain's default ``_arun`` runs this in a thread executor,
        which is essential: it frees the main event loop so that
        ``run_async()`` can schedule coroutines back onto it via
        ``run_coroutine_threadsafe``.

        Do NOT override ``_arun`` — that would run ``_execute`` directly
        on the event loop thread, deadlocking any tool that calls
        ``run_async`` for graph operations.
        """
        kwargs.pop("run_manager", None)
        from backend.observability import log_context  # noqa: PLC0415
        from backend.server.forge_logger import forge_logger  # noqa: PLC0415

        t0 = time.monotonic()
        args_snippet = _summarise_args(kwargs)
        with log_context(tool_name=self.name):
            forge_logger.emit(
                "INFO", "TOOL ",
                f"{self.name}(args={args_snippet})",
                tool_name=self.name,
                args_summary=args_snippet,
            )
            try:
                result = self._execute(**kwargs)
                duration_ms = int((time.monotonic() - t0) * 1000)
                success = not str(result).startswith("TOOL_ERROR")
                snippet = str(result).replace("\n", " ↵ ")
                forge_logger.emit(
                    "INFO" if success else "WARN",
                    "TOOL ",
                    f"{self.name} [{'ok' if success else 'err'}] "
                    f"{duration_ms}ms → {snippet[:160]}",
                    tool_name=self.name,
                    duration_ms=duration_ms,
                    tool_success=success,
                )
                return result
            except Exception as exc:  # noqa: BLE001
                duration_ms = int((time.monotonic() - t0) * 1000)
                err = f"TOOL_ERROR: {type(exc).__name__}: {exc}"
                forge_logger.emit(
                    "ERROR", "TOOL ",
                    f"{self.name} raised {type(exc).__name__}: {exc}",
                    tool_name=self.name,
                    duration_ms=duration_ms,
                    error_type=type(exc).__name__,
                    traceback=traceback.format_exc(limit=10),
                )
                return err

    def _execute(self, **kwargs: Any) -> str:
        """Implement tool logic; subclasses must override this method."""
        raise NotImplementedError


def _summarise_args(kwargs: dict[str, Any]) -> str:
    """Return a short queryable summary of tool args.

    Keeps keys; truncates individual values; elides large content bodies
    so the observability DB doesn't balloon on a single file_write call.
    """
    parts: list[str] = []
    for k, v in kwargs.items():
        s = str(v).replace("\n", " ↵ ")
        if len(s) > _ARG_SNIPPET_MAX:
            s = f"{s[:_ARG_SNIPPET_MAX - 3]}..."
        parts.append(f"{k}={s}")
    return ", ".join(parts)
