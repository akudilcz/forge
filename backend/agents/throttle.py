"""Global LLM call throttle — enforces a minimum delay between API calls.

All code paths that invoke LLM agents should call ``await llm_throttle.wait()``
before dispatching.  The delay is configured via ``config.llm.call_delay_ms``.
"""

from __future__ import annotations

import asyncio
import time


class LLMThrottle:
    """Async-safe global throttle gate for LLM API calls."""

    def __init__(self, delay_ms: int = 400) -> None:
        self._delay_ms = delay_ms
        self._last_call: float = 0.0
        self._lock = asyncio.Lock()

    @property
    def delay_ms(self) -> int:
        return self._delay_ms

    @delay_ms.setter
    def delay_ms(self, value: int) -> None:
        self._delay_ms = max(0, value)

    async def wait(self) -> None:
        """Block until the minimum delay has elapsed since the last call."""
        async with self._lock:
            waited_ms = 0
            if self._delay_ms > 0 and self._last_call > 0:
                elapsed_ms = (time.monotonic() - self._last_call) * 1000
                remaining = self._delay_ms - elapsed_ms
                if remaining > 0:
                    await asyncio.sleep(remaining / 1000)
                    waited_ms = int(remaining)
            self._last_call = time.monotonic()
        if waited_ms > 0:
            try:
                from backend.server.forge_logger import forge_logger  # noqa: PLC0415

                forge_logger.emit(
                    "INFO", "THROT",
                    f"throttled {waited_ms}ms",
                    duration_ms=waited_ms,
                )
            except Exception:  # noqa: BLE001
                pass


# Singleton — import and use directly.
llm_throttle = LLMThrottle()
