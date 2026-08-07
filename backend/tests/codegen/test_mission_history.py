"""Behavioural tests for mission-thread history compaction.

Design reference: specs/13-quality-and-convergence-guarantees.md
§"Phase 12 mission history".

The pruner must be deterministic and bring every history under the
budget, escalating only as far as needed: stub old tool results →
truncate protected ones (latest evaluate_progress last) → shrink the
preserved recent-turn window → cut the initial context to its floor.
Messages are never dropped or reordered, and the floor (system prompt +
first 4k tokens of the initial context) is never removed.
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
    INITIAL_CONTEXT_FLOOR_TOKENS,
    PRESERVED_RECENT_TURNS,
    PRUNED_STUB,
    TRUNCATION_MARKER_PREFIX,
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
        pruned = prune_mission_history(messages, 3_000)
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

    def test_last_n_complete_turns_survive_verbatim_when_stubbing_suffices(self) -> None:
        """While step 1 (stubbing old results) can reach the budget the
        recent-turn window stays at its full size and is untouched."""
        messages = _conversation(20, 400)
        pruned = prune_mission_history(messages, _total_tokens(messages) // 2)
        recent = pruned[-2 * PRESERVED_RECENT_TURNS :]
        for original, kept in zip(messages[-2 * PRESERVED_RECENT_TURNS :], recent, strict=True):
            assert kept.content == original.content


# ── escalation beyond stubbing (specs/13 §mission history escalation) ───────


def _protected_heavy(result_words: int) -> list[Any]:
    """Conversation whose PROTECTED tail alone dwarfs any small budget."""
    return _conversation(PRESERVED_RECENT_TURNS, result_words)


class TestProtectedTruncation:
    def test_protected_tail_alone_is_brought_under_budget(self) -> None:
        """Every turn is inside the preserved window, so stubbing reclaims
        nothing — truncation of protected tool results must do it."""
        messages = _protected_heavy(800)
        budget = _total_tokens(messages) // 3
        pruned = prune_mission_history(messages, budget)
        assert _total_tokens(pruned) <= budget

    def test_truncation_keeps_head_and_tail_and_marks_the_elision(self) -> None:
        messages = _protected_heavy(800)
        pruned = prune_mission_history(messages, _total_tokens(messages) // 3)
        truncated = [
            m
            for m in pruned
            if isinstance(m, ToolMessage) and TRUNCATION_MARKER_PREFIX in str(m.content)
        ]
        assert truncated, "expected protected tool results to be truncated"
        for message in truncated:
            content = str(message.content)
            original = next(
                str(o.content)
                for o in messages
                if isinstance(o, ToolMessage) and o.tool_call_id == message.tool_call_id
            )
            head, _, tail = content.partition(TRUNCATION_MARKER_PREFIX)
            assert original.startswith(head)
            assert len(head) > 0
            assert original.endswith(tail.split("...]\n")[-1])
            assert "re-run the tool" in content

    def test_latest_evaluate_progress_is_truncated_last(self) -> None:
        """The scoreboard survives intact while other protected results are
        excerpted, because it is the agent's current-state signal."""
        call_ids = ("c-read", "c-eval")
        messages: list[Any] = [
            SystemMessage(content="SYSTEM PROMPT sentinel"),
            HumanMessage(content="INITIAL MISSION CONTEXT sentinel"),
            AIMessage(
                content="read",
                tool_calls=[{"name": "file_read", "args": {}, "id": call_ids[0]}],
            ),
            ToolMessage(
                content="BIG " * 4000, name="file_read", tool_call_id=call_ids[0]
            ),
            AIMessage(
                content="score",
                tool_calls=[{"name": "evaluate_progress", "args": {}, "id": call_ids[1]}],
            ),
            ToolMessage(
                content="SCOREBOARD " * 20, name="evaluate_progress", tool_call_id=call_ids[1]
            ),
        ]
        budget = _total_tokens(messages) // 3
        pruned = prune_mission_history(messages, budget)
        assert _total_tokens(pruned) <= budget
        assert pruned[5].content == messages[5].content  # evaluate_progress intact
        assert TRUNCATION_MARKER_PREFIX in str(pruned[3].content)

    def test_result_is_deterministic(self) -> None:
        messages = _protected_heavy(800)
        budget = _total_tokens(messages) // 3
        first = prune_mission_history(messages, budget)
        second = prune_mission_history(messages, budget)
        assert [m.content for m in first] == [m.content for m in second]


