"""Gap Analyser: The deterministic brain of the FORGE system.

This module implements the pure function `analyse(graph) -> list[Gap]`.
It inspects the Project Graph for structural holes and integrity violations.
All graph access uses synchronous in-memory methods for speed.
"""

from __future__ import annotations

from typing import Any

from backend.analysis.gap_analyser_integrity import (
    VALID_PARENT_TYPES as VALID_PARENT_TYPES,
)
from backend.analysis.gap_analyser_integrity import NodeIntegrityChecks
from backend.analysis.gap_analyser_staleness import CorpusStalenessChecks
from backend.analysis.gap_analyser_structure import StructuralCompletenessChecks
from backend.analysis.gaps import Gap


def _log_summary(gaps: list[Gap]) -> None:
    """Emit one structured record per gap-type count for this analyse() run."""
    try:
        from backend.server.forge_logger import forge_logger  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return
    if not gaps:
        forge_logger.emit("INFO", "GAP  ", "analyse: 0 gaps", gap_total=0)
        return
    counts: dict[str, int] = {}
    for g in gaps:
        counts[g.type.value] = counts.get(g.type.value, 0) + 1
    summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    forge_logger.emit(
        "INFO", "GAP  ",
        f"analyse: {len(gaps)} gaps — {summary}",
        gap_total=len(gaps),
        counts=counts,
    )


class GapAnalyser(
    StructuralCompletenessChecks,
    NodeIntegrityChecks,
    CorpusStalenessChecks,
):
    """Detects gaps in the Project Graph using synchronous in-memory access."""

    def analyse(self, graph: Any) -> list[Gap]:
        """Run the full Gap Analysis on the given graph.

        Args:
            graph: The ProjectGraph instance (must support .all_nodes(),
                   .children_sync(), .node_sync()).

        Returns:
            A list of detected Gaps, sorted by priority and node ID.
        """
        all_nodes = graph.all_nodes()
        gaps: list[Gap] = []
        for node in all_nodes:
            gaps.extend(self._check_structural_completeness(graph, node))
            gaps.extend(self._check_staleness(graph, node))
            gaps.extend(self._check_integrity(graph, node))

        gaps.extend(self._check_duplicate_siblings(all_nodes))
        gaps.extend(self._check_sibling_title_duplicates(all_nodes))
        gaps.extend(self._check_empty_traces(all_nodes))
        gaps.extend(self._check_circular_traces(graph, all_nodes))
        gaps.extend(self._check_inadequate_content(all_nodes))
        gaps.extend(self._check_stale_architecture(all_nodes))
        gaps.extend(self._check_stale_suite(all_nodes))
        gaps.extend(self._check_stale_code(all_nodes))
        gaps.extend(self._check_design_contract_alignment(graph, all_nodes))

        # Sort by Priority (ASC) then Node ID (ASC) for deterministic order
        sorted_gaps = sorted(gaps, key=lambda g: (g.priority, g.node_id))
        _log_summary(sorted_gaps)
        return sorted_gaps

