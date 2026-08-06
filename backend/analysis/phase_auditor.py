"""PhaseAuditor — deterministic verification of phase completion criteria.

Each phase has a set of gap types that must be absent before it is truly
complete. The auditor runs the Gap Analyser and checks that every required
gap type is absent from the graph — both for the current phase and all
prior phases (regression check).

Design reference: specs/12-artifact-model-and-traceability.md §8.4
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.analysis.gap_analyser import GapAnalyser
from backend.analysis.gaps import Gap, GapType

# Gap types that must be absent for each phase to be considered complete.
# Phases 0–1 are human-initiated; no agent-resolvable gaps belong to them.
PHASE_COMPLETION_CRITERIA: dict[int, frozenset[GapType]] = {
    0: frozenset(),
    1: frozenset(),
    2: frozenset({GapType.UNCHUNKED_DOCUMENT}),
    3: frozenset({GapType.UNCOVERED_PARA}),
    4: frozenset({GapType.UNARCHITECTED}),
    5: frozenset({GapType.UNMODULARISED}),
    6: frozenset({GapType.UNCONTRACTED}),
    7: frozenset({GapType.UNREFINED_HLR}),
    8: frozenset({GapType.UNDESIGNED}),
    9: frozenset({GapType.UNSUITED}),
    10: frozenset({GapType.UNTESTED_HLR, GapType.UNTESTED_LLR}),
    11: frozenset(),  # Doco Render (no gap — deterministic render)
    12: frozenset(),  # Code Gen (LangGraph agents)
    13: frozenset({GapType.UNSYNCED_DESIGN, GapType.UNSYNCED_TEST}),
}

NUM_PHASES = 14  # phases 0-13


@dataclass
class PhaseAuditResult:
    """Result of auditing one phase."""

    phase: int
    is_complete: bool
    unresolved_gaps: list[Gap] = field(default_factory=list)
    blocking_gap_types: frozenset[GapType] = field(default_factory=frozenset)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the audit result to a JSON-safe dict for API responses."""
        return {
            "phase": self.phase,
            "is_complete": self.is_complete,
            "gap_count": len(self.unresolved_gaps),
            "blocking_gap_types": [g.value for g in self.blocking_gap_types],
        }


class PhaseAuditor:
    """Audits phase completion by running the Gap Analyser and checking criteria.

    The audit is *cumulative*: phase N's audit requires that all phases 0..N
    are clean (no regressions from prior phases).
    """

    def __init__(self) -> None:
        self._analyser = GapAnalyser()

    def audit(self, phase: int, graph: Any) -> PhaseAuditResult:
        """Audit a single phase: check this phase + all prior phases are clean."""
        required_absent = self._required_absent_for(phase)
        all_gaps = self._analyser.analyse(graph)
        blocking = [g for g in all_gaps if g.type in required_absent]
        result = PhaseAuditResult(
            phase=phase,
            is_complete=len(blocking) == 0,
            unresolved_gaps=blocking,
            blocking_gap_types=frozenset(g.type for g in blocking),
        )
        try:
            from backend.server.forge_logger import forge_logger  # noqa: PLC0415

            forge_logger.emit(
                "INFO" if result.is_complete else "WARN",
                "AUDIT",
                f"phase {phase}: "
                f"{'complete' if result.is_complete else 'incomplete'} "
                f"({len(blocking)} blocking gap(s))",
                phase=phase,
                is_complete=result.is_complete,
                blocking_count=len(blocking),
                blocking_types=[g.type.value for g in blocking],
            )
        except Exception:  # noqa: BLE001
            pass
        return result

    def audit_lifecycle(self, graph: Any) -> dict[int, PhaseAuditResult]:
        """Audit all 13 phases in a single graph scan.

        Runs the Gap Analyser once and distributes results across phases.
        """
        all_gaps = self._analyser.analyse(graph)
        results: dict[int, PhaseAuditResult] = {}

        for phase in range(NUM_PHASES):
            required_absent = self._required_absent_for(phase)
            blocking = [g for g in all_gaps if g.type in required_absent]
            results[phase] = PhaseAuditResult(
                phase=phase,
                is_complete=len(blocking) == 0,
                unresolved_gaps=blocking,
                blocking_gap_types=frozenset(g.type for g in blocking),
            )

        return results

    def _required_absent_for(self, phase: int) -> frozenset[GapType]:
        """Collect all gap types that must be absent for phases 0..phase."""
        required: frozenset[GapType] = frozenset()
        for p in range(min(phase, NUM_PHASES - 1) + 1):
            required |= PHASE_COMPLETION_CRITERIA.get(p, frozenset())
        return required