class TestTurnWindowEscalation:
    def test_window_shrinks_when_truncation_is_insufficient(self) -> None:
        """A budget too small for four excerpted turns drops the window, so
        older 'recent' results become stubs and the budget is met."""
        messages = _conversation(8, 800)
        budget = 800
        pruned = prune_mission_history(messages, budget)
        assert _total_tokens(pruned) <= budget
        recent_results = [
            m for m in pruned[-2 * PRESERVED_RECENT_TURNS :] if isinstance(m, ToolMessage)
        ]
        assert any(m.content == PRUNED_STUB for m in recent_results), (
            "expected the preserved window to have shrunk below PRESERVED_RECENT_TURNS"
        )
        assert str(pruned[-1].content) != PRUNED_STUB, "the last turn is never stubbed"

    def test_floor_over_budget_logs_error_and_sends_unpruned(self, caplog: Any) -> None:
        """When the floor (system prompt + first 4k tokens of the initial
        context) alone exceeds the budget this is an operator configuration
        problem: log ERROR, never violate the floor."""
        messages: list[Any] = [
            SystemMessage(content="SYSTEM PROMPT sentinel " + ("sys " * 5_000)),
            HumanMessage(content="INITIAL MISSION CONTEXT sentinel " + ("ctx " * 5_000)),
            *_turn(0, "file_read", 100),
        ]
        with caplog.at_level(logging.ERROR, logger="backend.codegen.mission_history"):
            pruned = prune_mission_history(messages, 200)
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert errors, "expected an ERROR naming the floor composition"
        assert "floor" in errors[0].message
        assert pruned[0].content == "SYSTEM PROMPT sentinel " + ("sys " * 5_000)
        assert str(pruned[1].content).startswith("INITIAL MISSION CONTEXT sentinel")
        assert _total_tokens(pruned) > 200

    def test_small_initial_context_is_left_alone_at_the_floor_step(self) -> None:
        """Step 4 is a no-op when the context is already under its floor;
        the run still ends over budget and says so."""
        messages = _conversation(8, 800)
        pruned = prune_mission_history(messages, 700)
        assert str(pruned[1].content) == str(messages[1].content)
        assert _total_tokens(pruned) > 700

    def test_already_pruned_history_is_stable(self) -> None:
        """Re-pruning an already-compacted history is idempotent: stubs are
        not re-stubbed and the result does not drift."""
        messages = _conversation(20, 400)
        budget = _total_tokens(messages) // 2
        once = prune_mission_history(messages, budget)
        assert prune_mission_history(once, budget) is once
        tighter = prune_mission_history(once, budget // 2)
        assert _total_tokens(tighter) <= budget // 2
        for before, after in zip(once, tighter, strict=True):
            if before.content == PRUNED_STUB:
                assert after.content == PRUNED_STUB

    def test_initial_context_floor_is_never_truncated_away(self) -> None:
        messages: list[Any] = [
            SystemMessage(content="SYSTEM PROMPT sentinel"),
            HumanMessage(content="INITIAL MISSION CONTEXT sentinel " + ("ctx " * 20_000)),
            *_turn(0, "file_read", 2_000),
        ]
        pruned = prune_mission_history(messages, 6_000)
        kept = str(pruned[1].content)
        assert kept.startswith("INITIAL MISSION CONTEXT sentinel")
        assert count_tokens(kept.split(TRUNCATION_MARKER_PREFIX)[0]) >= INITIAL_CONTEXT_FLOOR_TOKENS

    def test_over_budget_log_states_total_budget_and_overage(self, caplog: Any) -> None:
        messages: list[Any] = [SystemMessage(content="SYS " + ("sys " * 5_000))]
        with caplog.at_level(logging.ERROR, logger="backend.codegen.mission_history"):
            prune_mission_history(messages, 100)
        message = next(r.message for r in caplog.records if r.levelno == logging.ERROR)
        assert "total" in message
        assert "budget" in message
        assert "over by" in message


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
        hook = make_mission_trim_hook(_total_tokens(messages) // 2)
        with caplog.at_level(logging.INFO, logger="backend.codegen.mission_history"):
            hook({"messages": messages})
        assert any("pruned" in r.message for r in caplog.records)
