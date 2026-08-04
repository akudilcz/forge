"""Console conversation history with token-aware context management.

Maintains a rolling message history for the console agent, trimming
oldest messages when the conversation approaches the model's context
window limit.  A reserve is kept for the system prompt, tool calls,
and the agent's response.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

logger = logging.getLogger(__name__)

# Reserve ~30% of context window for system prompt + agent working space
_CONTEXT_RESERVE_RATIO = 0.30
# Approximate characters per token (conservative)
_CHARS_PER_TOKEN = 4


@dataclass
class ConversationHistory:
    """Thread-safe rolling conversation history with token-budget trimming."""

    context_window: int = 128000
    _messages: list[BaseMessage] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def messages(self) -> list[BaseMessage]:
        """Return a snapshot of the current message history."""
        with self._lock:
            return list(self._messages)

    @property
    def message_count(self) -> int:
        with self._lock:
            return len(self._messages)

    def set_context_window(self, tokens: int) -> None:
        """Update the context window size and re-trim if necessary."""
        with self._lock:
            self.context_window = max(tokens, 4096)
            self._trim_locked()

    def add_user_message(self, content: str) -> None:
        """Append a user message and trim if needed."""
        with self._lock:
            self._messages.append(HumanMessage(content=content))
            self._trim_locked()

    def add_ai_message(self, content: str) -> None:
        """Append an AI response and trim if needed."""
        with self._lock:
            self._messages.append(AIMessage(content=content))
            self._trim_locked()

    def get_messages_for_agent(self) -> list[BaseMessage]:
        """Return trimmed history suitable for passing to the agent."""
        with self._lock:
            self._trim_locked()
            return list(self._messages)

    def clear(self) -> None:
        """Reset the conversation history."""
        with self._lock:
            self._messages.clear()

    def _trim_locked(self) -> None:
        """Remove oldest message pairs until within budget. Must hold _lock."""
        budget = self._token_budget()
        while self._estimate_tokens() > budget and len(self._messages) > 1:
            # Remove oldest message; prefer removing pairs (user + ai)
            self._messages.pop(0)
            # If the next message is an AI response to the removed user msg, drop it too
            if self._messages and isinstance(self._messages[0], AIMessage):
                self._messages.pop(0)

    def _token_budget(self) -> int:
        """Usable token budget after reserving space for agent operations."""
        return int(self.context_window * (1 - _CONTEXT_RESERVE_RATIO))

    def _estimate_tokens(self) -> int:
        """Estimate total tokens in the conversation history."""
        total_chars = sum(len(m.content) for m in self._messages if isinstance(m.content, str))
        return total_chars // _CHARS_PER_TOKEN


# Singleton — one conversation per server process.
console_history = ConversationHistory()
