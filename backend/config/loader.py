"""Config loader — reads and writes ForgeConfig from the project SQLite database."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from backend.config.models import ForgeConfig

_SETTINGS_KEY = "forge"


def load_config(db_path: Path | str | None = None) -> ForgeConfig:
    """Load configuration from the *settings* table in *db_path*.

    Falls back to defaults when *db_path* is ``None``, the file does not exist,
    or no settings row is found.
    """
    if db_path is None:
        return ForgeConfig()
    path = Path(db_path)
    if not path.exists():
        return ForgeConfig()
    try:
        with sqlite3.connect(str(path)) as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key=?", (_SETTINGS_KEY,)
            ).fetchone()
        if row is None:
            return ForgeConfig()
        data: dict[str, Any] = json.loads(row[0])
        return ForgeConfig.model_validate(data)
    except Exception:  # noqa: BLE001
        return ForgeConfig()


def save_config(config: ForgeConfig, db_path: Path | str) -> None:
    """Serialise *config* to JSON and persist in the *settings* table of *db_path*."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(config.model_dump(mode="json"))
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS settings "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (_SETTINGS_KEY, data),
        )
        conn.commit()
