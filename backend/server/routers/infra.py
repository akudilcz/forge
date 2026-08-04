"""Infrastructure router — build status and pipeline state."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from backend.core.phase_store import PhaseStore
from backend.server.dependencies import get_phase_store

router = APIRouter(prefix="/infra", tags=["infra"])


@router.get("/build-status")
async def get_build_status(
    phase_store: PhaseStore = Depends(get_phase_store),
) -> dict[str, Any]:
    """Return current build pipeline status derived from phase states."""
    if phase_store is None:
        return {
            "build_status": "not_started",
            "last_build": None,
            "artifacts": [],
            "deployment_targets": [],
            "pipeline_stages": [],
        }

    phases = phase_store.get_all()
    active_phase = next((p for p in phases if p["status"] == "active"), None)
    complete_phases = [p for p in phases if p["status"] == "complete"]
    has_error = any(p["status"] == "error" for p in phases)

    if has_error:
        build_status = "failed"
    elif active_phase:
        build_status = "running"
    elif len(complete_phases) == len(phases):
        build_status = "complete"
    elif complete_phases:
        build_status = "partial"
    else:
        build_status = "not_started"

    last_build = (
        max(p["updated_at"] for p in complete_phases) if complete_phases else None
    )

    pipeline_stages = [
        {
            "phase": p["phase_number"],
            "name": p["name"],
            "status": p["status"],
            "updated_at": p["updated_at"],
        }
        for p in phases
    ]

    return {
        "build_status": build_status,
        "last_build": last_build,
        "artifacts": [],
        "deployment_targets": [],
        "pipeline_stages": pipeline_stages,
    }
