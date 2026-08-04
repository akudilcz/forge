"""Tests for EventBus subscribe/emit/overflow/history (comms/bus.py)."""

import asyncio

import pytest

from backend.comms.bus import EventBus
from backend.server.websocket.events import WSEvent, WSEventType


def _make_event(phase: int = 1) -> WSEvent:
    return WSEvent(event_type=WSEventType.PHASE_TRANSITION, payload={"phase": phase})


@pytest.mark.asyncio
async def test_emit_delivers_to_subscriber_with_sequence() -> None:
    bus = EventBus()
    q = bus.subscribe()
    event = _make_event()
    await bus.emit(event)
    received = q.get_nowait()
    assert received.event_type == WSEventType.PHASE_TRANSITION
    assert received.sequence == 1
    assert bus.sequence == 1


@pytest.mark.asyncio
async def test_multiple_subscribers_all_receive() -> None:
    bus = EventBus()
    q1 = bus.subscribe()
    q2 = bus.subscribe()
    await bus.emit(_make_event(1))
    assert not q1.empty()
    assert not q2.empty()


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery() -> None:
    bus = EventBus()
    q = bus.subscribe()
    bus.unsubscribe(q)
    await bus.emit(_make_event())
    assert q.empty()


@pytest.mark.asyncio
async def test_unsubscribe_unknown_queue_noop() -> None:
    bus = EventBus()
    bus.unsubscribe(asyncio.Queue())  # Should not raise


@pytest.mark.asyncio
async def test_history_since_returns_correct_events() -> None:
    bus = EventBus()
    await bus.emit(_make_event(1))
    await bus.emit(_make_event(2))
    await bus.emit(_make_event(3))
    events = bus.history_since(1)
    assert len(events) == 2
    assert bus.history_since(10) == []


@pytest.mark.asyncio
async def test_recent_events_respects_limit() -> None:
    bus = EventBus()
    for i in range(10):
        await bus.emit(_make_event(i))
    assert len(bus.recent_events(limit=3)) == 3
    assert len(bus.recent_events()) == 10


@pytest.mark.asyncio
async def test_backpressure_drops_oldest_for_slow_subscriber() -> None:
    bus = EventBus()
    small_q: asyncio.Queue[WSEvent] = asyncio.Queue(maxsize=1)
    bus._subscribers = [small_q]

    await bus.emit(_make_event(1))
    assert small_q.qsize() == 1

    await bus.emit(_make_event(2))
    assert small_q.qsize() == 1
    item = small_q.get_nowait()
    assert item.payload["phase"] == 2
