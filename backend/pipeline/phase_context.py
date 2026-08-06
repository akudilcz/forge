"""Centralised per-gap agent context manager.

Owns the shared ``MemorySaver`` checkpointer and generates ``thread_id``
values scoped to a single gap: each gap starts a clean conversation
containing only its own task, while retries of the same gap reuse the
same thread (that history is genuinely useful). Batch steps pass the
fixed scope ``"batch"`` and keep one thread per (phase, gap type) step.
Phase boundaries invalidate every thread via the nonce.

An audit measured 90–98% of per-gap dispatch tokens as re-sent dead
history under the old per-(phase, gap_type) scoping — every gap of a
type appended to one unbounded thread. Cross-gap "learning" was mostly
pattern-shortcutting; see specs/13-quality-and-convergence-guarantees.md for the
follow-up note on a summary mechanism if transfer proves valuable.

When accumulated prompt size exceeds the configured dispatch budget,
oldest messages are trimmed via a ``pre_model_hook`` on the LangGraph
react agent. The hook runs before every LLM call, keeping the most
recent turns and shedding early history. Tokens are counted exactly
(tiktoken, shared with ``prompting/context_budget.py``) — the previous
"approximate" counter undercounted and let real prompts overshoot the
intended cap. No summarisation, no checkpoint surgery — just a FIFO
trim on the message list.

Usage::

    from backend.pipeline.phase_context import phase_context

    phase_context.reset_phase(phase)
    hook = make_trim_hook(config.llm.dispatch_token_budget)
    thread_id = phase_context.get_thread_id(phase, gap.type, gap.node_id)
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import trim_messages
from langgraph.checkpoint.memory import MemorySaver

from backend.analysis.gaps import GapType
from backend.prompting.context_budget import count_tokens

logger = logging.getLogger(__name__)


def _count_message_tokens(messages: list[Any]) -> int:
    """Exact tiktoken count over message contents and tool-call payloads."""
    total = 0
    for message in messages:
        content = message.content
        total += count_tokens(content if isinstance(content, str) else str(content))
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            total += count_tokens(str(tool_calls))
    return total


def make_trim_hook(budget_tokens: int) -> Any:
    """Return a ``pre_model_hook`` that trims oldest messages when over budget.

    ``budget_tokens`` is the explicit cap (``llm.dispatch_token_budget``) —
    task-scaled, not a fraction of the model window. The hook reads the
    accumulated messages from agent state, applies ``trim_messages`` with
    the exact tiktoken counter, and overwrites the messages key. System
    messages are always preserved and the trimmed list always starts on a
    human message. Loud but non-fatal: it trims and logs, never crashes
    mid-loop.
    """

    def _trim(state: dict[str, Any]) -> dict[str, Any]:
        messages = state.get("messages", [])
        if not messages:
            return {"messages": messages}
        trimmed = trim_messages(
            messages,
            max_tokens=budget_tokens,
            token_counter=_count_message_tokens,
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
                budget_tokens,
            )
        return {"messages": trimmed}

    return _trim


class PhaseContext:
    """Manage agent conversation context scoped per gap within a phase."""

    def __init__(self) -> None:
        self._checkpointer = MemorySaver()
        self._nonce: int = 0

    def get_checkpointer(self) -> MemorySaver:
        """Return the shared checkpointer for agent compilation."""
        return self._checkpointer

    def get_thread_id(self, phase: int, gap_type: GapType, scope: str) -> str:
        """Return a deterministic thread ID for one gap (or batch step).

        ``scope`` is the gap's ``node_id`` for per-gap dispatch — each gap
        gets a clean transcript, retries reuse it — or ``"batch"`` for
        batch steps sharing one thread per (phase, gap type).
        """
        return f"phase-{phase}-{gap_type.value}-{scope}-{self._nonce}"

    def reset_phase(self, phase: int) -> None:  # noqa: ARG002
        """Invalidate all thread IDs by incrementing the nonce."""
        self._nonce += 1

    def reset_all(self) -> None:
        """Full reset: new checkpointer + new nonce (start of a run)."""
        self._checkpointer = MemorySaver()
        self._nonce += 1


#: Module-level singleton — single source of truth for phase context.
phase_context = PhaseContext()
