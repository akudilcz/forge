"""Tests for PhaseContext and make_trim_hook — phase-scoped context manager."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import MemorySaver

from backend.analysis.gaps import GapType
from backend.crew.phase_context import PhaseContext, make_trim_hook


def _fresh() -> PhaseContext:
    """Return a fresh PhaseContext for isolated testing."""
    return PhaseContext()


# ── get_thread_id ─────────────────────────────────────────────────────────────


def test_get_thread_id_deterministic() -> None:
    """Same phase + gap type returns the same thread_id within a session."""
    ctx = _fresh()
    tid1 = ctx.get_thread_id(5, GapType.UNMODULARISED)
    tid2 = ctx.get_thread_id(5, GapType.UNMODULARISED)
    assert tid1 == tid2


def test_get_thread_id_varies_by_gap_type() -> None:
    """Different gap types produce different thread IDs."""
    ctx = _fresh()
    tid_a = ctx.get_thread_id(5, GapType.UNMODULARISED)
    tid_b = ctx.get_thread_id(5, GapType.STALE_NODE)
    assert tid_a != tid_b


def test_get_thread_id_varies_by_phase() -> None:
    """Different phases produce different thread IDs."""
    ctx = _fresh()
    tid_a = ctx.get_thread_id(3, GapType.UNCOVERED_PARA)
    tid_b = ctx.get_thread_id(7, GapType.UNCOVERED_PARA)
    assert tid_a != tid_b


def test_get_thread_id_includes_phase_and_gap_type() -> None:
    """Thread ID contains phase number and gap type for debuggability."""
    ctx = _fresh()
    tid = ctx.get_thread_id(5, GapType.UNMODULARISED)
    assert "phase-5" in tid
    assert "UNMODULARISED" in tid


# ── reset_phase ───────────────────────────────────────────────────────────────


def test_reset_phase_changes_thread_id() -> None:
    """After reset_phase, the same phase + gap type returns a different ID."""
    ctx = _fresh()
    tid_before = ctx.get_thread_id(5, GapType.UNMODULARISED)
    ctx.reset_phase(5)
    tid_after = ctx.get_thread_id(5, GapType.UNMODULARISED)
    assert tid_before != tid_after


def test_reset_phase_changes_all_phases() -> None:
    """reset_phase increments a global nonce, affecting all thread IDs."""
    ctx = _fresh()
    tid_other = ctx.get_thread_id(3, GapType.UNCOVERED_PARA)
    ctx.reset_phase(5)
    tid_other_after = ctx.get_thread_id(3, GapType.UNCOVERED_PARA)
    assert tid_other != tid_other_after


# ── reset_all ─────────────────────────────────────────────────────────────────


def test_reset_all_changes_thread_ids() -> None:
    """reset_all changes all thread IDs."""
    ctx = _fresh()
    tid = ctx.get_thread_id(5, GapType.UNMODULARISED)
    ctx.reset_all()
    tid_after = ctx.get_thread_id(5, GapType.UNMODULARISED)
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


def test_trim_hook_noop_when_under_budget() -> None:
    """Hook returns all messages when total tokens are under budget."""
    hook = make_trim_hook(context_window=128000)
    msgs = [
        SystemMessage(content="sys"),
        HumanMessage(content="hi"),
        AIMessage(content="hello"),
    ]
    result = hook({"messages": msgs})
    assert len(result["messages"]) == len(msgs)


def test_trim_hook_removes_oldest_messages() -> None:
    """Hook trims oldest messages when total tokens exceed budget."""
    # Very small context window → aggressive trim
    hook = make_trim_hook(context_window=500)
    msgs = [
        SystemMessage(content="You are an agent."),
        HumanMessage(content="First " + "x" * 200),
        AIMessage(content="Response " + "y" * 200),
        HumanMessage(content="Second " + "x" * 200),
        AIMessage(content="Response " + "y" * 200),
        HumanMessage(content="Third " + "x" * 200),
        AIMessage(content="Response " + "y" * 200),
    ]
    result = hook({"messages": msgs})
    trimmed = result["messages"]
    assert len(trimmed) < len(msgs)


def test_trim_hook_preserves_system_message() -> None:
    """System message is always preserved by the trim hook."""
    hook = make_trim_hook(context_window=500)
    msgs = [
        SystemMessage(content="You are an agent."),
        HumanMessage(content="Old " + "x" * 200),
        AIMessage(content="Old " + "y" * 200),
        HumanMessage(content="New " + "x" * 200),
        AIMessage(content="New " + "y" * 200),
    ]
    result = hook({"messages": msgs})
    trimmed = result["messages"]
    assert isinstance(trimmed[0], SystemMessage)
    assert trimmed[0].content == "You are an agent."


def test_trim_hook_starts_on_human() -> None:
    """After trimming, the first non-system message is a HumanMessage."""
    hook = make_trim_hook(context_window=600)
    msgs = [
        SystemMessage(content="sys"),
        HumanMessage(content="Old " + "x" * 200),
        AIMessage(content="Old " + "y" * 200),
        HumanMessage(content="New " + "x" * 100),
        AIMessage(content="New " + "y" * 100),
    ]
    result = hook({"messages": msgs})
    trimmed = result["messages"]
    non_system = [m for m in trimmed if not isinstance(m, SystemMessage)]
    if non_system:
        assert isinstance(non_system[0], HumanMessage)


def test_trim_hook_noop_on_empty() -> None:
    """Hook handles empty message list gracefully."""
    hook = make_trim_hook(context_window=128000)
    result = hook({"messages": []})
    assert result["messages"] == []


def test_trim_hook_budget_is_seventy_percent() -> None:
    """Hook budget is 70% of the context window (30% reserved)."""
    # 1000 token window → 700 token budget
    # Each message is ~50+ tokens at 4 chars/token
    hook = make_trim_hook(context_window=1000)
    # Create messages totalling more than 700 tokens but less than 1000
    msgs = [
        SystemMessage(content="sys"),
        HumanMessage(content="a" * 1500),  # ~375 tokens
        AIMessage(content="b" * 1500),  # ~375 tokens
        HumanMessage(content="c" * 500),  # ~125 tokens
        AIMessage(content="d" * 500),  # ~125 tokens
    ]
    result = hook({"messages": msgs})
    trimmed = result["messages"]
    # Should have trimmed at least one pair
    assert len(trimmed) < len(msgs)
