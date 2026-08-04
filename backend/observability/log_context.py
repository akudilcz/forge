"""Correlation context for structured logging.

Every log record is auto-annotated with whatever is in the current
``log_context``. Typical push points:

    with log_context(run_id=run_id):
        with log_context(phase=3):
            with log_context(cycle=2):
                with log_context(gap_type="UNCOVERED_PARA", node_id="PARA-0001"):
                    forge_logger.emit("INFO", "CREW ", "dispatching")
                    # → record has run_id, phase, cycle, gap_type, node_id set

The context is stored in a ``ContextVar`` so it propagates across
``asyncio.Task`` boundaries automatically. For thread-pool work, callers
should wrap submission with ``contextvars.copy_context().run(...)``; the
``run_with_context`` helper below does this.
"""

from __future__ import annotations

import contextvars
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

# No default: readers pass an explicit empty mapping, so no single mutable
# dict is ever shared across contexts.
_ctx: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar("forge_log_ctx")


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    """Push correlation fields on the stack for the duration of the block.

    Nested contexts merge shallowly — inner fields override outer fields
    of the same name.
    """
    current = dict(_ctx.get({}))
    for k, v in fields.items():
        if v is None:
            continue
        current[k] = v
    token = _ctx.set(current)
    try:
        yield
    finally:
        _ctx.reset(token)


def current_context() -> dict[str, Any]:
    """Return a copy of the active correlation fields."""
    return dict(_ctx.get({}))


def new_run_id() -> str:
    """Generate a fresh correlation ID for a build run."""
    return f"run-{uuid.uuid4().hex[:12]}"


def new_call_id() -> str:
    """Generate a fresh correlation ID for one LLM call."""
    return f"call-{uuid.uuid4().hex[:12]}"


def run_with_context[T](fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run ``fn(*args, **kwargs)`` with the current context snapshot.

    Intended for passing callables to ``loop.run_in_executor`` so the
    correlation fields propagate into thread-pool workers.
    """
    ctx = contextvars.copy_context()
    return ctx.run(fn, *args, **kwargs)
