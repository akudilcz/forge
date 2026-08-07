"""Mission-thread history compaction — bounded prompts for Phase 12.

The mission agent runs one continuous LangGraph thread for up to 200
tool calls. Without compaction its prompts grow monotonically (measured
live: 52k→250k tokens over 140-250 sequential calls), so a
``pre_model_hook`` prunes the conversation before every LLM call down to
``llm.mission_token_budget`` (exact tiktoken count).

Compaction never drops or reorders messages — every AI tool call keeps
its matching ``ToolMessage`` so providers never see orphaned calls. Only
message *contents* change. The pruned list is passed to the model via
``llm_input_messages``; checkpointed state is never mutated.

Escalation (specs/13 §"Phase 12 mission history"), applied in this order
and only as far as the budget requires — each step is deterministic:

1. Stub non-protected tool results, oldest first (``PRUNED_STUB``).
2. Truncate *protected* tool results to a head+tail excerpt carrying an
   explicit marker (see :func:`_excerpt`). Excerpt sizes come from a
   max-min fair split of the budget left over once everything else is
   counted, so the largest results give up the most and small ones stay
   verbatim. The latest ``evaluate_progress`` result is truncated LAST
   (it is the agent's current-state signal): it keeps its full size
   while any other protected tool result can still yield.
3. If still over: shrink the preserved recent-turn window
   (``TURN_WINDOW_ESCALATION``: 4 → 2 → 1), re-running steps 1-2 at each
   size. The last turn is always preserved.
4. If still over: truncate the initial mission context down to its first
   ``INITIAL_CONTEXT_FLOOR_TOKENS`` tokens.

The floor — the system prompt plus the initial context's first
``INITIAL_CONTEXT_FLOOR_TOKENS`` tokens — is never removed. If the floor
alone exceeds the budget the history is sent over budget and an ERROR
names the floor's composition: that is a configuration problem only the
operator can fix.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from backend.prompting.context_budget import count_tokens

logger = logging.getLogger(__name__)

#: Number of trailing complete turns (AIMessage + its tool results) that
#: pruning never stubs — in-flight work stays intact.
PRESERVED_RECENT_TURNS = 4

#: Recent-turn window sizes tried in order when truncation alone cannot
#: reach the budget. The last turn is never given up.
TURN_WINDOW_ESCALATION = (PRESERVED_RECENT_TURNS, 2, 1)

#: Head of the initial mission context that survives every escalation.
INITIAL_CONTEXT_FLOOR_TOKENS = 4_096

#: Replacement content for pruned (non-protected) tool results.
PRUNED_STUB = (
    "[pruned: old tool result elided to fit the mission token budget — "
    "re-run the tool if this output is still needed]"
)

#: Stable prefix of the truncation marker — callers and tests locate an
#: excerpt by this substring.
TRUNCATION_MARKER_PREFIX = "\n[... "


def _truncation_marker(elided: int, total: int) -> str:
    """Marker naming exactly how much was elided and how to get it back."""
    return (
        f"{TRUNCATION_MARKER_PREFIX}{elided} of {total} characters elided to fit "
        "the mission token budget — re-run the tool for the full output ...]\n"
    )


def _message_tokens(message: Any) -> int:
    """Exact tiktoken count of one message's content and tool-call payload."""
    content = message.content
    total = count_tokens(content if isinstance(content, str) else str(content))
    if isinstance(message, AIMessage) and message.tool_calls:
        total += count_tokens(str(message.tool_calls))
    return total


def _min_excerpt_tokens() -> int:
    """Smallest excerpt worth calling one: half payload, half marker.

    An excerpt that cannot carry at least as many payload tokens as its
    own marker costs is a stub wearing a longer coat — the preservation
    guarantee it claims to honour would be hollow. When the per-message
    allowance falls below this, the recent-turn window shrinks instead
    (step 3) so the survivors keep a usable excerpt.
    """
    return 2 * count_tokens(_truncation_marker(0, 0))


def _excerpt(content: str, allowed_tokens: int) -> str:
    """Shrink ``content`` to a head+tail excerpt of ``allowed_tokens``.

    ``N``, the number of characters kept, is derived from the budget and
    never guessed: ``allowed_tokens`` is converted to characters using
    *this* string's own measured characters-per-token ratio, then split
    evenly between head and tail. The candidate is re-counted and shrunk
    by 10% until it fits, so a bad ratio (dense JSON, base64)
    self-corrects instead of silently overshooting. Returns the marker
    alone if not even that fits.
    """
    total_tokens = count_tokens(content)
    if total_tokens <= allowed_tokens:
        return content

    keep_chars = int(allowed_tokens * len(content) / total_tokens)
    while keep_chars > 0:
        head_chars = keep_chars // 2
        tail_chars = keep_chars - head_chars
        candidate = (
            content[:head_chars]
            + _truncation_marker(len(content) - keep_chars, len(content))
            + content[len(content) - tail_chars :]
        )
        if count_tokens(candidate) <= allowed_tokens:
            return candidate
        keep_chars = int(keep_chars * 0.9) - 1
    return _truncation_marker(len(content), len(content))


