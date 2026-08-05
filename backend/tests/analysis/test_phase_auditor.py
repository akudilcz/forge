"""Tests for PhaseAuditor — phase completion criteria verification."""

from unittest.mock import MagicMock

import pytest

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.analysis.phase_auditor import (
    NUM_PHASES,
    PHASE_COMPLETION_CRITERIA,
    PhaseAuditor,
    PhaseAuditResult,
)
from backend.quality.checks import QUALITY_GAP_TYPES


def _make_gap(gap_type: GapType) -> Gap:
    return Gap(
        type=gap_type,
        priority=GapPriority.DOCUMENT_STRUCTURE,
        node_id="test.node",
        description="test gap",
    )


# Types PhaseAuditor is not responsible for (quality gaps + auxiliary staleness +
# trace integrity). The prod QUALITY_GAP_TYPES is the source of truth for quality;
# the auxiliary set below covers non-quality types that still don't gate phase completion.
_NON_STRUCTURAL_GAP_TYPES = QUALITY_GAP_TYPES | {
    GapType.EMPTY_TRACE,
    GapType.CIRCULAR_TRACE,
    GapType.STALE_ARCHITECTURE,
    GapType.STALE_SUITE,
    GapType.STALE_CODE,
    GapType.MISSING_CODE,
}


@pytest.fixture
def auditor() -> PhaseAuditor:
    return PhaseAuditor()


@pytest.fixture
def empty_graph() -> MagicMock:
    graph = MagicMock()
    graph.all_nodes.return_value = []
    graph.children_sync.return_value = []
    graph.node_sync.return_value = None
    graph.any_trace_to.return_value = False
    graph.nodes_tracing_to.return_value = []
    return graph


def test_criteria_covers_structural_gap_types() -> None:
    covered: set[GapType] = set()
    for types in PHASE_COMPLETION_CRITERIA.values():
        covered |= types
    for gap_type in GapType:
        if gap_type in _NON_STRUCTURAL_GAP_TYPES:
            continue
        assert gap_type in covered, (
            f"GapType.{gap_type.name} missing from PHASE_COMPLETION_CRITERIA"
        )


def test_criteria_has_13_phases_and_human_phases_empty() -> None:
    assert len(PHASE_COMPLETION_CRITERIA) == NUM_PHASES == 14
    assert PHASE_COMPLETION_CRITERIA[0] == frozenset()
    assert PHASE_COMPLETION_CRITERIA[1] == frozenset()


def test_audit_result_to_dict() -> None:
    gap = _make_gap(GapType.UNCHUNKED_DOCUMENT)
    complete = PhaseAuditResult(
        phase=2, is_complete=True, unresolved_gaps=[], blocking_gap_types=frozenset()
    )
    incomplete = PhaseAuditResult(
        phase=2,
        is_complete=False,
        unresolved_gaps=[gap],
        blocking_gap_types=frozenset({GapType.UNCHUNKED_DOCUMENT}),
    )
    d = complete.to_dict()
    assert d["is_complete"] is True
    assert d["gap_count"] == 0
    d2 = incomplete.to_dict()
    assert d2["is_complete"] is False
    assert d2["gap_count"] == 1
    assert "UNCHUNKED_DOCUMENT" in d2["blocking_gap_types"]


def test_audit_phase_complete_when_no_gaps(auditor: PhaseAuditor, empty_graph: MagicMock) -> None:
    result = auditor.audit(2, empty_graph)
    assert result.is_complete is True
    assert result.unresolved_gaps == []


def test_audit_phase_incomplete_when_doc_gap_present(auditor: PhaseAuditor) -> None:
    from backend.graph.models import GraphNode, NodeType

    doc_node = GraphNode(
        node_id="proj.doc.whitepaper",
        node_type=NodeType.DOCUMENT.value,
        title="Whitepaper",
        content="content",
    )
    graph = MagicMock()
    graph.all_nodes.return_value = [doc_node]
    graph.children_sync.return_value = []
    graph.node_sync.return_value = None
    graph.any_trace_to.return_value = False
    graph.nodes_tracing_to.return_value = []

    result = auditor.audit(2, graph)
    assert result.is_complete is False
    assert GapType.UNCHUNKED_DOCUMENT in {g.type for g in result.unresolved_gaps}


def test_audit_human_phases_always_complete(
    auditor: PhaseAuditor, empty_graph: MagicMock
) -> None:
    assert auditor.audit(0, empty_graph).is_complete is True
    assert auditor.audit(1, empty_graph).is_complete is True


def test_audit_lifecycle_returns_all_phases_complete_on_empty_graph(
    auditor: PhaseAuditor, empty_graph: MagicMock
) -> None:
    results = auditor.audit_lifecycle(empty_graph)
    assert set(results.keys()) == set(range(NUM_PHASES))
    for phase, result in results.items():
        assert isinstance(result, PhaseAuditResult)
        assert result.phase == phase
        assert result.is_complete is True
