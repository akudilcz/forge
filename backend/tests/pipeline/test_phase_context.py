"""Tests for PhaseContext and make_trim_hook — per-gap scoped context manager."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver

from backend.analysis.gaps import GapType
from backend.pipeline.phase_context import PhaseContext, make_trim_hook
from backend.prompting.context_budget import count_tokens


def _fresh() -> PhaseContext:
    """Return a fresh PhaseContext for isolated testing."""
    return PhaseContext()


# ── get_thread_id ─────────────────────────────────────────────────────────────


def test_get_thread_id_retry_same_gap_same_thread() -> None:
    """Retries of the same gap (same node) reuse the same thread ID."""
    ctx = _fresh()
    tid1 = ctx.get_thread_id(5, GapType.UNMODULARISED, "HLR-0001")
    tid2 = ctx.get_thread_id(5, GapType.UNMODULARISED, "HLR-0001")
    assert tid1 == tid2


def test_get_thread_id_new_gap_fresh_thread() -> None:
    """A different gap (different node) of the same type gets a fresh thread."""
    ctx = _fresh()
    tid_a = ctx.get_thread_id(5, GapType.UNMODULARISED, "HLR-0001")
    tid_b = ctx.get_thread_id(5, GapType.UNMODULARISED, "HLR-0002")
    assert tid_a != tid_b


def test_get_thread_id_varies_by_gap_type() -> None:
    """Different gap types produce different thread IDs even for the same node."""
    ctx = _fresh()
    tid_a = ctx.get_thread_id(5, GapType.UNMODULARISED, "HLR-0001")
    tid_b = ctx.get_thread_id(5, GapType.STALE_NODE, "HLR-0001")
    assert tid_a != tid_b


def test_get_thread_id_varies_by_phase() -> None:
    """Different phases produce different thread IDs."""
    ctx = _fresh()
    tid_a = ctx.get_thread_id(3, GapType.UNCOVERED_PARA, "PARA-0001")
    tid_b = ctx.get_thread_id(7, GapType.UNCOVERED_PARA, "PARA-0001")
    assert tid_a != tid_b


def test_get_thread_id_includes_phase_type_and_scope() -> None:
    """Thread ID contains phase, gap type, and scope for debuggability."""
    ctx = _fresh()
    tid = ctx.get_thread_id(5, GapType.UNMODULARISED, "HLR-0007")
    assert "phase-5" in tid
    assert "UNMODULARISED" in tid
    assert "HLR-0007" in tid


def test_get_thread_id_batch_scope_stable() -> None:
    """Batch steps use a fixed scope and share one thread per phase+type."""
    ctx = _fresh()
    tid1 = ctx.get_thread_id(3, GapType.UNCOVERED_PARA, "batch")
    tid2 = ctx.get_thread_id(3, GapType.UNCOVERED_PARA, "batch")
    assert tid1 == tid2


# ── reset_phase ───────────────────────────────────────────────────────────────


def test_reset_phase_changes_thread_id() -> None:
    """After reset_phase, the same gap identity returns a different ID."""
    ctx = _fresh()
    tid_before = ctx.get_thread_id(5, GapType.UNMODULARISED, "HLR-0001")
    ctx.reset_phase(5)
    tid_after = ctx.get_thread_id(5, GapType.UNMODULARISED, "HLR-0001")
    assert tid_before != tid_after


def test_reset_phase_changes_all_phases() -> None:
    """reset_phase increments a global nonce, affecting all thread IDs."""
    ctx = _fresh()
    tid_other = ctx.get_thread_id(3, GapType.UNCOVERED_PARA, "PARA-0001")
    ctx.reset_phase(5)
    tid_other_after = ctx.get_thread_id(3, GapType.UNCOVERED_PARA, "PARA-0001")
    assert tid_other != tid_other_after


# ── reset_all ─────────────────────────────────────────────────────────────────


def test_reset_all_changes_thread_ids() -> None:
    """reset_all changes all thread IDs."""
    ctx = _fresh()
    tid = ctx.get_thread_id(5, GapType.UNMODULARISED, "HLR-0001")
    ctx.reset_all()
    tid_after = ctx.get_thread_id(5, GapType.UNMODULARISED, "HLR-0001")
    assert tid != tid_after


def test_reset_all_creates_new_checkpointer() -> None:
    """reset_all replaces the MemorySaver instance."""
    ctx = _fresh()
    cp1 = ctx.get_checkpointer()
    ctx.reset_all()
    cp2 = ctx.get_checkpointer()
    assert cp1 is not cp2


# ── get_checkpointer ─────────────────────────────────────────────────────────


def test_get_checkpointer_returns_memorysaver() -> None:
    """get_checkpointer returns a MemorySaver instance."""
    ctx = _fresh()
    assert isinstance(ctx.get_checkpointer(), MemorySaver)


def test_get_checkpointer_stable_within_session() -> None:
    """Same checkpointer returned between resets."""
    ctx = _fresh()
    cp1 = ctx.get_checkpointer()
    cp2 = ctx.get_checkpointer()
    assert cp1 is cp2


# ── make_trim_hook ────────────────────────────────────────────────────────────


def _message_tokens(messages: list[Any]) -> int:
    """Sum exact tiktoken counts over message string contents."""
    return sum(count_tokens(m.content) for m in messages if isinstance(m.content, str))


def test_trim_hook_noop_when_under_budget() -> None:
    """Hook returns all messages when total tokens are under budget."""
    hook = make_trim_hook(budget_tokens=24000)
    msgs = [
        SystemMessage(content="sys"),
        HumanMessage(content="hi"),
        AIMessage(content="hello"),
    ]
    result = hook({"messages": msgs})
    assert len(result["messages"]) == len(msgs)


def test_trim_hook_enforces_configured_cap() -> None:
    """Hook trims until the exact token total fits within the configured cap."""
    budget = 150
    hook = make_trim_hook(budget_tokens=budget)
    msgs = [
        SystemMessage(content="You are an agent."),
        HumanMessage(content="First " + "word " * 120),
        AIMessage(content="Response " + "word " * 120),
        HumanMessage(content="Second " + "word " * 60),
        AIMessage(content="Response " + "word " * 60),
    ]
    result = hook({"messages": msgs})
    trimmed = result["messages"]
    assert len(trimmed) < len(msgs)
    assert _message_tokens(trimmed) <= budget


def test_trim_hook_counts_exactly_not_approximately() -> None:
    """Content the chars/4 approximation undercounts must still be trimmed.

    Digit-per-word text tokenises at roughly one token per two characters,
    double what LangChain's "approximate" counter (chars/4) assumes. The old
    hook let such prompts through at ~2x the intended cap (106K real vs 89.6K
    intended). The exact tiktoken counter must trim it.
    """
    text = "9 8 7 3 1 4 2 6 5 0 " * 30  # 600 chars → ~150 approx, ~300 exact
    exact = count_tokens(text)
    approx = len(text) / 4
    budget = int((exact + approx) / 2)  # between the two counts
    assert approx < budget < exact  # premise of the test
    hook = make_trim_hook(budget_tokens=budget)
    msgs = [
        SystemMessage(content="sys"),
        HumanMessage(content=text),
        AIMessage(content="ok"),
        HumanMessage(content="follow-up"),
    ]
    result = hook({"messages": msgs})
    assert _message_tokens(result["messages"]) <= budget


def test_trim_hook_preserves_system_message() -> None:
    """System message is always preserved by the trim hook."""
    hook = make_trim_hook(budget_tokens=100)
    msgs = [
        SystemMessage(content="You are an agent."),
        HumanMessage(content="Old " + "word " * 100),
        AIMessage(content="Old " + "word " * 100),
        HumanMessage(content="New " + "word " * 50),
        AIMessage(content="New " + "word " * 50),
    ]
    result = hook({"messages": msgs})
    trimmed = result["messages"]
    assert isinstance(trimmed[0], SystemMessage)
    assert trimmed[0].content == "You are an agent."


def test_trim_hook_starts_on_human() -> None:
    """After trimming, the first non-system message is a HumanMessage."""
    hook = make_trim_hook(budget_tokens=120)
    msgs = [
        SystemMessage(content="sys"),
        HumanMessage(content="Old " + "word " * 100),
        AIMessage(content="Old " + "word " * 100),
        HumanMessage(content="New " + "word " * 40),
        AIMessage(content="New " + "word " * 40),
    ]
    result = hook({"messages": msgs})
    trimmed = result["messages"]
    non_system = [m for m in trimmed if not isinstance(m, SystemMessage)]
    if non_system:
        assert isinstance(non_system[0], HumanMessage)


def test_trim_hook_noop_on_empty() -> None:
    """Hook handles empty message list gracefully."""
    hook = make_trim_hook(budget_tokens=24000)
    result = hook({"messages": []})
    assert result["messages"] == []
