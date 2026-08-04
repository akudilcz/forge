"""Tests for backend.agents.throttle — global LLM call throttle."""

import asyncio
import time

import pytest

from backend.agents.throttle import LLMThrottle


@pytest.mark.asyncio
async def test_throttle_enforces_minimum_delay() -> None:
    """Two rapid calls should be spaced by at least delay_ms."""
    throttle = LLMThrottle(delay_ms=100)
    await throttle.wait()
    t0 = time.monotonic()
    await throttle.wait()
    elapsed_ms = (time.monotonic() - t0) * 1000
    assert elapsed_ms >= 90  # allow 10ms tolerance


@pytest.mark.asyncio
async def test_throttle_no_delay_when_enough_time_elapsed() -> None:
    """No extra delay if enough time has already passed."""
    throttle = LLMThrottle(delay_ms=50)
    await throttle.wait()
    await asyncio.sleep(0.06)  # 60ms > 50ms
    t0 = time.monotonic()
    await throttle.wait()
    elapsed_ms = (time.monotonic() - t0) * 1000
    assert elapsed_ms < 30  # should be near-instant


@pytest.mark.asyncio
async def test_throttle_zero_delay() -> None:
    """Zero delay means no waiting."""
    throttle = LLMThrottle(delay_ms=0)
    await throttle.wait()
    t0 = time.monotonic()
    await throttle.wait()
    elapsed_ms = (time.monotonic() - t0) * 1000
    assert elapsed_ms < 20


@pytest.mark.asyncio
async def test_throttle_delay_setter() -> None:
    """delay_ms property can be updated at runtime."""
    throttle = LLMThrottle(delay_ms=500)
    assert throttle.delay_ms == 500
    throttle.delay_ms = 100
    assert throttle.delay_ms == 100
    throttle.delay_ms = -10
    assert throttle.delay_ms == 0  # clamped to 0
