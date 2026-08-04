"""Async/sync bridge for running coroutines from synchronous tool code.

LangGraph tool methods are synchronous (``_run``), but graph operations
are async (``aiosqlite``).  LangChain's default ``_arun`` calls ``_run``
in a thread executor, so there is NO running event loop in the tool thread.

Each call opens its own ``aiosqlite.connect()`` (no shared connection),
so ``asyncio.run()`` is safe — the new event loop is short-lived and
the connection commits before the loop closes.
"""

from __future__ import annotations

import asyncio
from typing import Any


def run_async(coro: Any, *, timeout: int = 30) -> Any:
    """Run an async coroutine from a sync context.

    Creates a fresh event loop via ``asyncio.run()``.  This is safe
    because each graph operation opens its own aiosqlite connection
    and the in-memory NetworkX graph is protected by the GIL.
    """
    return asyncio.run(coro)