def _head_floor(content: str, floor_tokens: int) -> str:
    """Keep the shortest prefix holding at least ``floor_tokens`` tokens.

    The floor is a *guarantee*, so the prefix rounds up: the first
    ``floor_tokens`` tokens are all present, never partly cut.
    """
    if count_tokens(content) <= floor_tokens:
        return content
    low, high = 0, len(content)
    while low < high:
        middle = (low + high) // 2
        if count_tokens(content[:middle]) >= floor_tokens:
            high = middle
        else:
            low = middle + 1
    return content[:low] + _truncation_marker(len(content) - low, len(content))


def _fair_shares(sizes: list[int], allowance: int) -> list[int]:
    """Max-min fair split of ``allowance`` over ``sizes``.

    Smallest first: each entry takes an equal share of what is left, or
    its full size if smaller, releasing the remainder to the larger ones.
    So only oversized entries are truncated, and they all land on the
    same ceiling — the largest gives up the most.
    """
    shares = [0] * len(sizes)
    remaining = allowance
    for position, index in enumerate(sorted(range(len(sizes)), key=lambda i: (sizes[i], i))):
        share = remaining // (len(sizes) - position)
        shares[index] = min(sizes[index], max(0, share))
        remaining -= shares[index]
    return shares


def _protected_indices(messages: list[Any], recent_turns: int) -> set[int]:
    """Indices covered by the preservation rule (specs/13)."""
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
        recent = ai_indices[-recent_turns:]
        protected.update(range(recent[0], len(messages)))

    return protected


def _stub_old_results(
    messages: list[Any], protected: set[int], budget_tokens: int
) -> tuple[list[Any], int, int]:
    """Step 1 — stub non-protected tool results, oldest first."""
    counts = [_message_tokens(m) for m in messages]
    total = sum(counts)
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

    return pruned, total, stubbed


def _protected_tool_allowances(
    messages: list[Any], indices: list[int], allowance: int
) -> dict[int, int]:
    """Per-message token allowances, latest ``evaluate_progress`` last.

    The scoreboard is served first — up to its full size — but never so
    greedily that another protected result drops below
    :func:`_min_excerpt_tokens`. So it yields only once every other
    protected result is already at its floor: truncated last. What is
    left over is split max-min fairly among the rest.
    """
    sizes = {i: _message_tokens(messages[i]) for i in indices}
    latest_eval = next(
        (i for i in reversed(indices) if messages[i].name == "evaluate_progress"), None
    )
    others = [i for i in indices if i != latest_eval]

    if latest_eval is None:
        return dict(zip(indices, _fair_shares([sizes[i] for i in indices], allowance), strict=True))

    reserved_for_others = _min_excerpt_tokens() * len(others)
    eval_share = min(sizes[latest_eval], max(0, allowance - reserved_for_others))
    shares = _fair_shares([sizes[i] for i in others], allowance - eval_share)
    return {latest_eval: eval_share} | dict(zip(others, shares, strict=True))


def _truncate_protected_results(
    messages: list[Any], protected: set[int], budget_tokens: int, total: int
) -> tuple[list[Any], int, int, bool]:
    """Step 2 — excerpt protected tool results to fit the budget.

    The fourth element is False when some excerpt had to go below
    :func:`_min_excerpt_tokens`, i.e. this window is too wide for the
    budget and the caller should escalate.
    """
    indices = [
        i
        for i in sorted(protected)
        if isinstance(messages[i], ToolMessage) and isinstance(messages[i].content, str)
    ]
    if not indices:
        return messages, total, 0, True

    fixed = total - sum(_message_tokens(messages[i]) for i in indices)
    allowances = _protected_tool_allowances(messages, indices, max(0, budget_tokens - fixed))
    minimum = _min_excerpt_tokens()

    truncated_list = list(messages)
    truncated = 0
    usable = True
    for i in indices:
        content = str(messages[i].content)
        excerpt = _excerpt(content, allowances[i])
        if excerpt == content:
            continue
        truncated_list[i] = messages[i].model_copy(update={"content": excerpt})
        truncated += 1
        usable = usable and allowances[i] >= minimum

    return truncated_list, sum(_message_tokens(m) for m in truncated_list), truncated, usable


