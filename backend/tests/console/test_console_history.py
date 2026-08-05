"""Tests for console conversation history management."""

from langchain_core.messages import AIMessage, HumanMessage

from backend.console.history import ConversationHistory


def test_add_and_retrieve_messages() -> None:
    h = ConversationHistory(context_window=128000)
    h.add_user_message("Hello")
    h.add_ai_message("Hi there!")
    msgs = h.messages
    assert len(msgs) == 2
    assert isinstance(msgs[0], HumanMessage)
    assert isinstance(msgs[1], AIMessage)
    assert msgs[0].content == "Hello"


def test_clear_resets_history() -> None:
    h = ConversationHistory(context_window=128000)
    h.add_user_message("msg1")
    h.add_ai_message("resp1")
    h.clear()
    assert h.message_count == 0
    assert h.messages == []


def test_trimming_removes_oldest_pairs() -> None:
    # Tiny context window: 50 tokens budget = 35 usable (70%)
    # Each message ~100 chars = ~25 tokens, so only ~1 message fits
    h = ConversationHistory(context_window=50)
    h.add_user_message("A" * 100)
    h.add_ai_message("B" * 100)
    h.add_user_message("C" * 100)
    h.add_ai_message("D" * 100)
    h.add_user_message("E" * 100)  # This should trigger trimming

    msgs = h.get_messages_for_agent()
    assert len(msgs) < 5
    # Most recent message should be preserved
    assert msgs[-1].content == "E" * 100


def test_set_context_window_trims() -> None:
    h = ConversationHistory(context_window=1000000)
    for i in range(20):
        h.add_user_message(f"message {i} " + "x" * 200)
        h.add_ai_message(f"response {i} " + "y" * 200)

    assert h.message_count == 40
    # Shrink to minimum (4096 tokens). Budget = 4096 * 0.7 ≈ 2867 tokens.
    # 40 msgs × 210 chars ≈ 2100 tokens — fits. Use a larger payload.
    h2 = ConversationHistory(context_window=1000000)
    for i in range(50):
        h2.add_user_message(f"message {i} " + "x" * 2000)
        h2.add_ai_message(f"response {i} " + "y" * 2000)

    assert h2.message_count == 100
    h2.set_context_window(5000)  # 5000 * 0.7 = 3500 token budget
    # Each msg ~2010 chars ≈ 502 tokens. Budget fits ~7 messages
    assert h2.message_count < 100


def test_minimum_context_window() -> None:
    h = ConversationHistory()
    h.set_context_window(10)  # below minimum
    assert h.context_window == 4096


def test_get_messages_for_agent_returns_copy() -> None:
    h = ConversationHistory(context_window=128000)
    h.add_user_message("test")
    msgs1 = h.get_messages_for_agent()
    msgs2 = h.get_messages_for_agent()
    assert msgs1 is not msgs2  # different list objects
    assert len(msgs1) == len(msgs2)
