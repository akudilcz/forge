"""Secrets router — manage API keys stored in the project database.

Keys are persisted in a ``secrets`` JSON blob in the settings table and
injected into ``os.environ`` on startup and whenever they are updated.
"""

from __future__ import annotations

import json
import os
import sqlite3

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(prefix="/secrets", tags=["secrets"])

# Well-known API key names and their descriptions
_KNOWN_KEYS = [
    {"name": "POE_API_KEY", "label": "Poe"},
    {"name": "OPENROUTER_API_KEY", "label": "OpenRouter"},
]


class SecretEntry(BaseModel):
    name: str
    label: str
    is_set: bool


class SetSecretRequest(BaseModel):
    name: str
    value: str


def _db_path_from_request(request: Request) -> str:
    return getattr(request.app.state, "db_path", "")


def _load_secrets(db_path: str) -> dict[str, str]:
    """Load the secrets dict from the settings table."""
    if not db_path:
        return {}
    try:
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'secrets'"
        ).fetchone()
        conn.close()
        if row:
            stored: dict[str, str] = json.loads(row[0])
            return stored
    except Exception:  # noqa: BLE001
        pass
    return {}


def _save_secrets(db_path: str, secrets: dict[str, str]) -> None:
    """Persist the secrets dict to the settings table."""
    if not db_path:
        return
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('secrets', ?)",
        (json.dumps(secrets),),
    )
    conn.commit()
    conn.close()


def inject_secrets_into_env(db_path: str) -> None:
    """Load stored secrets and set them as environment variables.

    Called at startup so LLM providers can find their API keys.
    """
    for name, value in _load_secrets(db_path).items():
        if value:
            os.environ[name] = value


def _rebuild_agent_pool(request: Request) -> None:
    """Rebuild the agent pool so agents pick up new API keys."""
    pool = getattr(request.app.state, "agent_pool", None)
    if pool is not None:
        pool.rebuild()


@router.get("", response_model=list[SecretEntry])
async def list_secrets(request: Request) -> list[SecretEntry]:
    """Return the list of known API keys and whether each is set."""
    db_path = _db_path_from_request(request)
    stored = _load_secrets(db_path)
    result: list[SecretEntry] = []
    for key_info in _KNOWN_KEYS:
        name = key_info["name"]
        is_set = bool(stored.get(name) or os.environ.get(name))
        result.append(SecretEntry(name=name, label=key_info["label"], is_set=is_set))
    return result


@router.post("")
async def set_secret(body: SetSecretRequest, request: Request) -> dict[str, str]:
    """Store an API key and inject it into the environment."""
    db_path = _db_path_from_request(request)
    stored = _load_secrets(db_path)
    stored[body.name] = body.value
    _save_secrets(db_path, stored)

    # Inject into current process
    if body.value:
        os.environ[body.name] = body.value
    elif body.name in os.environ:
        del os.environ[body.name]

    # Rebuild agents so they pick up the new key immediately
    _rebuild_agent_pool(request)

    return {"status": "ok"}


@router.delete("/{name}")
async def delete_secret(name: str, request: Request) -> dict[str, str]:
    """Remove a stored API key."""
    db_path = _db_path_from_request(request)
    stored = _load_secrets(db_path)
    stored.pop(name, None)
    _save_secrets(db_path, stored)

    os.environ.pop(name, None)
    _rebuild_agent_pool(request)
    return {"status": "ok"}
