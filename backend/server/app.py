"""FastAPI application factory for the FORGE Control Station server.

Wires together routers, WebSocket endpoint, CORS middleware, and optional
static frontend serving; intended to be used with uvicorn's ``--factory`` flag.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.server.lifespan import lifespan
from backend.server.middleware.auth import maybe_add_session_auth
from backend.server.routers import (
    agents,
    architecture,
    contracts,
    infra,
    quality,
    requirements,
    steering,
    workspace,
)
from backend.server.routers import auth as auth_router
from backend.server.routers import (
    compliance as compliance_router,
)
from backend.server.routers import (
    console as console_router,
)
from backend.server.routers import (
    graph as graph_router,
)
from backend.server.routers import (
    patterns as patterns_router,
)
from backend.server.routers import (
    phases as phases_router_mod,
)
from backend.server.routers import (
    secrets as secrets_router,
)
from backend.server.routers import (
    session as session_router_mod,
)
from backend.server.routers import (
    settings as settings_router,
)
from backend.server.routers import (
    tests as tests_router,
)
from backend.server.websocket.events import WSEvent, WSEventType
from backend.server.websocket.manager import WebSocketManager


# Resolve frontend/dist.  When installed non-editable (Docker) __file__ lives
# inside site-packages so parent traversal won't reach the project root.
# Fall back to CWD-relative path which matches the Dockerfile WORKDIR (/app).
def _find_frontend_dist() -> Path:
    for base in (Path(__file__).resolve().parent, Path.cwd()):
        candidate = base
        for _ in range(6):
            dist = candidate / "frontend" / "dist"
            if dist.is_dir():
                return dist
            candidate = candidate.parent
    return Path.cwd() / "frontend" / "dist"


_FRONTEND_DIST = _find_frontend_dist()


def create_app(workspace_path: Path | None = None) -> FastAPI:
    """Application factory used by uvicorn ``--factory`` flag."""
    # Enable faulthandler so Ctrl+C / SIGINT dumps full tracebacks of all threads
    import faulthandler
    import signal

    faulthandler.enable()
    faulthandler.register(signal.SIGUSR1)  # kill -USR1 <pid> for on-demand dump
    app = FastAPI(
        title="FORGE Control Station",
        description="AI-driven software build orchestration system.",
        version="1.1.0",
        lifespan=lifespan,
    )

    # Store workspace so lifespan can derive the DB path
    app.state.workspace_root = workspace_path or _find_workspace()

    # ------------------------------------------------------------------
    # CORS — allow all origins in development
    # ------------------------------------------------------------------
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------
    # Request/response observability
    # ------------------------------------------------------------------
    @app.middleware("http")
    async def _observability_middleware(request, call_next):  # type: ignore[no-untyped-def]
        import time as _time  # noqa: PLC0415

        from backend.server.forge_logger import forge_logger  # noqa: PLC0415

        t0 = _time.monotonic()
        try:
            response = await call_next(request)
        except Exception as exc:  # noqa: BLE001
            duration_ms = int((_time.monotonic() - t0) * 1000)
            forge_logger.emit(
                "ERROR", "HTTP ",
                f"{request.method} {request.url.path} → 500 after {duration_ms}ms",
                http_method=request.method,
                http_path=str(request.url.path),
                duration_ms=duration_ms,
                error_type=type(exc).__name__,
            )
            raise
        duration_ms = int((_time.monotonic() - t0) * 1000)
        level = "INFO" if response.status_code < 400 else "WARN"
        forge_logger.emit(
            level, "HTTP ",
            f"{request.method} {request.url.path} → {response.status_code} ({duration_ms}ms)",
            http_method=request.method,
            http_path=str(request.url.path),
            http_status=response.status_code,
            duration_ms=duration_ms,
        )
        return response

    # ------------------------------------------------------------------
    # Optional session auth (set FORGE_AUTH_USER + FORGE_AUTH_PASS)
    # ------------------------------------------------------------------
    maybe_add_session_auth(app)

    # ------------------------------------------------------------------
    # API routers — mounted under /api/v1 (canonical) and /api (shorthand
    # used by the frontend in production where no Vite proxy rewrites)
    # ------------------------------------------------------------------
    # Auth routes — outside /api prefix, public paths bypass middleware
    app.include_router(auth_router.router)

    from backend.server.routers import logs as logs_router  # noqa: PLC0415

    _routers = [
        console_router.router,
        session_router_mod.router,
        phases_router_mod.router,
        agents.router,
        architecture.router,
        contracts.router,
        requirements.router,
        quality.router,
        infra.router,
        steering.router,
        workspace.router,
        graph_router.router,
        compliance_router.router,
        tests_router.router,
        patterns_router.router,
        settings_router.router,
        secrets_router.router,
        logs_router.router,
    ]
    for router in _routers:
        app.include_router(router, prefix="/api/v1")
        app.include_router(router, prefix="/api")

    # ------------------------------------------------------------------
    # WebSocket endpoint
    # ------------------------------------------------------------------
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        """Accept a WebSocket connection, send a state snapshot, then hold open for push events."""
        manager: WebSocketManager = app.state.ws_manager
        connection_id = str(uuid.uuid4())

        async with manager.session(websocket, connection_id):
            # Send a welcome snapshot so the frontend has full state on connect
            try:
                session = app.state.session
                pool = getattr(app.state, "agent_pool", None)
                agent_states = (
                    [{"agent_id": aid} for aid in pool.all_ids()] if pool is not None else []
                )
                phase_store = getattr(app.state, "phase_store", None)
                phase_states = phase_store.get_all() if phase_store is not None else []
                flow = getattr(app.state, "flow", None)
                loop_status = flow.state.loop_status if flow is not None else "idle"
                snapshot_event = WSEvent(
                    event_type=WSEventType.SESSION_SNAPSHOT,
                    payload={
                        **session.model_dump(mode="json"),
                        "agents": agent_states,
                        "phases": phase_states,
                        "loop_status": loop_status,
                    },
                )
                await manager.send_to(websocket, snapshot_event)
            except Exception:  # noqa: BLE001
                return  # Connection already dead before we could send

            # Keep-alive loop: wait for client messages (or disconnect)
            try:
                while True:
                    await websocket.receive_text()
            except (WebSocketDisconnect, Exception):  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------
    @app.get("/health")
    async def health() -> dict[str, str]:
        """Liveness probe — returns ``{"status": "ok"}`` when the server is up."""
        return {"status": "ok"}

    # ------------------------------------------------------------------
    # Serve static frontend (production build)
    # ------------------------------------------------------------------
    if _FRONTEND_DIST.exists():
        app.mount("/assets", StaticFiles(directory=str(_FRONTEND_DIST / "assets")), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str) -> FileResponse:
            """Catch-all route that serves the SPA index.html for client-side routing."""
            return FileResponse(str(_FRONTEND_DIST / "index.html"))

    return app


def _find_workspace() -> Path:
    """Resolve workspace path from env var or current working directory."""
    env = os.environ.get("FORGE_WORKSPACE", "")
    return Path(env) if env else Path.cwd()
