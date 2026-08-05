"""SQLite-backed LangChain response cache for local LLM call caching.

Stores serialized ``Generation`` lists keyed by ``(prompt, llm_string)`` in a
single SQLite file (``<llm.cache_dir>/llm_cache.db``). The cache is passed
per-model via the ``cache=`` constructor parameter in ``build_llm`` — never
installed globally via ``set_llm_cache`` — so cache participation is explicit
at every construction site.

Only non-streaming ``.invoke``/``.ainvoke`` calls consult the cache
(langchain-core routes them through ``_generate_with_cache`` /
``_agenerate_with_cache``); agent streaming paths bypass it entirely.
See design/01_architecture.md §7.4, including the independence exemption for
the semantic duplicate checker.
"""
from __future__ import annotations

import sqlite3
import warnings
from pathlib import Path
from typing import Any

from langchain_core.caches import RETURN_VAL_TYPE, BaseCache
from langchain_core.load import dumps, loads

#: Repo root, derived from this file's location (backend/agents/llm_cache.py)
#: — stable regardless of the process cwd.
_REPO_ROOT = Path(__file__).resolve().parents[2]

_TABLE_DDL = (
    "CREATE TABLE IF NOT EXISTS llm_cache ("
    "  prompt TEXT NOT NULL,"
    "  llm TEXT NOT NULL,"
    "  response TEXT NOT NULL,"
    "  PRIMARY KEY (prompt, llm)"
    ")"
)


def resolve_cache_db_path(cache_dir: str) -> Path:
    """Return the absolute path of the cache DB for ``llm.cache_dir``.

    An absolute *cache_dir* is used as-is. A relative one resolves against
    the **repo root** (the directory containing ``backend/``), never the
    process cwd — integration tests chdir into throwaway per-test
    workspaces, and a cwd-relative cache would be cold on every run.
    """
    directory = Path(cache_dir)
    if not directory.is_absolute():
        directory = _REPO_ROOT / directory
    return directory / "llm_cache.db"


class SQLiteLLMCache(BaseCache):
    """LangChain ``BaseCache`` backed by a single local SQLite file.

    The parent directory and DB file are created lazily on first use, so
    constructing the cache (e.g. inside ``build_llm``) performs no I/O.
    Connections are opened per operation: cheap for a local file and safe
    across the executor threads langchain-core uses for the async cache
    hooks (``alookup``/``aupdate`` delegate to the sync methods).
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    @property
    def db_path(self) -> Path:
        """Location of the SQLite cache file."""
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        """Open a connection, creating the parent directory and schema."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.execute(_TABLE_DDL)
        return conn

    def lookup(self, prompt: str, llm_string: str) -> RETURN_VAL_TYPE | None:
        """Return the cached generations for the key, or None on a miss."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT response FROM llm_cache WHERE prompt = ? AND llm = ?",
                (prompt, llm_string),
            ).fetchone()
        if row is None:
            return None
        with warnings.catch_warnings():
            # langchain_core.load.loads is marked beta; the payload is our
            # own trusted serialization of core Generation objects.
            warnings.simplefilter("ignore")
            return loads(row[0], allowed_objects="core")

    def update(self, prompt: str, llm_string: str, return_val: RETURN_VAL_TYPE) -> None:
        """Store the generations for the key, replacing any previous entry."""
        payload = dumps(list(return_val))
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO llm_cache (prompt, llm, response) VALUES (?, ?, ?)",
                (prompt, llm_string, payload),
            )

    def clear(self, **kwargs: Any) -> None:
        """Delete every cached entry."""
        with self._connect() as conn:
            conn.execute("DELETE FROM llm_cache")
