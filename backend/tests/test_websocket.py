"""Tests for WebSocket broadcaster and event bus."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.comms.bus import EventBus
from backend.server.websocket.broadcaster import EventBroadcaster
from backend.server.websocket.events import WSEvent, WSEventType
from backend.server.websocket.manager import WebSocketManager


@pytest.fixture
def broadcaster() -> EventBroadcaster:
    mgr = WebSocketManager()
    return EventBroadcaster(mgr)


@pytest.mark.asyncio
async def test_broadcaster_emit_and_gap_list_update(broadcaster: EventBroadcaster) -> None:
    broadcaster.emit(WSEventType.PHASE_TRANSITION, {"phase": 1})

    gaps = [
        Gap(type=GapType.UNCHUNKED_DOCUMENT, priority=GapPriority.DOCUMENT_STRUCTURE,
            node_id="doc.spec", description="Test")
    ]
    broadcaster.gap_list_update(gaps)  # Should not raise


@pytest.mark.asyncio
async def test_gap_list_update_includes_total(
    broadcaster: EventBroadcaster, monkeypatch: pytest.MonkeyPatch
) -> None:
    gaps = [
        Gap(type=GapType.UNCHUNKED_DOCUMENT, priority=GapPriority.DOCUMENT_STRUCTURE,
            node_id="doc.1", description="gap 1"),
        Gap(type=GapType.UNCOVERED_PARA, priority=GapPriority.REQUIREMENTS_HLR,
            node_id="doc.2", description="gap 2"),
    ]
    payloads: list[WSEvent] = []
    monkeypatch.setattr(broadcaster._manager, "broadcast", payloads.append)
    broadcaster.gap_list_update(gaps)
    assert len(payloads) == 1
    assert payloads[0].payload["total"] == 2


@pytest.mark.asyncio
async def test_broadcaster_convenience_methods(broadcaster: EventBroadcaster) -> None:
    broadcaster.agent_status_change("agent1", "running", current_task="task")
    broadcaster.phase_transition(0, 1, "active")
    broadcaster.session_snapshot({"session_id": "test"})
    broadcaster.steering_update({"directive": "focus on safety"})


# ── EventBus ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_event_bus_emit_and_receive() -> None:
    bus = EventBus()
    q = bus.subscribe()

    event = WSEvent(event_type=WSEventType.PHASE_TRANSITION, payload={"phase": 1})
    await bus.emit(event)

    received = q.get_nowait()
    assert received.event_type == WSEventType.PHASE_TRANSITION


@pytest.mark.asyncio
async def test_event_bus_history_and_since() -> None:
    bus = EventBus()
    e1 = WSEvent(event_type=WSEventType.PHASE_TRANSITION, payload={})
    e2 = WSEvent(event_type=WSEventType.AGENT_STATUS_CHANGE, payload={})
    await bus.emit(e1)
    seq_after_first = bus.sequence
    await bus.emit(e2)

    history = bus.recent_events()
    assert len(history) == 2

    newer = bus.history_since(seq_after_first)
    assert len(newer) == 1
    assert newer[0].event_type == WSEventType.AGENT_STATUS_CHANGE


@pytest.mark.asyncio
async def test_event_bus_unsubscribe() -> None:
    bus = EventBus()
    q = bus.subscribe()
    bus.unsubscribe(q)
    e = WSEvent(event_type=WSEventType.PHASE_TRANSITION, payload={})
    await bus.emit(e)
    assert q.empty()


# ── WebSocketManager ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_manager_connect_and_disconnect() -> None:
    mgr = WebSocketManager()
    mock_ws = MagicMock()

    async def _accept() -> None:
        pass

    mock_ws.accept = _accept
    await mgr.connect(mock_ws, "conn-1")
    assert mgr.connection_count == 1
    assert "conn-1" in mgr.connection_ids()

    await mgr.disconnect(mock_ws)
    assert mgr.connection_count == 0


@pytest.mark.asyncio
async def test_manager_disconnect_unknown_ws_noop() -> None:
    mgr = WebSocketManager()
    mock_ws = MagicMock()
    await mgr.disconnect(mock_ws)  # Should not raise


@pytest.mark.asyncio
async def test_manager_broadcast_records_history() -> None:
    import asyncio as _asyncio

    mgr = WebSocketManager()
    event = WSEvent(event_type=WSEventType.PHASE_TRANSITION, payload={"phase": 1})
    mgr.broadcast(event)
    await _asyncio.sleep(0)
    assert len(mgr._event_history) == 1


@pytest.mark.asyncio
async def test_manager_broadcast_removes_dead_connection() -> None:
    import asyncio as _asyncio

    mgr = WebSocketManager()
    mock_ws = MagicMock()

    async def _accept() -> None:
        pass

    async def _send_text(_msg: str) -> None:
        raise RuntimeError("disconnected")

    mock_ws.accept = _accept
    mock_ws.send_text = _send_text
    await mgr.connect(mock_ws, "dead-conn")
    assert mgr.connection_count == 1

    event = WSEvent(event_type=WSEventType.PHASE_TRANSITION, payload={})
    mgr.broadcast(event)
    await _asyncio.sleep(0.05)
    assert mgr.connection_count == 0


@pytest.mark.asyncio
async def test_manager_session_context_manager() -> None:
    mgr = WebSocketManager()
    mock_ws = MagicMock()

    async def _accept() -> None:
        pass

    mock_ws.accept = _accept

    async with mgr.session(mock_ws, "conn-ctx"):
        assert mgr.connection_count == 1

    assert mgr.connection_count == 0
