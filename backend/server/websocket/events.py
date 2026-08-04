"""WebSocket event types and envelope model for FORGE's real-time push channel.

``WSEventType`` enumerates every event the server can push to the frontend.
``WSEvent`` is the typed JSON envelope transmitted over the WebSocket connection.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class WSEventType(str, Enum):
    """Enumeration of all WebSocket event types emitted by FORGE."""

    AGENT_STATUS_CHANGE = "AGENT_STATUS_CHANGE"
    TASK_START = "TASK_START"
    TASK_COMPLETE = "TASK_COMPLETE"
    STEP_OUTPUT = "STEP_OUTPUT"
    MESSAGE_BUS_EVENT = "MESSAGE_BUS_EVENT"
    AGENT_MESSAGE = "AGENT_MESSAGE"
    ARCHITECTURE_UPDATE = "ARCHITECTURE_UPDATE"
    TEST_RESULT_STREAM = "TEST_RESULT_STREAM"
    AUDIT_ENTRY = "AUDIT_ENTRY"
    PHASE_TRANSITION = "PHASE_TRANSITION"
    STEERING_UPDATE = "STEERING_UPDATE"
    BLOCKER_RAISED = "BLOCKER_RAISED"
    BLOCKER_RESOLVED = "BLOCKER_RESOLVED"
    CONTRACT_STATE_CHANGE = "CONTRACT_STATE_CHANGE"
    PACKAGE_INVALIDATED = "PACKAGE_INVALIDATED"
    SESSION_SNAPSHOT = "SESSION_SNAPSHOT"
    GAP_LIST_UPDATE = "GAP_LIST_UPDATE"
    FORGE_LOG = "FORGE_LOG"
    WORK_QUEUE = "WORK_QUEUE"
    GRAPH_NODE_CHANGED = "GRAPH_NODE_CHANGED"


class WSEvent(BaseModel):
    """Envelope for all WebSocket messages sent to the frontend."""

    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    event_type: WSEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    sequence: int = 0

    def to_json(self) -> str:
        """Serialise to JSON string for transmission."""
        return self.model_dump_json()
