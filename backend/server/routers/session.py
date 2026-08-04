"""Session router — session metadata read/update endpoints."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.core.session import ForgeSession
from backend.server.dependencies import get_broadcaster, get_forge_session
from backend.server.websocket.broadcaster import EventBroadcaster

router = APIRouter(prefix="/session", tags=["session"])


class SessionPatch(BaseModel):
    """Request body for patching mutable session fields."""

    project_name: str | None = None


@router.patch("", response_model=None)
async def patch_session(
    body: SessionPatch,
    session: ForgeSession = Depends(get_forge_session),
    broadcaster: EventBroadcaster = Depends(get_broadcaster),
) -> dict[str, Any]:
    """Update mutable session fields (project_name)."""
    if body.project_name is not None:
        name = body.project_name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="project_name cannot be blank.")
        session.project_name = name

    if broadcaster is not None:
        broadcaster.session_snapshot(session.model_dump(mode="json"))
    return session.model_dump(mode="json")


@router.get("", response_model=None)
async def get_session(
    session: ForgeSession = Depends(get_forge_session),
) -> dict[str, Any]:
    """Return the current session metadata as JSON."""
    return session.model_dump(mode="json")
