"""Tests for the secrets router — API key management."""

import os
import sqlite3
from pathlib import Path

import pytest

from backend.server.routers.secrets import (
    _load_secrets,
    _save_secrets,
    inject_secrets_into_env,
)


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """Create a temporary SQLite DB with a settings table."""
    path = str(tmp_path / "test.db")
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()
    return path


class TestLoadAndSaveSecrets:
    def test_empty_when_no_secrets(self, db_path: str) -> None:
        assert _load_secrets(db_path) == {}

    def test_roundtrip(self, db_path: str) -> None:
        secrets = {"ANTHROPIC_API_KEY": "sk-test-123", "OPENAI_API_KEY": "sk-openai"}
        _save_secrets(db_path, secrets)
        loaded = _load_secrets(db_path)
        assert loaded == secrets

    def test_update_existing(self, db_path: str) -> None:
        _save_secrets(db_path, {"KEY1": "val1"})
        _save_secrets(db_path, {"KEY1": "val2", "KEY2": "val3"})
        loaded = _load_secrets(db_path)
        assert loaded == {"KEY1": "val2", "KEY2": "val3"}

    def test_empty_db_path(self) -> None:
        assert _load_secrets("") == {}


class TestInjectSecretsIntoEnv:
    def test_injects_keys(self, db_path: str) -> None:
        _save_secrets(db_path, {"TEST_INJECT_KEY": "test-value-123"})
        try:
            inject_secrets_into_env(db_path)
            assert os.environ.get("TEST_INJECT_KEY") == "test-value-123"
        finally:
            os.environ.pop("TEST_INJECT_KEY", None)

    def test_skips_empty_values(self, db_path: str) -> None:
        _save_secrets(db_path, {"TEST_EMPTY_KEY": ""})
        os.environ.pop("TEST_EMPTY_KEY", None)
        inject_secrets_into_env(db_path)
        assert "TEST_EMPTY_KEY" not in os.environ

    def test_handles_missing_db(self) -> None:
        # Should not raise
        inject_secrets_into_env("/nonexistent/path.db")
