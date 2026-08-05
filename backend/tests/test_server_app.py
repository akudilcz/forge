"""Tests for backend.server.app — factory wiring, middleware, websocket, SPA."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.server.websocket.manager import WebSocketManager


def _make_app() -> FastAPI:
    os.environ.pop("FORGE_AUTH_USER", None)
    os.environ.pop("FORGE_AUTH_PASS", None)

    from backend.server.app import create_app

    app = create_app()
    app.state.ws_manager = WebSocketManager()

    session = MagicMock()
    session.model_dump.return_value = {"session_id": "sess-test"}
    app.state.session = session

    pool = MagicMock()
    pool.all_ids.return_value = ["doc", "req"]
    app.state.agent_pool = pool

    phase_store = MagicMock()
    phase_store.get_all.return_value = [{"phase_number": 0, "status": "pending"}]
    app.state.phase_store = phase_store
    return app


# ── Health / middleware ─────────────────────────────────────────────────────


class TestHttpMiddleware:
    def test_health_ok(self) -> None:
        app = _make_app()
        resp = TestClient(app).get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_unhandled_exception_logged_and_returns_500(self) -> None:
        app = _make_app()

        @app.get("/boom")
        async def boom() -> None:
            raise RuntimeError("kaboom")

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/boom")
        assert resp.status_code == 500

    def test_error_status_logged_as_warn(self) -> None:
        app = _make_app()
        resp = TestClient(app).get("/api/v1/nope-not-a-route")
        assert resp.status_code == 404


# ── WebSocket endpoint ──────────────────────────────────────────────────────


class TestWebSocketEndpoint:
    def test_snapshot_sent_on_connect(self) -> None:
        app = _make_app()
        client = TestClient(app)
        with client.websocket_connect("/ws") as ws:
            data = ws.receive_json()
        assert data["event_type"] == "session_snapshot"
        assert data["payload"]["session_id"] == "sess-test"
        assert data["payload"]["agents"] == [
            {"agent_id": "doc"}, {"agent_id": "req"},
        ]
        assert data["payload"]["loop_status"] == "idle"

    def test_snapshot_without_pool_or_store(self) -> None:
        app = _make_app()
        app.state.agent_pool = None
        app.state.phase_store = None
        flow = MagicMock()
        flow.state.loop_status = "running"
        app.state.flow = flow
        client = TestClient(app)
        with client.websocket_connect("/ws") as ws:
            data = ws.receive_json()
        assert data["payload"]["agents"] == []
        assert data["payload"]["phases"] == []
        assert data["payload"]["loop_status"] == "running"

    def test_snapshot_failure_closes_connection(self) -> None:
        app = _make_app()
        app.state.session = MagicMock()
        app.state.session.model_dump.side_effect = RuntimeError("dead")
        client = TestClient(app)
        with client.websocket_connect("/ws") as ws:
            with pytest.raises(WebSocketDisconnect):
                ws.receive_json()

    def test_disconnect_unregisters(self) -> None:
        app = _make_app()
        manager: WebSocketManager = app.state.ws_manager
        client = TestClient(app)
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            assert manager.connection_count == 1
        assert manager.connection_count == 0


# ── Frontend dist resolution / SPA serving ──────────────────────────────────


class TestFrontendServing:
    def test_find_frontend_dist_from_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from backend.server.app import _find_frontend_dist

        dist = tmp_path / "frontend" / "dist"
        dist.mkdir(parents=True)
        monkeypatch.chdir(tmp_path)
        assert _find_frontend_dist() == dist

    def test_find_frontend_dist_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from backend.server.app import _find_frontend_dist

        monkeypatch.chdir(tmp_path)
        assert _find_frontend_dist() == tmp_path / "frontend" / "dist"

    def test_spa_catch_all_serves_index(self, tmp_path: Path) -> None:
        dist = tmp_path / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text("<html>SPA SHELL</html>")
        with patch("backend.server.app._FRONTEND_DIST", dist):
            app = _make_app()
        resp = TestClient(app).get("/some/client/route")
        assert resp.status_code == 200
        assert "SPA SHELL" in resp.text


# ── Workspace resolution ────────────────────────────────────────────────────


class TestFindWorkspace:
    def test_env_var_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from backend.server.app import _find_workspace

        monkeypatch.setenv("FORGE_WORKSPACE", str(tmp_path))
        assert _find_workspace() == tmp_path

    def test_defaults_to_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from backend.server.app import _find_workspace

        monkeypatch.delenv("FORGE_WORKSPACE", raising=False)
        monkeypatch.chdir(tmp_path)
        assert _find_workspace() == tmp_path

    def test_create_app_with_explicit_workspace(self, tmp_path: Path) -> None:
        from backend.server.app import create_app

        app = create_app(workspace_path=tmp_path)
        assert app.state.workspace_root == tmp_path
