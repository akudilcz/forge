"""Centralised phase-scoped agent context manager.

Owns the shared ``MemorySaver`` checkpointer and generates phase-scoped
``thread_id`` values so that agents accumulate conversation history within
a phase and start fresh at each phase boundary.

When accumulated prompt size approaches the context window limit, oldest
messages are trimmed via a ``pre_model_hook`` on the LangGraph react
agent.  The hook runs before every LLM call, keeping the most recent
turns and shedding early history.  No summarisation, no checkpoint
surgery — just a FIFO trim on the message list.

Usage::

    from backend.crew.phase_context import phase_context

    phase_context.reset_phase(phase)
    hook = phase_context.make_trim_hook(context_window)
    thread_id = phase_context.get_thread_id(phase, gap.type)
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import trim_messages
from langgraph.checkpoint.memory import MemorySaver

from backend.analysis.gaps import GapType

logger = logging.getLogger(__name__)

# Reserve 30% for system prompt + agent working space + tool responses
_CONTEXT_RESERVE_RATIO = 0.30


def make_trim_hook(context_window: int) -> Any:
    """Return a ``pre_model_hook`` that trims oldest messages when over budget.

    The hook reads the accumulated messages from agent state, applies
    ``trim_messages`` to keep the most recent turns within the token
    budget, and overwrites the messages key.  System messages are always
    preserved and the trimmed list always starts on a human message.
    """
    budget = int(context_window * (1 - _CONTEXT_RESERVE_RATIO))

    def _trim(state: dict[str, Any]) -> dict[str, Any]:
        messages = state.get("messages", [])
        if not messages:
            return {"messages": messages}
        trimmed = trim_messages(
            messages,
            max_tokens=budget,
            token_counter="approximate",
            strategy="last",
            include_system=True,
            start_on="human",
        )
        removed = len(messages) - len(trimmed)
        if removed > 0:
            logger.info(
                "phase_context.trim removed=%d remaining=%d budget=%d",
                removed,
                len(trimmed),
                budget,
            )
        return {"messages": trimmed}

    return _trim


class PhaseContext:
    """Manage agent conversation context scoped to phase boundaries."""

    def __init__(self) -> None:
        self._checkpointer = MemorySaver()
        self._nonce: int = 0

    def get_checkpointer(self) -> MemorySaver:
        """Return the shared checkpointer for agent compilation."""
        return self._checkpointer

    def get_thread_id(self, phase: int, gap_type: GapType) -> str:
        """Return a deterministic thread ID for this phase + gap type."""
        return f"phase-{phase}-{gap_type.value}-{self._nonce}"

    def reset_phase(self, phase: int) -> None:  # noqa: ARG002
        """Invalidate all thread IDs by incrementing the nonce."""
        self._nonce += 1

    def reset_all(self) -> None:
        """Full reset: new checkpointer + new nonce (start of a run)."""
        self._checkpointer = MemorySaver()
        self._nonce += 1


#: Module-level singleton — single source of truth for phase context.
phase_context = PhaseContext()
