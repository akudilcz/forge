"""Event broadcaster — typed convenience layer over WebSocketManager.

Provides named methods for every WSEventType so callers don't need to construct
WSEvent objects or reference event-type strings directly.
"""

from __future__ import annotations

from typing import Any

from backend.server.websocket.events import WSEvent, WSEventType
from backend.server.websocket.manager import WebSocketManager


class EventBroadcaster:
    """Higher-level wrapper around :class:`WebSocketManager`.

    Provides typed convenience methods for each :class:`WSEventType`.
    """

    def __init__(self, manager: WebSocketManager) -> None:
        self._manager = manager

    # ------------------------------------------------------------------
    # Generic emit
    # ------------------------------------------------------------------

    def emit(self, event_type: WSEventType, payload: dict[str, Any] | None = None) -> None:
        """Create and broadcast a :class:`WSEvent`."""
        event = WSEvent(event_type=event_type, payload=payload or {})
        self._manager.broadcast(event)

    # ------------------------------------------------------------------
    # Typed convenience helpers
    # ------------------------------------------------------------------

    def agent_status_change(self, agent_id: str, status: str, **extra: Any) -> None:
        """Broadcast an AGENT_STATUS_CHANGE event; extra kwargs are merged into the payload."""
        self.emit(
            WSEventType.AGENT_STATUS_CHANGE,
            {"agent_id": agent_id, "status": status, **extra},
        )

    def task_start(self, task_id: str, agent_id: str, description: str) -> None:
        """Broadcast a TASK_START event for a new agent task."""
        self.emit(
            WSEventType.TASK_START,
            {"task_id": task_id, "agent_id": agent_id, "description": description},
        )

    def task_complete(self, task_id: str, agent_id: str, success: bool = True) -> None:
        """Broadcast a TASK_COMPLETE event when an agent task finishes."""
        self.emit(
            WSEventType.TASK_COMPLETE,
            {"task_id": task_id, "agent_id": agent_id, "success": success},
        )

    def phase_transition(self, from_phase: int, to_phase: int, status: str) -> None:
        """Broadcast a PHASE_TRANSITION event when the active phase changes."""
        self.emit(
            WSEventType.PHASE_TRANSITION,
            {"from_phase": from_phase, "to_phase": to_phase, "status": status},
        )

    def audit_entry(self, entry: dict[str, Any]) -> None:
        """Broadcast an AUDIT_ENTRY event for a new audit log record."""
        self.emit(WSEventType.AUDIT_ENTRY, {"entry": entry})

    def session_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Broadcast a full SESSION_SNAPSHOT (used on initial WebSocket connect)."""
        self.emit(WSEventType.SESSION_SNAPSHOT, snapshot)

    def blocker_raised(self, blocker_id: str, description: str, phase: int) -> None:
        """Broadcast a BLOCKER_RAISED event when a phase-blocking issue is detected."""
        self.emit(
            WSEventType.BLOCKER_RAISED,
            {"blocker_id": blocker_id, "description": description, "phase": phase},
        )

    def blocker_resolved(self, blocker_id: str) -> None:
        """Broadcast a BLOCKER_RESOLVED event when a blocker is cleared."""
        self.emit(WSEventType.BLOCKER_RESOLVED, {"blocker_id": blocker_id})

    def contract_state_change(self, contract_id: str, state: str) -> None:
        """Broadcast a CONTRACT_STATE_CHANGE event when a contract's lifecycle state changes."""
        self.emit(
            WSEventType.CONTRACT_STATE_CHANGE,
            {"contract_id": contract_id, "state": state},
        )

    def architecture_update(self, payload: dict[str, Any]) -> None:
        """Broadcast an ARCHITECTURE_UPDATE event when the architecture graph changes."""
        self.emit(WSEventType.ARCHITECTURE_UPDATE, payload)

    def test_result_stream(self, payload: dict[str, Any]) -> None:
        """Broadcast a TEST_RESULT_STREAM event with incremental test execution output."""
        self.emit(WSEventType.TEST_RESULT_STREAM, payload)

    def steering_update(self, payload: dict[str, Any]) -> None:
        """Broadcast a STEERING_UPDATE event when steering guidance changes."""
        self.emit(WSEventType.STEERING_UPDATE, payload)

    def package_invalidated(self, package_id: str) -> None:
        """Broadcast a PACKAGE_INVALIDATED event when a context package becomes stale."""
        self.emit(WSEventType.PACKAGE_INVALIDATED, {"package_id": package_id})

    def gap_list_update(self, gaps: list[Any]) -> None:
        """Broadcast a GAP_LIST_UPDATE event with the current full gap list."""
        self.emit(
            WSEventType.GAP_LIST_UPDATE,
            {"gaps": [g.model_dump() for g in gaps], "total": len(gaps)},
        )
