"""Auth router — login, logout, and session check endpoints."""

from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, Cookie, Response
from pydantic import BaseModel

from backend.server.middleware.auth import (
    _COOKIE_NAME,
    _SESSION_MAX_AGE,
    _make_secret,
    is_auth_enabled,
    sign_token,
    verify_token,
)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class AuthStatus(BaseModel):
    authenticated: bool
    auth_required: bool


@router.post("/login")
async def login(body: LoginRequest, response: Response) -> dict[str, str]:
    """Validate credentials and set a session cookie."""
    expected_user = os.environ.get("FORGE_AUTH_USER", "")
    expected_pass = os.environ.get("FORGE_AUTH_PASS", "")

    if not expected_user or not expected_pass:
        return {"status": "ok", "detail": "Auth not configured"}

    user_ok = secrets.compare_digest(body.username, expected_user)
    pass_ok = secrets.compare_digest(body.password, expected_pass)

    if not (user_ok and pass_ok):
        response.status_code = 401
        try:
            from backend.server.forge_logger import forge_logger  # noqa: PLC0415
            forge_logger.emit(
                "WARN", "AUTH ",
                f"login failed: username={body.username!r}",
                auth_user=body.username,
                auth_outcome="invalid_credentials",
            )
        except Exception:  # noqa: BLE001
            pass
        return {"status": "error", "detail": "Invalid credentials"}

    secret = _make_secret(expected_pass)
    token = sign_token(body.username, secret)
    try:
        from backend.server.forge_logger import forge_logger  # noqa: PLC0415
        forge_logger.emit(
            "INFO", "AUTH ",
            f"login ok: {body.username!r}",
            auth_user=body.username,
            auth_outcome="success",
        )
    except Exception:  # noqa: BLE001
        pass
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        max_age=_SESSION_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=False,  # Render terminates TLS at the proxy
    )
    return {"status": "ok"}


@router.get("/check")
async def check_auth(
    forge_session: str | None = Cookie(default=None, alias=_COOKIE_NAME),
) -> AuthStatus:
    """Return whether the user is authenticated and whether auth is required."""
    auth_required = is_auth_enabled()
    if not auth_required:
        return AuthStatus(authenticated=True, auth_required=False)

    if not forge_session:
        return AuthStatus(authenticated=False, auth_required=True)

    secret = _make_secret(os.environ.get("FORGE_AUTH_PASS", ""))
    user = verify_token(forge_session, secret)
    return AuthStatus(authenticated=user is not None, auth_required=True)


@router.post("/logout")
async def logout(response: Response) -> dict[str, str]:
    """Clear the session cookie."""
    response.delete_cookie(key=_COOKIE_NAME)
    return {"status": "ok"}
