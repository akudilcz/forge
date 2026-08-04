"""Cookie-based session auth middleware for password-protecting the application.

Activated only when both FORGE_AUTH_USER and FORGE_AUTH_PASS environment
variables are set.  When disabled, all requests pass through unmodified.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

# Session cookie lives for 7 days
_SESSION_MAX_AGE = 7 * 24 * 3600
_COOKIE_NAME = "forge_session"

# Exact paths that bypass authentication
_PUBLIC_PATHS = frozenset({"/auth/login", "/auth/check", "/health"})

# Path prefixes that bypass authentication (static assets, SPA shell)
_PUBLIC_PREFIXES = ("/assets/",)


def _make_secret(password: str) -> str:
    """Derive a signing secret from the password."""
    return hashlib.sha256(f"forge-session-{password}".encode()).hexdigest()


def sign_token(username: str, secret: str) -> str:
    """Create a signed session token: ``timestamp:username:signature``."""
    ts = str(int(time.time()))
    payload = f"{ts}:{username}"
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{sig}"


def verify_token(token: str, secret: str) -> str | None:
    """Verify a session token and return the username, or None if invalid/expired."""
    parts = token.split(":", 2)
    if len(parts) != 3:
        return None
    ts_str, username, sig = parts
    expected = hmac.new(secret.encode(), f"{ts_str}:{username}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        ts = int(ts_str)
    except ValueError:
        return None
    if time.time() - ts > _SESSION_MAX_AGE:
        return None
    return username


class SessionAuthMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that enforces cookie-based session auth."""

    def __init__(self, app, username: str, password: str) -> None:  # type: ignore[no-untyped-def]
        super().__init__(app)
        self._username = username
        self._password = password
        self._secret = _make_secret(password)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        # Allow public paths through (auth endpoints, health check, static assets)
        if path in _PUBLIC_PATHS or path.startswith(_PUBLIC_PREFIXES):
            return await call_next(request)

        # Check session cookie
        token = request.cookies.get(_COOKIE_NAME, "")
        user = verify_token(token, self._secret)
        if user is None:
            # For non-API paths (SPA routes), serve the index.html so the
            # frontend can show its own login page instead of a raw 401
            if not path.startswith(("/api/", "/ws", "/auth/")):
                return await call_next(request)
            return Response(content='{"detail":"Unauthorized"}', status_code=401,
                            media_type="application/json")

        return await call_next(request)


def is_auth_enabled() -> bool:
    """Return True if auth credentials are configured."""
    return bool(os.environ.get("FORGE_AUTH_USER") and os.environ.get("FORGE_AUTH_PASS"))


def maybe_add_session_auth(app: FastAPI) -> None:
    """Add session auth middleware if credentials are configured via env vars."""
    username = os.environ.get("FORGE_AUTH_USER", "")
    password = os.environ.get("FORGE_AUTH_PASS", "")
    if username and password:
        app.add_middleware(SessionAuthMiddleware, username=username, password=password)
