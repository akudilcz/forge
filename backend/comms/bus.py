"""Typed async event bus for inter-component communication."""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any


class EventBus:
    """Simple async pub-sub bus. All subscribers receive every event."""

    def __init__(self) -> None:
        self._subscribers: list[asyncio.Queue[Any]] = []
        self._history: deque[Any] = deque(maxlen=2000)
        self._sequence: int = 0

    def subscribe(self) -> asyncio.Queue[Any]:
        """Return a new subscription queue. Caller must drain it promptly."""
        q: asyncio.Queue[Any] = asyncio.Queue(maxsize=2000)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Any]) -> None:
        """Remove a previously subscribed queue. No-op if already removed."""
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    async def emit(self, event: Any) -> None:
        """Broadcast event to all current subscribers."""
        self._sequence += 1
        if hasattr(event, "sequence"):
            event.sequence = self._sequence
        self._history.append(event)
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Drop oldest entry for slow subscriber, then retry
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    pass

    def history_since(self, sequence: int) -> list[Any]:
        """Return buffered events newer than *sequence*."""
        return [e for e in self._history if getattr(e, "sequence", 0) > sequence]

    def recent_events(self, limit: int = 200) -> list[Any]:
        """Return the most recent events from history."""
        return list(self._history)[-limit:]

    @property
    def sequence(self) -> int:
        return self._sequence
