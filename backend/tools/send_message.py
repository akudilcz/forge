"""send_message — inter-agent messaging via the event bus."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from backend.tools.base import ForgeTool


class _Args(BaseModel):
    to_agent: str = Field(description="Target agent ID (or 'broadcast' for all agents).")
    subject: str = Field(description="Short subject line (max 120 chars).")
    body: str = Field(description="Message body.")
    priority: str = Field(default="normal", description="Priority: low | normal | high | blocker.")


class SendMessageTool(ForgeTool):
    """Send an inter-agent message (or broadcast) via the WebSocket event bus.

    Messages with priority='blocker' are emitted as BLOCKER_RAISED events; all
    others are emitted as AGENT_MESSAGE events.  If no event loop is running the
    message is silently dropped.
    """

    name: str = "send_message"
    description: str = (
        "Send a message to another agent or broadcast to all agents. "
        "Use priority='blocker' to raise a blocker that stops phase progression. "
        "Messages are delivered via the event bus and visible in the dashboard."
    )
    args_schema: type[BaseModel] = _Args

    _bus: object = None  # EventBus — injected at bind time

    def bind_bus(self, bus: object) -> SendMessageTool:
        """Inject the EventBus instance at runtime and return self for chaining."""
        object.__setattr__(self, "_bus", bus)
        return self

    def _execute(
        self,
        to_agent: str,
        subject: str,
        body: str,
        priority: str = "normal",
    ) -> str:
        """Construct and emit a WSEvent to the bus, then return a status string."""
        bus = self._bus
        if bus is None:
            return "ERROR: Event bus not available"

        from backend.server.websocket.events import WSEvent, WSEventType

        payload = {
            "from_agent": getattr(self, "_agent_id", "unknown"),
            "to_agent": to_agent,
            "subject": subject[:120],
            "body": body,
            "priority": priority,
            "sent_at": datetime.now(UTC).isoformat(),
        }

        event_type = (
            WSEventType.BLOCKER_RAISED
            if priority == "blocker"
            else WSEventType.AGENT_MESSAGE
        )

        event = WSEvent(event_type=event_type, payload=payload)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(bus.emit(event))  # type: ignore[attr-defined]
        except RuntimeError:
            pass  # No running loop — message silently dropped

        return f"OK: message sent to '{to_agent}' (priority={priority})"
