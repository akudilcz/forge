"""WebSocket connection manager — registry and broadcast logic for FORGE's push channel.

Maintains the set of active WebSocket connections and provides both async and
thread-safe broadcast helpers so agents running in executor threads can push
events to the frontend without deadlocking the event loop.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import WebSocket

from backend.server.websocket.events import WSEvent

logger = logging.getLogger(__name__)

_sequence_counter = itertools.count(1)


class WebSocketManager:
    """Manages all active WebSocket connections for a session.

    Thread-safety model: all mutations happen inside asyncio tasks that run
    on the single event-loop thread, so no locking is required.
    """

    def __init__(self) -> None:
        # websocket -> connection_id string
        self._connections: dict[WebSocket, str] = {}
        self._event_history: list[WSEvent] = []
        self._history_limit: int = 1000
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Store the event loop for thread-safe broadcasting from executors."""
        self._loop = loop

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self, websocket: WebSocket, connection_id: str) -> None:
        """Accept *websocket* and register it under *connection_id*."""
        await websocket.accept()
        self._connections[websocket] = connection_id
        logger.info("WebSocket connected: %s (total=%d)", connection_id, len(self._connections))
        try:
            from backend.server.forge_logger import forge_logger  # noqa: PLC0415

            forge_logger.emit(
                "INFO", "WS   ",
                f"connect {connection_id} (total={len(self._connections)})",
                connection_id=connection_id,
                total_connections=len(self._connections),
            )
        except Exception:  # noqa: BLE001
            pass

    async def disconnect(self, websocket: WebSocket) -> None:
        """Remove *websocket* from the registry."""
        connection_id = self._connections.pop(websocket, "<unknown>")
        logger.info(
            "WebSocket disconnected: %s (total=%d)", connection_id, len(self._connections)
        )
        try:
            from backend.server.forge_logger import forge_logger  # noqa: PLC0415

            forge_logger.emit(
                "INFO", "WS   ",
                f"disconnect {connection_id} (total={len(self._connections)})",
                connection_id=connection_id,
                total_connections=len(self._connections),
            )
        except Exception:  # noqa: BLE001
            pass

    @asynccontextmanager
    async def session(self, websocket: WebSocket, connection_id: str) -> AsyncIterator[None]:
        """Async context manager that handles connect/disconnect automatically."""
        await self.connect(websocket, connection_id)
        try:
            yield
        finally:
            await self.disconnect(websocket)

    # ------------------------------------------------------------------
    # Broadcasting
    # ------------------------------------------------------------------

    def broadcast(self, event: WSEvent) -> None:
        """Schedule a broadcast of *event* to all connected clients.

        Uses ``asyncio.create_task`` so it never blocks the caller's coroutine.
        """
        # Stamp the sequence number
        event.sequence = next(_sequence_counter)
        self._record(event)
        asyncio.create_task(self._broadcast_task(event))  # noqa: RUF006

    async def _broadcast_task(self, event: WSEvent) -> None:
        """Async task: send *event* JSON to every connected WebSocket."""
        if not self._connections:
            return
        message = event.to_json()
        dead: list[WebSocket] = []
        for ws in list(self._connections):
            try:
                await ws.send_text(message)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            await self.disconnect(ws)

    def broadcast_threadsafe(self, event: WSEvent) -> None:
        """Schedule broadcast from a thread-pool executor (no running event loop).

        Uses ``asyncio.run_coroutine_threadsafe`` so it is safe to call from
        any thread, including CrewAI / LiteLLM callback threads.
        """
        event.sequence = next(_sequence_counter)
        self._record(event)
        if self._loop is not None:
            asyncio.run_coroutine_threadsafe(self._broadcast_task(event), self._loop)

    async def send_to(self, websocket: WebSocket, event: WSEvent) -> None:
        """Send *event* to a single specific *websocket*."""
        event.sequence = next(_sequence_counter)
        try:
            await websocket.send_text(event.to_json())
        except Exception:  # noqa: BLE001
            await self.disconnect(websocket)

    # ------------------------------------------------------------------
    # History / replay
    # ------------------------------------------------------------------

    def _record(self, event: WSEvent) -> None:
        """Append *event* to the rolling history buffer, trimming if over the limit."""
        self._event_history.append(event)
        if len(self._event_history) > self._history_limit:
            self._event_history = self._event_history[-self._history_limit :]

    def history_since(self, sequence: int) -> list[WSEvent]:
        """Return events with sequence number greater than *sequence*."""
        return [e for e in self._event_history if e.sequence > sequence]

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @property
    def connection_count(self) -> int:
        return len(self._connections)

    def connection_ids(self) -> list[str]:
        """Return the list of active connection ID strings."""
        return list(self._connections.values())
