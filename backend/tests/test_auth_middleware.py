"""Tests for the cookie-based session auth middleware."""

from unittest.mock import patch

from fastapi import FastAPI
from starlette.testclient import TestClient

from backend.server.middleware.auth import (
    SessionAuthMiddleware,
    _make_secret,
    is_auth_enabled,
    maybe_add_session_auth,
    sign_token,
    verify_token,
)


def _make_app(username: str = "admin", password: str = "secret") -> FastAPI:
    """Create a minimal FastAPI app with session auth enabled."""
    app = FastAPI()
    app.add_middleware(SessionAuthMiddleware, username=username, password=password)

    @app.get("/api/ping")
    async def ping() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/auth/login")
    async def login() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/auth/check")
    async def check() -> dict[str, str]:
        return {"status": "ok"}

    return app


class TestTokenSigningAndVerification:
    def test_sign_and_verify_roundtrip(self) -> None:
        secret = _make_secret("mypass")
        token = sign_token("admin", secret)
        assert verify_token(token, secret) == "admin"

    def test_reject_tampered_token(self) -> None:
        secret = _make_secret("mypass")
        token = sign_token("admin", secret)
        tampered = token[:-1] + ("a" if token[-1] != "a" else "b")
        assert verify_token(tampered, secret) is None

    def test_reject_wrong_secret(self) -> None:
        secret = _make_secret("mypass")
        token = sign_token("admin", secret)
        assert verify_token(token, _make_secret("other")) is None

    def test_reject_malformed_token(self) -> None:
        assert verify_token("garbage", _make_secret("x")) is None
        assert verify_token("a:b", _make_secret("x")) is None

    def test_reject_expired_token(self) -> None:
        import time
        secret = _make_secret("mypass")
        old_ts = str(int(time.time()) - 8 * 24 * 3600)  # 8 days ago
        import hashlib
        import hmac as hmac_mod
        payload = f"{old_ts}:admin"
        sig = hmac_mod.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        token = f"{payload}:{sig}"
        assert verify_token(token, secret) is None


class TestSessionAuthMiddleware:
    def test_rejects_unauthenticated_api_request(self) -> None:
        client = TestClient(_make_app())
        resp = client.get("/api/ping")
        assert resp.status_code == 401

    def test_allows_health_check(self) -> None:
        client = TestClient(_make_app())
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_allows_auth_login(self) -> None:
        client = TestClient(_make_app())
        resp = client.post("/auth/login")
        assert resp.status_code == 200

    def test_allows_auth_check(self) -> None:
        client = TestClient(_make_app())
        resp = client.get("/auth/check")
        assert resp.status_code == 200

    def test_accepts_valid_session_cookie(self) -> None:
        secret = _make_secret("secret")
        token = sign_token("admin", secret)
        client = TestClient(_make_app(), cookies={"forge_session": token})
        resp = client.get("/api/ping")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_rejects_invalid_session_cookie(self) -> None:
        client = TestClient(_make_app(), cookies={"forge_session": "bogus"})
        resp = client.get("/api/ping")
        assert resp.status_code == 401


class TestIsAuthEnabled:
    def test_disabled_without_env_vars(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("FORGE_AUTH_USER", None)
            os.environ.pop("FORGE_AUTH_PASS", None)
            assert is_auth_enabled() is False

    def test_enabled_with_env_vars(self) -> None:
        with patch.dict("os.environ", {"FORGE_AUTH_USER": "u", "FORGE_AUTH_PASS": "p"}):
            assert is_auth_enabled() is True


class TestMaybeAddSessionAuth:
    def test_does_nothing_without_env_vars(self) -> None:
        app = FastAPI()

        @app.get("/api/ping")
        async def ping() -> dict[str, str]:
            return {"ok": "true"}

        with patch.dict("os.environ", {}, clear=False):
            import os
            os.environ.pop("FORGE_AUTH_USER", None)
            os.environ.pop("FORGE_AUTH_PASS", None)
            maybe_add_session_auth(app)

        client = TestClient(app)
        assert client.get("/api/ping").status_code == 200

    def test_adds_auth_when_env_vars_set(self) -> None:
        app = FastAPI()

        @app.get("/api/ping")
        async def ping() -> dict[str, str]:
            return {"ok": "true"}

        with patch.dict("os.environ", {"FORGE_AUTH_USER": "u", "FORGE_AUTH_PASS": "p"}):
            maybe_add_session_auth(app)

        secret = _make_secret("p")
        token = sign_token("u", secret)
        client = TestClient(app)
        assert client.get("/api/ping").status_code == 401
        client_auth = TestClient(app, cookies={"forge_session": token})
        assert client_auth.get("/api/ping").status_code == 200
