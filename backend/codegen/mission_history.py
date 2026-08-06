"""Mission-thread history compaction — bounded prompts for Phase 12.

The mission agent runs one continuous LangGraph thread for up to 200
tool calls. Without compaction its prompts grow monotonically (measured
live: 52k→250k tokens over 140-250 sequential calls), so a
``pre_model_hook`` prunes the conversation before every LLM call down to
``llm.mission_token_budget`` (exact tiktoken count).

Pruning is deterministic and **oldest tool result first**: eligible
``ToolMessage`` contents are replaced with a short stub — the agent can
re-run the tool if it still needs the output. Messages are never dropped
or reordered, so every AI tool call keeps its matching tool result and
providers never see orphaned calls. The pruned list is passed to the
model via ``llm_input_messages``; checkpointed state is never mutated.

Preservation rule (design/22 §History Compaction) — ALWAYS kept verbatim:

1. The system prompt.
2. The initial mission context (first ``HumanMessage``).
3. The latest ``evaluate_progress`` result (current scoreboard).
4. The last ``PRESERVED_RECENT_TURNS`` complete turns (an ``AIMessage``
   plus its following ``ToolMessage``s).

If the preserved set alone exceeds the budget, the hook logs a loud
warning and sends it unpruned — the rule is never violated to hit the
number.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from backend.prompting.context_budget import count_tokens

logger = logging.getLogger(__name__)

#: Number of trailing complete turns (AIMessage + its tool results) that
#: pruning never touches — in-flight work stays intact.
PRESERVED_RECENT_TURNS = 4

#: Replacement content for pruned tool results.
PRUNED_STUB = (
    "[pruned: old tool result elided to fit the mission token budget — "
    "re-run the tool if this output is still needed]"
)


def _message_tokens(message: Any) -> int:
    """Exact tiktoken count of one message's content and tool-call payload."""
    content = message.content
    total = count_tokens(content if isinstance(content, str) else str(content))
    if isinstance(message, AIMessage) and message.tool_calls:
        total += count_tokens(str(message.tool_calls))
    return total


def _protected_indices(messages: list[Any]) -> set[int]:
    """Indices covered by the preservation rule (design/22)."""
    protected: set[int] = set()

    for i, message in enumerate(messages):
        if isinstance(message, SystemMessage):
            protected.add(i)

    for i, message in enumerate(messages):
        if isinstance(message, HumanMessage):
            protected.add(i)  # initial mission context
            break

    for i in range(len(messages) - 1, -1, -1):
        message = messages[i]
        if isinstance(message, ToolMessage) and message.name == "evaluate_progress":
            protected.add(i)  # latest scoreboard
            break

    ai_indices = [i for i, m in enumerate(messages) if isinstance(m, AIMessage)]
    if ai_indices:
        recent = ai_indices[-PRESERVED_RECENT_TURNS:]
        protected.update(range(recent[0], len(messages)))

    return protected


def prune_mission_history(messages: list[Any], budget_tokens: int) -> list[Any]:
    """Deterministically prune history to ``budget_tokens``.

    Returns the input list unchanged (same object) when already under
    budget; otherwise returns a new list in which the oldest
    non-protected tool results have their content replaced by
    ``PRUNED_STUB`` until the total fits. Never drops or reorders
    messages and never touches protected ones.
    """
    counts = [_message_tokens(m) for m in messages]
    total = sum(counts)
    if total <= budget_tokens:
        return messages

    protected = _protected_indices(messages)
    stub_tokens = count_tokens(PRUNED_STUB)
    pruned = list(messages)
    stubbed = 0

    for i, message in enumerate(messages):
        if total <= budget_tokens:
            break
        if i in protected or not isinstance(message, ToolMessage):
            continue
        if message.content == PRUNED_STUB:
            continue
        pruned[i] = message.model_copy(update={"content": PRUNED_STUB})
        total -= counts[i] - stub_tokens
        stubbed += 1

    if total > budget_tokens:
        logger.warning(
            "mission_history: %d tokens still over the %d-token budget after "
            "pruning %d tool result(s) — the preserved set (system prompt, "
            "initial context, latest evaluate_progress, last %d turns) "
            "exceeds the budget and is sent unpruned",
            total - budget_tokens,
            budget_tokens,
            stubbed,
            PRESERVED_RECENT_TURNS,
        )
    if stubbed:
        logger.info(
            "mission_history: pruned %d tool result(s); history now %d tokens "
            "(budget %d, %d messages)",
            stubbed,
            total,
            budget_tokens,
            len(pruned),
        )
    return pruned


def make_mission_trim_hook(budget_tokens: int) -> Any:
    """Return a ``pre_model_hook`` enforcing the mission token budget.

    The hook prunes via :func:`prune_mission_history` and returns the
    result as ``llm_input_messages`` only — checkpointed thread state is
    never rewritten, so pruning stays deterministic and reversible (a
    later, larger budget would see the original history).
    """

    def _hook(state: dict[str, Any]) -> dict[str, Any]:
        return {"llm_input_messages": prune_mission_history(state["messages"], budget_tokens)}

    return _hook
