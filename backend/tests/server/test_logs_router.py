"""Tests for backend.server.routers.logs — structured log query endpoint."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.server.routers import logs as logs_router


def _make_app(
    logs_db_path: str | None, workspace: str | None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(logs_router.router, prefix="/api/v1")
    if logs_db_path is not None:
        app.state.logs_db_path = logs_db_path
    if workspace is not None:
        app.state.workspace = workspace
    return app


class TestResolveDbPath:
    def test_prefers_app_state_path(self, tmp_path: Path) -> None:
        request = MagicMock()
        request.app.state.logs_db_path = str(tmp_path / "l.db")
        assert logs_router._resolve_db_path(request) == tmp_path / "l.db"

    def test_falls_back_to_workspace(self, tmp_path: Path) -> None:
        request = MagicMock()
        request.app.state.logs_db_path = None
        request.app.state.workspace = str(tmp_path)
        expected = tmp_path / ".forge" / "forge.logs.db"
        assert logs_router._resolve_db_path(request) == expected

    def test_raises_503_when_unconfigured(self) -> None:
        request = MagicMock()
        request.app.state.logs_db_path = None
        request.app.state.workspace = None
        with pytest.raises(HTTPException) as exc:
            logs_router._resolve_db_path(request)
        assert exc.value.status_code == 503


class TestGetLogs:
    def test_503_when_no_db_configured(self) -> None:
        app = _make_app(logs_db_path=None, workspace=None)
        resp = TestClient(app).get("/api/v1/logs")
        assert resp.status_code == 503

    def test_empty_response_when_db_missing(self, tmp_path: Path) -> None:
        app = _make_app(str(tmp_path / "absent.db"), workspace=None)
        resp = TestClient(app).get("/api/v1/logs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["records"] == []
        assert data["dropped_since"] == {"count": 0, "ts_ms": None}

    def test_workspace_fallback_path_used(self, tmp_path: Path) -> None:
        app = _make_app(logs_db_path=None, workspace=str(tmp_path))
        resp = TestClient(app).get("/api/v1/logs")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_queries_existing_db_with_filters(self, tmp_path: Path) -> None:
        db = tmp_path / "forge.logs.db"
        db.touch()
        app = _make_app(str(db), workspace=None)
        with patch(
            "backend.server.routers.logs.query_logs",
            return_value={
                "total": 1,
                "records": [{"msg": "hello", "level": "INFO"}],
                "dropped_since": {"count": 0, "ts_ms": None},
            },
        ) as mock_query:
            resp = TestClient(app).get(
                "/api/v1/logs",
                params={
                    "level": ["INFO"], "category": ["PHASE"],
                    "phase": 3, "since": "-5m", "q": "hello",
                    "limit": 10, "offset": 0,
                },
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["records"][0]["msg"] == "hello"
        kwargs = mock_query.call_args.kwargs
        assert kwargs["level"] == ["INFO"]
        assert kwargs["phase"] == 3
        assert kwargs["since"] == "-5m"

    def test_bad_filter_returns_400(self, tmp_path: Path) -> None:
        db = tmp_path / "forge.logs.db"
        db.touch()
        app = _make_app(str(db), workspace=None)
        with patch(
            "backend.server.routers.logs.query_logs",
            side_effect=ValueError("bad since expression"),
        ):
            resp = TestClient(app).get("/api/v1/logs", params={"since": "junk"})
        assert resp.status_code == 400
        assert "bad since expression" in resp.json()["detail"]

    def test_limit_validation(self, tmp_path: Path) -> None:
        app = _make_app(str(tmp_path / "l.db"), workspace=None)
        resp = TestClient(app).get("/api/v1/logs", params={"limit": 99999})
        assert resp.status_code == 422
