"""Behavioural tests for mission-thread history compaction.

Design reference: specs/03-build-pipeline.md
§"History Compaction (mission token budget)".

The pruner must be deterministic, prune oldest tool results first, and
ALWAYS preserve: the system prompt, the initial mission context (first
HumanMessage), the latest evaluate_progress result, and the last
``PRESERVED_RECENT_TURNS`` complete turns.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from backend.codegen.mission_history import (
    PRESERVED_RECENT_TURNS,
    PRUNED_STUB,
    make_mission_trim_hook,
    prune_mission_history,
)
from backend.prompting.context_budget import count_tokens

# ── conversation builders ───────────────────────────────────────────────────


def _turn(index: int, tool_name: str, result_words: int) -> list[Any]:
    """One complete ReAct turn: AIMessage(tool_calls) + ToolMessage."""
    call_id = f"call-{index}"
    return [
        AIMessage(
            content=f"turn {index}: calling {tool_name}",
            tool_calls=[{"name": tool_name, "args": {}, "id": call_id}],
        ),
        ToolMessage(
            content=f"result {index} " + ("lorem " * result_words),
            name=tool_name,
            tool_call_id=call_id,
        ),
    ]


def _conversation(n_turns: int, result_words: int) -> list[Any]:
    """System + big initial human + n_turns turns with fat tool results.

    Turn 2 (0-based index 2) is an evaluate_progress call so an OLD
    evaluate result exists; the LAST evaluate_progress is placed near the
    end but outside the protected recent-turn window when n_turns is
    large enough.
    """
    messages: list[Any] = [
        SystemMessage(content="SYSTEM PROMPT sentinel"),
        HumanMessage(content="INITIAL MISSION CONTEXT sentinel " + ("ctx " * 200)),
    ]
    for i in range(n_turns):
        tool = (
            "evaluate_progress" if i in (2, n_turns - PRESERVED_RECENT_TURNS - 1) else "file_read"
        )
        messages.extend(_turn(i, tool, result_words))
    return messages


def _total_tokens(messages: list[Any]) -> int:
    total = 0
    for m in messages:
        total += count_tokens(m.content if isinstance(m.content, str) else str(m.content))
        if isinstance(m, AIMessage) and m.tool_calls:
            total += count_tokens(str(m.tool_calls))
    return total


# ── prune_mission_history ───────────────────────────────────────────────────


class TestBudgetEnforcement:
    def test_under_budget_history_is_returned_unchanged(self) -> None:
        messages = _conversation(3, 5)
        assert prune_mission_history(messages, 100_000) is messages

    def test_oversized_history_is_pruned_to_budget(self) -> None:
        messages = _conversation(20, 400)
        budget = _total_tokens(messages) // 2
        pruned = prune_mission_history(messages, budget)
        assert _total_tokens(pruned) <= budget

    def test_pruning_is_oldest_tool_result_first(self) -> None:
        messages = _conversation(20, 400)
        # Budget large enough that only SOME tool results need stubbing.
        budget = int(_total_tokens(messages) * 0.8)
        pruned = prune_mission_history(messages, budget)
        stub_indices = [
            i
            for i, m in enumerate(pruned)
            if isinstance(m, ToolMessage) and m.content == PRUNED_STUB
        ]
        intact_indices = [
            i
            for i, m in enumerate(pruned)
            if isinstance(m, ToolMessage) and m.content != PRUNED_STUB
        ]
        assert stub_indices, "expected at least one stubbed tool result"
        assert intact_indices, "expected at least one intact tool result"
        # Every stubbed result is older than every intact prunable one
        # (the intact protected ones at the start are evaluate_progress /
        # recent turns, which are exempt).
        prunable_intact = [
            i
            for i in intact_indices
            if pruned[i].name != "evaluate_progress"
            and i < len(pruned) - 2 * PRESERVED_RECENT_TURNS
        ]
        for s in stub_indices:
            for k in prunable_intact:
                assert s < k

    def test_message_count_and_order_are_preserved(self) -> None:
        """Pruning stubs contents; it never drops or reorders messages,
        so AI tool_calls always keep their matching ToolMessage."""
        messages = _conversation(20, 400)
        pruned = prune_mission_history(messages, 1_000)
        assert len(pruned) == len(messages)
        for orig, new in zip(messages, pruned, strict=True):
            assert type(orig) is type(new)

    def test_original_messages_are_not_mutated(self) -> None:
        messages = _conversation(10, 400)
        originals = [m.content for m in messages]
        prune_mission_history(messages, 500)
        assert [m.content for m in messages] == originals


class TestPreservationInvariants:
    def test_system_prompt_and_initial_context_survive(self) -> None:
        messages = _conversation(20, 400)
        pruned = prune_mission_history(messages, 1_000)
        assert pruned[0].content == "SYSTEM PROMPT sentinel"
        assert "INITIAL MISSION CONTEXT sentinel" in pruned[1].content

    def test_latest_evaluate_progress_result_survives(self) -> None:
        messages = _conversation(20, 400)
        pruned = prune_mission_history(messages, 1_000)
        eval_msgs = [
            m for m in pruned if isinstance(m, ToolMessage) and m.name == "evaluate_progress"
        ]
        assert len(eval_msgs) == 2
        # The OLD evaluate result (turn 2) is prunable; the LATEST is not.
        assert eval_msgs[0].content == PRUNED_STUB
        latest_content = eval_msgs[-1].content
        assert isinstance(latest_content, str)
        assert latest_content != PRUNED_STUB
        assert latest_content.startswith(f"result {20 - PRESERVED_RECENT_TURNS - 1}")

    def test_last_n_complete_turns_survive(self) -> None:
        messages = _conversation(20, 400)
        pruned = prune_mission_history(messages, 1_000)
        recent = pruned[-2 * PRESERVED_RECENT_TURNS :]
        for m in recent:
            assert m.content != PRUNED_STUB

    def test_preserved_set_over_budget_warns_but_never_violated(self, caplog: Any) -> None:
        """A budget smaller than the preserved messages logs loudly and
        keeps the preserved set intact rather than breaking the rule."""
        messages = _conversation(20, 400)
        with caplog.at_level(logging.WARNING, logger="backend.codegen.mission_history"):
            pruned = prune_mission_history(messages, 10)
        assert any("budget" in r.message for r in caplog.records)
        assert pruned[0].content == "SYSTEM PROMPT sentinel"
        assert "INITIAL MISSION CONTEXT sentinel" in pruned[1].content
        for m in pruned[-2 * PRESERVED_RECENT_TURNS :]:
            assert m.content != PRUNED_STUB


# ── make_mission_trim_hook ──────────────────────────────────────────────────


class TestMissionTrimHook:
    def test_hook_returns_pruned_llm_input_without_mutating_state(self) -> None:
        messages = _conversation(20, 400)
        hook = make_mission_trim_hook(1_000)
        out = hook({"messages": messages})
        assert set(out) == {"llm_input_messages"}
        assert _total_tokens(out["llm_input_messages"]) < _total_tokens(messages)
        assert any(
            isinstance(m, ToolMessage) and m.content == PRUNED_STUB
            for m in out["llm_input_messages"]
        )
        # Checkpointed state untouched.
        assert all(m.content != PRUNED_STUB for m in messages)

    def test_hook_is_noop_under_budget(self) -> None:
        messages = _conversation(2, 5)
        hook = make_mission_trim_hook(100_000)
        out = hook({"messages": messages})
        assert out["llm_input_messages"] is messages

    def test_hook_logs_prune_summary(self, caplog: Any) -> None:
        messages = _conversation(20, 400)
        hook = make_mission_trim_hook(1_000)
        with caplog.at_level(logging.INFO, logger="backend.codegen.mission_history"):
            hook({"messages": messages})
        assert any("pruned" in r.message for r in caplog.records)
