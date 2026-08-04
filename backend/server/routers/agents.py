"""Agents router — agent listing, prompt management, and inter-agent messaging endpoints.

Exposes REST endpoints for listing agents, managing per-role and per-gap-type
prompt overrides, sending directives, and retrieving agent message history.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from backend.agents.factory import (
    gap_inherits_from_role,
    get_gap_prompt,
    get_prompt,
    is_default_gap_prompt,
    is_default_prompt,
    reset_gap_prompt,
    reset_prompt,
    set_gap_prompt,
    set_prompt,
)
from backend.agents.pool import AgentPool
from backend.comms.bus import EventBus
from backend.server.dependencies import get_agent_pool, get_event_bus
from backend.server.websocket.events import WSEvent, WSEventType

router = APIRouter(prefix="/agents", tags=["agents"])


class DirectiveBody(BaseModel):
    """Request body for sending a direct instruction to an agent."""

    content: str
    priority: str = "normal"


class PromptBody(BaseModel):
    """Request body for setting a custom system prompt on an agent or gap type."""

    prompt: str


@router.get("")
async def list_agents(
    pool: AgentPool = Depends(get_agent_pool),
) -> list[dict[str, Any]]:
    """Return the list of all registered agent IDs."""
    if pool is None:
        return []
    return [{"agent_id": aid} for aid in pool.all_ids()]


@router.get("/definitions")
async def list_agent_definitions(
    request: Request,
) -> list[dict[str, Any]]:
    """Return static agent definitions: role, model, tools, goal, gap types handled."""
    from backend.agents.definitions import AGENT_REGISTRY, GAP_AGENT_MAPPING  # noqa: PLC0415

    config = getattr(request.app.state, "config", None)
    registry = getattr(request.app.state, "tool_registry", None)

    role_gaps: dict[str, list[str]] = {}
    for gap_type, role in GAP_AGENT_MAPPING.items():
        role_gaps.setdefault(role.value, []).append(gap_type.value)

    result = []
    for role, defn in AGENT_REGISTRY.items():
        model = config.llm.agents.get(role.value, "") if config else ""
        tools = sorted(t.name for t in registry.get_tools_for_role(role)) if registry else []
        result.append(
            {
                "role": defn.role.value,
                "model": model,
                "goal": defn.goal,
                "tools": tools,
                "gap_types": role_gaps.get(role.value, []),
            }
        )
    return result


# ── Gap-type prompt endpoints ─────────────────────────────────────────────────


@router.get("/gaps/{gap_type}/prompt")
async def get_gap_prompt_endpoint(gap_type: str) -> dict[str, Any]:
    """Return the effective system prompt for a gap type (gap override > role > default)."""
    from backend.agents.definitions import GAP_AGENT_MAPPING  # noqa: PLC0415
    from backend.analysis.gaps import GapType  # noqa: PLC0415

    try:
        gt = GapType(gap_type)
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail=f"Unknown gap type '{gap_type}'"
        ) from exc

    role = GAP_AGENT_MAPPING.get(gt)
    role_name = role.value if role else ""
    return {
        "gap_type": gap_type,
        "prompt": get_gap_prompt(gap_type, role_name),
        "is_default": is_default_gap_prompt(gap_type),
        "inherited_from": role_name if gap_inherits_from_role(gap_type) else None,
    }


@router.put("/gaps/{gap_type}/prompt")
async def set_gap_prompt_endpoint(gap_type: str, body: PromptBody) -> dict[str, Any]:
    """Store a gap-type-specific system prompt override."""
    if not body.prompt.strip():
        raise HTTPException(status_code=422, detail="Prompt must not be empty")
    set_gap_prompt(gap_type, body.prompt)
    return {
        "gap_type": gap_type,
        "prompt": body.prompt,
        "is_default": False,
        "inherited_from": None,
    }


@router.delete("/gaps/{gap_type}/prompt")
async def reset_gap_prompt_endpoint(gap_type: str) -> dict[str, Any]:
    """Remove any gap-type override, restoring the role-level or default prompt."""
    from backend.agents.definitions import GAP_AGENT_MAPPING  # noqa: PLC0415
    from backend.analysis.gaps import GapType  # noqa: PLC0415

    reset_gap_prompt(gap_type)
    try:
        gt = GapType(gap_type)
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail=f"Unknown gap type '{gap_type}'"
        ) from exc
    role = GAP_AGENT_MAPPING.get(gt)
    role_name = role.value if role else ""
    return {
        "gap_type": gap_type,
        "prompt": get_gap_prompt(gap_type, role_name),
        "is_default": True,
        "inherited_from": role_name if gap_inherits_from_role(gap_type) else None,
    }


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str,
    pool: AgentPool = Depends(get_agent_pool),
) -> dict[str, Any]:
    """Return info for a single agent."""
    if pool is None:
        raise HTTPException(status_code=503, detail="Agent pool not initialised")
    if agent_id not in pool.all_ids():
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")
    return {"agent_id": agent_id}


@router.post("/{agent_id}/directive")
async def send_directive(
    agent_id: str,
    body: DirectiveBody,
    bus: EventBus = Depends(get_event_bus),
) -> dict[str, str]:
    """Send a direct instruction to an agent via the event bus."""
    if bus is None:
        raise HTTPException(status_code=503, detail="Event bus not available")

    await bus.emit(
        WSEvent(
            event_type=WSEventType.AGENT_MESSAGE,
            payload={
                "to_agent": agent_id,
                "from_agent": "engineer",
                "subject": "Direct directive",
                "body": body.content,
                "priority": body.priority,
            },
        )
    )
    return {"status": "sent", "agent_id": agent_id}


@router.get("/{agent_id}/messages")
async def get_agent_messages(
    agent_id: str,
    bus: EventBus = Depends(get_event_bus),
) -> list[dict[str, Any]]:
    """Return recent messages addressed to this agent from the event bus history."""
    if bus is None:
        return []
    messages = []
    for event in bus.recent_events():
        payload = event.payload
        if payload.get("to_agent") == agent_id or payload.get("to_agent") == "broadcast":
            messages.append(
                {
                    "event_id": str(event.event_id),
                    "event_type": event.event_type,
                    "payload": payload,
                    "timestamp": event.timestamp.isoformat(),
                }
            )
    return messages[-50:]


# ── Prompt management ─────────────────────────────────────────────────────────


@router.get("/{agent_id}/prompt")
async def get_agent_prompt(agent_id: str) -> dict[str, Any]:
    """Return the effective system prompt for the agent (override or default)."""
    return {
        "role": agent_id,
        "prompt": get_prompt(agent_id),
        "is_default": is_default_prompt(agent_id),
    }


@router.put("/{agent_id}/prompt")
async def set_agent_prompt(agent_id: str, body: PromptBody) -> dict[str, Any]:
    """Store a custom system prompt override for the agent."""
    if not body.prompt.strip():
        raise HTTPException(status_code=422, detail="Prompt must not be empty")
    set_prompt(agent_id, body.prompt)
    return {"role": agent_id, "prompt": body.prompt, "is_default": False}


@router.delete("/{agent_id}/prompt")
async def reset_agent_prompt(agent_id: str) -> dict[str, Any]:
    """Remove any custom override, restoring the default prompt."""
    reset_prompt(agent_id)
    return {"role": agent_id, "prompt": get_prompt(agent_id), "is_default": True}
