"""ForgeFlow run state — the runtime state model, the structural-gap phase
mapping, and the single-step control-flow signal.

These are defined here and re-exported by :mod:`backend.pipeline.flow`, which
remains the public facade (external code imports them from ``flow``).

Design reference: specs/12-artifact-model-and-traceability.md
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from backend.analysis.gaps import GapType

# Canonical phase mapping for structural gaps.
GAP_TYPE_TO_PHASE: dict[GapType, int] = {
    GapType.UNCHUNKED_DOCUMENT: 2,
    GapType.UNCOVERED_PARA: 3,
    GapType.UNARCHITECTED: 4,
    GapType.UNMODULARISED: 5,
    GapType.UNCONTRACTED: 6,
    GapType.UNREFINED_HLR: 7,
    GapType.UNDESIGNED: 8,
    GapType.UNSUITED: 9,
    GapType.UNTESTED_HLR: 10,
    GapType.UNTESTED_LLR: 10,
    # UNSYNCED_DESIGN / UNSYNCED_TEST: handled by workspace_sync step (no agent)
}


class _SingleStepDone(Exception):  # noqa: N818 — control-flow signal, not an error
    """Raised when single_step=True and one gap has been resolved."""


class ForgeFlowState(BaseModel):
    """Mutable runtime state of a single ForgeFlow run."""

    session_id: str = ""
    active_agents: list[str] = Field(default_factory=list)
    start_phase: int = 0
    end_phase: int = 14
    current_phase: int = 0
    loop_status: str = "idle"
    iteration: int = 0
    single_step: bool = False
    error: str | None = None
    run_id: str | None = None
