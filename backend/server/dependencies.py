"""FastAPI dependency-injection helpers for accessing shared app-state objects.

Each function is a FastAPI ``Depends``-compatible callable that extracts a
service from ``request.app.state``, returning ``None`` if not yet initialised.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import Request

if TYPE_CHECKING:
    from backend.agents.pool import AgentPool
    from backend.comms.bus import EventBus
    from backend.config.models import ForgeConfig
    from backend.core.phase_store import PhaseStore
    from backend.core.session import ForgeSession
    from backend.graph.engine import ProjectGraph
    from backend.server.websocket.broadcaster import EventBroadcaster


def get_agent_pool(request: Request) -> AgentPool | None:
    """Return the shared AgentPool from app state."""
    return getattr(request.app.state, "agent_pool", None)


def get_event_bus(request: Request) -> EventBus | None:
    """Return the shared EventBus from app state."""
    return getattr(request.app.state, "bus", None)


def get_project_graph(request: Request) -> ProjectGraph | None:
    """Return the shared ProjectGraph from app state."""
    return getattr(request.app.state, "graph", None)


def get_forge_session(request: Request) -> ForgeSession | None:
    """Return the active ForgeSession from app state."""
    return getattr(request.app.state, "session", None)


def get_forge_config(request: Request) -> ForgeConfig | None:
    """Return the loaded ForgeConfig from app state."""
    return getattr(request.app.state, "config", None)


def get_broadcaster(request: Request) -> EventBroadcaster | None:
    """Return the shared EventBroadcaster from app state."""
    return getattr(request.app.state, "broadcaster", None)


def get_phase_store(request: Request) -> PhaseStore | None:
    """Return the shared PhaseStore from app state."""
    return getattr(request.app.state, "phase_store", None)



def get_config_path(request: Request) -> Path | None:
    """Return the SQLite db_path used for settings storage."""
    db_path = getattr(request.app.state, "db_path", None)
    return Path(db_path) if db_path else None
