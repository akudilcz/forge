"""GET /api/v1/logs — query the structured log store.

Every filter is optional; combinations are AND-joined. Time filters
accept either ``-5m`` / ``-1h`` / ``-7d`` relative strings or ISO
timestamps. Results are paginated (default 500 per page, max 5000).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from backend.observability.query import query_logs

router = APIRouter(prefix="/logs", tags=["logs"])


class LogQueryResponse(BaseModel):
    total: int = Field(..., description="Total rows matching the filter")
    records: list[dict[str, Any]]
    dropped_since: dict[str, Any]


def _resolve_db_path(request: Request) -> Path:
    """Locate the logs DB — preferring app.state; falling back to the
    default location for the configured workspace."""
    db_path = getattr(request.app.state, "logs_db_path", None)
    if db_path:
        return Path(db_path)
    workspace = getattr(request.app.state, "workspace", None)
    if workspace:
        return Path(workspace) / ".forge" / "forge.logs.db"
    raise HTTPException(
        status_code=503, detail="logs DB path not configured on app.state"
    )


@router.get("", response_model=LogQueryResponse, summary="Query structured logs")
def get_logs(  # noqa: PLR0913 — filters are intentionally extensive
    request: Request,
    level: list[str] | None = Query(default=None),
    category: list[str] | None = Query(default=None),
    run_id: str | None = Query(default=None),
    phase: int | None = Query(default=None),
    gap_type: str | None = Query(default=None),
    node_id: str | None = Query(default=None),
    call_id: str | None = Query(default=None),
    since: str | None = Query(
        default=None,
        description="Relative (-5m, -1h, -3d) or ISO timestamp",
    ),
    until: str | None = Query(default=None),
    q: str | None = Query(default=None, description="Substring match in msg/detail"),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> LogQueryResponse:
    db_path = _resolve_db_path(request)
    if not db_path.exists():
        return LogQueryResponse(total=0, records=[], dropped_since={"count": 0, "ts_ms": None})
    try:
        result = query_logs(
            db_path,
            level=level,
            category=category,
            run_id=run_id,
            phase=phase,
            gap_type=gap_type,
            node_id=node_id,
            call_id=call_id,
            since=since,
            until=until,
            q=q,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return LogQueryResponse(**result)
