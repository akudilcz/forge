"""Extended coverage for WebSocketManager — focusing on previously-uncovered branches."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.server.websocket.events import WSEvent, WSEventType
from backend.server.websocket.manager import WebSocketManager


def _event() -> WSEvent:
    return WSEvent(event_type=WSEventType.AGENT_STATUS_CHANGE, payload={"x": 1})


def test_set_loop_stores_loop() -> None:
    mgr = WebSocketManager()
    loop = asyncio.new_event_loop()
    try:
        mgr.set_loop(loop)
        assert mgr._loop is loop
    finally:
        loop.close()


@pytest.mark.asyncio
async def test_session_context_manager_connect_and_disconnect() -> None:
    mgr = WebSocketManager()
    ws = MagicMock()
    ws.accept = AsyncMock()

    async with mgr.session(ws, "conn-1"):
        assert ws in mgr._connections

    assert ws not in mgr._connections


@pytest.mark.asyncio
async def test_broadcast_task_with_no_connections_early_returns() -> None:
    mgr = WebSocketManager()
    # No connections — _broadcast_task should noop.
    await mgr._broadcast_task(_event())  # must not raise


@pytest.mark.asyncio
async def test_broadcast_task_sends_and_collects_dead_sockets() -> None:
    mgr = WebSocketManager()
    alive = MagicMock()
    alive.send_text = AsyncMock()
    dead = MagicMock()
    dead.send_text = AsyncMock(side_effect=RuntimeError("broken pipe"))
    mgr._connections[alive] = "alive"
    mgr._connections[dead] = "dead"

    await mgr._broadcast_task(_event())

    alive.send_text.assert_awaited_once()
    dead.send_text.assert_awaited_once()
    # Dead socket removed.
    assert dead not in mgr._connections
    assert alive in mgr._connections


def test_broadcast_threadsafe_no_loop_is_noop() -> None:
    """Without a set loop, broadcast_threadsafe records the event but does not crash."""
    mgr = WebSocketManager()
    mgr.broadcast_threadsafe(_event())
    assert len(mgr._event_history) == 1


@pytest.mark.asyncio
async def test_send_to_disconnects_on_failure() -> None:
    mgr = WebSocketManager()
    ws = MagicMock()
    ws.send_text = AsyncMock(side_effect=RuntimeError("boom"))
    mgr._connections[ws] = "conn-1"
    await mgr.send_to(ws, _event())
    assert ws not in mgr._connections


def test_history_since_returns_events_above_sequence() -> None:
    mgr = WebSocketManager()
    a, b, c = _event(), _event(), _event()
    a.sequence, b.sequence, c.sequence = 1, 2, 3
    mgr._event_history = [a, b, c]
    assert mgr.history_since(1) == [b, c]


def test_record_trims_over_limit() -> None:
    mgr = WebSocketManager()
    mgr._history_limit = 3
    for i in range(5):
        e = _event()
        e.sequence = i
        mgr._record(e)
    assert len(mgr._event_history) == 3
    assert [e.sequence for e in mgr._event_history] == [2, 3, 4]


@pytest.mark.asyncio
async def test_connect_tolerates_logger_failure() -> None:
    from unittest.mock import patch

    from backend.server.forge_logger import forge_logger

    mgr = WebSocketManager()
    ws = MagicMock()
    ws.accept = AsyncMock()
    with patch.object(forge_logger, "emit", side_effect=RuntimeError("sink down")):
        await mgr.connect(ws, "conn-1")
    assert ws in mgr._connections


@pytest.mark.asyncio
async def test_disconnect_tolerates_logger_failure() -> None:
    from unittest.mock import patch

    from backend.server.forge_logger import forge_logger

    mgr = WebSocketManager()
    ws = MagicMock()
    mgr._connections[ws] = "conn-1"
    with patch.object(forge_logger, "emit", side_effect=RuntimeError("sink down")):
        await mgr.disconnect(ws)
    assert ws not in mgr._connections


@pytest.mark.asyncio
async def test_broadcast_threadsafe_with_loop_schedules_send() -> None:
    mgr = WebSocketManager()
    mgr.set_loop(asyncio.get_running_loop())
    ws = MagicMock()
    ws.send_text = AsyncMock()
    mgr._connections[ws] = "conn-1"

    mgr.broadcast_threadsafe(_event())
    # Let the scheduled coroutine run on this loop.
    for _ in range(5):
        await asyncio.sleep(0)

    ws.send_text.assert_awaited_once()
    assert len(mgr._event_history) == 1
