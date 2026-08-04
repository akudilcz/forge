"""Tests for phase-level node-creation constraints."""

from __future__ import annotations

import pytest

from backend.analysis.gaps import GapType
from backend.crew.phase_constraints import (
    PHASE_CREATE_ALLOWLIST,
    check_create_allowed,
    reset_phase_constraints,
    set_phase_constraints,
)
from backend.graph.models import NodeType


def test_no_constraint_active_allows_anything() -> None:
    assert check_create_allowed("MODULE") is None
    assert check_create_allowed("ANYTHING") is None


def test_constraint_blocks_wrong_type_and_allows_correct() -> None:
    token = set_phase_constraints(GapType.UNREFINED_HLR)
    try:
        result = check_create_allowed("MODULE")
        assert result is not None
        assert "MODULE" in result
        assert "LLR" in result
        assert check_create_allowed("LLR") is None
        assert check_create_allowed("llr") is None  # case-insensitive
    finally:
        reset_phase_constraints(token)


def test_constraint_resets_after_token_reset() -> None:
    token = set_phase_constraints(GapType.UNREFINED_HLR)
    reset_phase_constraints(token)
    assert check_create_allowed("MODULE") is None


def test_quality_gaps_block_all_creation() -> None:
    for gap_type in (GapType.STALE_NODE, GapType.ORPHAN_NODE, GapType.EMPTY_CONTENT):
        token = set_phase_constraints(gap_type)
        try:
            assert check_create_allowed("RECORD") is not None, (
                f"{gap_type} should block all creation"
            )
        finally:
            reset_phase_constraints(token)


def test_allowlist_covers_all_gap_types() -> None:
    # UNSYNCED_DESIGN/TEST are handled by workspace_sync (no agent)
    # EMPTY_TRACE/CIRCULAR_TRACE are structural validations (no agent)
    programmatic = {
        GapType.UNSYNCED_DESIGN,
        GapType.UNSYNCED_TEST,
        GapType.EMPTY_TRACE,
        GapType.CIRCULAR_TRACE,
    }
    for gap_type in GapType:
        if gap_type in programmatic:
            continue
        assert gap_type in PHASE_CREATE_ALLOWLIST, f"{gap_type} missing from PHASE_CREATE_ALLOWLIST"


@pytest.mark.parametrize(
    ("gap_type", "allowed", "blocked"),
    [
        (GapType.UNDESIGNED, NodeType.DESIGN.value, NodeType.MODULE.value),
        (GapType.UNARCHITECTED, NodeType.MODULE.value, NodeType.CONTRACT.value),
    ],
)
def test_phase_specific_allowlists(gap_type: GapType, allowed: str, blocked: str) -> None:
    token = set_phase_constraints(gap_type)
    try:
        assert check_create_allowed(allowed) is None
        assert check_create_allowed(blocked) is not None
    finally:
        reset_phase_constraints(token)
