"""Flow control — create and cancel ForgeFlow background work for the API.

Owns the flow-task lifecycle used by the phases router endpoints: cancelling
a running flow task, constructing a fresh ForgeFlow, and cancelling all agent
work during a reset. Re-exported by :mod:`backend.server.routers.phases`,
which remains the public facade (tests patch the names there).
"""

import asyncio
from pathlib import Path
from typing import Any

from fastapi import Request

from backend.agents.pool import AgentPool
from backend.config.models import ForgeConfig
from backend.core.phase_store import PhaseStore
from backend.graph.engine import ProjectGraph
from backend.server.websocket.broadcaster import EventBroadcaster
from backend.server.websocket.events import WSEventType


async def _cancel_existing_flow(request: Request) -> None:
    """Cancel and await any currently-running flow task before starting a new one."""
    task: asyncio.Task[Any] | None = getattr(request.app.state, "flow_task", None)
    if task is not None and not task.done():
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=3.0)
        except (TimeoutError, asyncio.CancelledError):
            pass
    request.app.state.flow_task = None


def _make_flow(
    pool: AgentPool, graph: ProjectGraph, config: ForgeConfig,
    broadcaster: EventBroadcaster, phase_store: PhaseStore,
    workspace: Path | None = None,
) -> Any:
    """Create a new ForgeFlow instance."""
    from backend.pipeline.flow import ForgeFlow

    return ForgeFlow(
        pool=pool, graph=graph, config=config,
        broadcaster=broadcaster, phase_store=phase_store,
        workspace=workspace,
    )


async def _cancel_agent_work(
    request: Request,
    broadcaster: EventBroadcaster | None,
) -> None:
    """Cancel running flow and console tasks, reset all agents to idle."""
    for attr in ("flow_task", "console_task"):
        task: asyncio.Task[Any] | None = getattr(request.app.state, attr, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=3.0)
            except (TimeoutError, asyncio.CancelledError):
                pass
        setattr(request.app.state, attr, None)

    if broadcaster is not None:
        broadcaster.emit(WSEventType.PHASE_TRANSITION, {"loop_status": "idle"})
        pool: AgentPool | None = getattr(request.app.state, "agent_pool", None)
        if pool:
            for agent_id in pool.all_ids():
                broadcaster.agent_status_change(agent_id, "idle")