def _compact(
    messages: list[Any], budget_tokens: int, recent_turns: int
) -> tuple[list[Any], int, int, int, bool]:
    """Steps 1-2 at one recent-turn window size."""
    protected = _protected_indices(messages, recent_turns)
    pruned, total, stubbed = _stub_old_results(messages, protected, budget_tokens)
    if total <= budget_tokens:
        return pruned, total, stubbed, 0, True
    pruned, total, truncated, usable = _truncate_protected_results(
        pruned, protected, budget_tokens, total
    )
    return pruned, total, stubbed, truncated, usable


def _floor_initial_context(messages: list[Any]) -> list[Any]:
    """Step 4 — cut the initial mission context back to its floor head."""
    index = next((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), None)
    if index is None or not isinstance(messages[index].content, str):
        return messages
    content = str(messages[index].content)
    floored_content = _head_floor(content, INITIAL_CONTEXT_FLOOR_TOKENS)
    if floored_content == content:
        return messages
    floored = list(messages)
    floored[index] = messages[index].model_copy(update={"content": floored_content})
    return floored


def _floor_report(messages: list[Any]) -> tuple[int, int, int, int]:
    """The immovable cost: (system, initial-context floor, other messages, their tokens).

    Nothing may be dropped, so every remaining message costs at least its
    stub (tool results) or its full content plus tool-call payload (AI
    messages) — that structural minimum is part of the floor.
    """
    system = sum(_message_tokens(m) for m in messages if isinstance(m, SystemMessage))
    initial_index = next(
        (i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), None
    )
    initial = (
        min(_message_tokens(messages[initial_index]), INITIAL_CONTEXT_FLOOR_TOKENS)
        if initial_index is not None
        else 0
    )
    stub_tokens = count_tokens(PRUNED_STUB)
    others = [
        m
        for i, m in enumerate(messages)
        if not isinstance(m, SystemMessage) and i != initial_index
    ]
    structural = sum(
        stub_tokens if isinstance(m, ToolMessage) else _message_tokens(m) for m in others
    )
    return system, initial, len(others), structural


def prune_mission_history(messages: list[Any], budget_tokens: int) -> list[Any]:
    """Deterministically prune history to ``budget_tokens``.

    Returns the input list unchanged (same object) when already under
    budget; otherwise returns a new list of the same length and order in
    which contents have been stubbed, excerpted, or both per the module
    escalation. Messages are never dropped or reordered.
    """
    total = sum(_message_tokens(m) for m in messages)
    if total <= budget_tokens:
        return messages

    last_window = TURN_WINDOW_ESCALATION[-1]
    for recent_turns in TURN_WINDOW_ESCALATION:
        pruned, total, stubbed, truncated, usable = _compact(
            messages, budget_tokens, recent_turns
        )
        if total <= budget_tokens and (usable or recent_turns == last_window):
            _log_result(total, budget_tokens, stubbed, truncated, recent_turns, len(pruned))
            return pruned

    pruned, total, stubbed, truncated, _ = _compact(
        _floor_initial_context(messages), budget_tokens, last_window
    )
    if total <= budget_tokens:
        _log_result(total, budget_tokens, stubbed, truncated, last_window, len(pruned))
        return pruned

    system_tokens, initial_tokens, other_count, structural_tokens = _floor_report(messages)
    logger.error(
        "mission_history: %d tokens total against a %d-token budget (over by %d) after "
        "stubbing %d and truncating %d tool result(s) at a %d-turn window — the immovable "
        "floor does not fit: system prompt %d tokens + first %d tokens of the initial "
        "mission context + %d undroppable message(s) costing %d tokens at their minimum. "
        "Sending over budget; raise llm.mission_token_budget, shrink the system prompt or "
        "mission context, or lower the tool-calls-per-pass bound",
        total,
        budget_tokens,
        total - budget_tokens,
        stubbed,
        truncated,
        last_window,
        system_tokens,
        initial_tokens,
        other_count,
        structural_tokens,
    )
    return pruned


def _log_result(
    total: int, budget_tokens: int, stubbed: int, truncated: int, recent_turns: int, count: int
) -> None:
    """Summarise one compaction at INFO (WARNING once escalation bites)."""
    level = logging.INFO if recent_turns == PRESERVED_RECENT_TURNS and not truncated else (
        logging.WARNING
    )
    logger.log(
        level,
        "mission_history: pruned to %d tokens total against a %d-token budget, stubbing %d "
        "and truncating %d tool result(s) with a %d-turn preserved window (%d messages)",
        total,
        budget_tokens,
        stubbed,
        truncated,
        recent_turns,
        count,
    )


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
