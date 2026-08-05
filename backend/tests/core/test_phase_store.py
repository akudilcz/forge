"""Tests for PhaseStore."""

import os
import tempfile
from collections.abc import Iterator
from typing import Any

import pytest

from backend.core.phase_store import PhaseStore


@pytest.fixture
def store() -> Iterator[PhaseStore]:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db = f.name
    ps = PhaseStore(db)
    yield ps
    os.unlink(db)


def _status_of(store: PhaseStore, phase: int) -> Any:
    phase_state = store.get(phase)
    assert phase_state is not None
    return phase_state["status"]


def test_get_all_returns_all_phases_pending(store: PhaseStore) -> None:
    phases = store.get_all()
    assert len(phases) == 15
    assert all(p["status"] == "pending" for p in phases)


def test_get_and_get_missing(store: PhaseStore) -> None:
    phase = store.get(0)
    assert phase is not None
    assert phase["phase_number"] == 0
    assert store.get(99) is None


def test_set_status_valid_and_invalid(store: PhaseStore) -> None:
    store.set_status(0, "active")
    assert _status_of(store, 0) == "active"
    with pytest.raises(ValueError, match="Invalid status 'invalid_status'"):
        store.set_status(0, "invalid_status")


def test_reset_all(store: PhaseStore) -> None:
    store.set_status(0, "complete")
    store.set_status(1, "active")
    store.reset_all()
    assert all(p["status"] == "pending" for p in store.get_all())


def test_reset_active_to_pending_preserves_complete(store: PhaseStore) -> None:
    store.set_status(0, "active")
    store.set_status(1, "complete")
    store.reset_active_to_pending()
    assert _status_of(store, 0) == "pending"
    assert _status_of(store, 1) == "complete"
