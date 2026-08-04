"""Gap analyser v4 tests — workspace-synced model.

Tests for the node types (HLR, LLR, ARCHITECTURE, DESIGN) and gap types
(UNARCHITECTED, UNMODULARISED, UNCONTRACTED, UNDESIGNED,
UNTESTED_HLR, UNTESTED_LLR, UNSYNCED_DESIGN, UNSYNCED_TEST).

Priority chain: UNARCHITECTED(P3) → UNMODULARISED(P4) → UNCONTRACTED(P5)
                → UNREFINED_HLR(P6) → UNDESIGNED(P7)
                → UNTESTED_HLR(P8) → UNTESTED_LLR(P9) → UNSYNCED_DESIGN(P10)
"""

from unittest.mock import MagicMock

import pytest

from backend.analysis.gap_analyser import GapAnalyser
from backend.analysis.gaps import GapPriority, GapType
from backend.graph.models import GraphNode, NodeType

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def analyser() -> GapAnalyser:
    return GapAnalyser()


def _graph(
    nodes: list[GraphNode],
    children_map: dict[str, list[GraphNode]] | None = None,
    node_map: dict[str, GraphNode] | None = None,
) -> MagicMock:
    g = MagicMock()
    g.all_nodes.return_value = nodes
    g.children_sync.side_effect = lambda nid: (children_map or {}).get(nid, [])
    g.node_sync.side_effect = lambda nid: (node_map or {}).get(nid)
    g.any_trace_to.return_value = False
    g.nodes_tracing_to.return_value = []
    return g


# ── Node constructors ─────────────────────────────────────────────────────────


def _project(node_id: str = "proj.test") -> GraphNode:
    return GraphNode(node_id=node_id, node_type=NodeType.PROJECT.value,
                     title="Project", content="")


def _architecture(node_id: str = "proj.test.arch.v1") -> GraphNode:
    return GraphNode(node_id=node_id, node_type=NodeType.ARCHITECTURE.value,
                     title="Architecture", content="System arch")


def _hlr(node_id: str = "proj.test.doc.spec.par.001.hlr.001") -> GraphNode:
    return GraphNode(node_id=node_id, node_type=NodeType.HLR.value,
                     title="HLR", content="System shall sort")


def _llr(node_id: str = "proj.test.doc.spec.par.001.hlr.001.llr.001") -> GraphNode:
    return GraphNode(node_id=node_id, node_type=NodeType.LLR.value,
                     title="LLR", content="Shall sort ascending")


def _module(node_id: str = "proj.test.arch.v1.mod.sorter") -> GraphNode:
    return GraphNode(node_id=node_id, node_type=NodeType.MODULE.value,
                     title="Sorter", content="Sorting module")


def _contract(node_id: str = "proj.test.arch.v1.mod.sorter.ctr.api") -> GraphNode:
    return GraphNode(node_id=node_id, node_type=NodeType.CONTRACT.value,
                     title="Contract", content="sort(list) -> list")


def _design(node_id: str = "proj.test.arch.v1.mod.sorter.design.bubble") -> GraphNode:
    return GraphNode(node_id=node_id, node_type=NodeType.DESIGN.value,
                     title="BubbleSort Design", content="class BubbleSort: sort(items: list) -> list")


def _suite(node_id: str = "proj.test.suite.main") -> GraphNode:
    return GraphNode(node_id=node_id, node_type=NodeType.SUITE.value,
                     title="Test Suite", content="")


def _case_hlr(node_id: str = "proj.test.suite.sys.case.001") -> GraphNode:
    return GraphNode(node_id=node_id, node_type=NodeType.CASE_HLR.value,
                     title="HLR Test", content="test sorting")


def _case_llr(node_id: str = "proj.test.suite.unit.case.001") -> GraphNode:
    return GraphNode(node_id=node_id, node_type=NodeType.CASE_LLR.value,
                     title="LLR Test", content="test sort ascending")


# ── Priority order assertions ─────────────────────────────────────────────────


@pytest.mark.parametrize(("lower", "higher"), [
    (GapPriority.ARCHITECTURE, GapPriority.MODULARISATION),
    (GapPriority.MODULARISATION, GapPriority.CONTRACT_DESIGN),
    (GapPriority.CONTRACT_DESIGN, GapPriority.REQUIREMENTS_LLR),
    (GapPriority.REQUIREMENTS_LLR, GapPriority.DESIGN),
    (GapPriority.TEST_HLR, GapPriority.TEST_LLR),
    (GapPriority.TEST_LLR, GapPriority.CODE_SYNC),
], ids=[
    "architecture_before_modularisation",
    "modularisation_before_contract",
    "contract_before_llr_elaboration",
    "llr_elaboration_before_design",
    "untested_hlr_before_untested_llr",
    "untested_llr_before_code_sync",
])
def test_priority_order(lower: GapPriority, higher: GapPriority) -> None:
    """Each priority level fires before the next in the chain."""
    assert lower < higher


# ── UNARCHITECTED ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("has_arch", "expect_gap"), [
    (False, True),
    (True, False),
], ids=["fires_when_no_architecture", "not_fired_when_architecture_exists"])
def test_unarchitected(analyser: GapAnalyser, has_arch: bool, expect_gap: bool) -> None:
    """UNARCHITECTED fires when PROJECT has no ARCHITECTURE child."""
    proj = _project()
    if has_arch:
        arch = _architecture()
        g = _graph([proj], children_map={"proj.test": [arch]})
    else:
        g = _graph([proj])

    gaps = analyser.analyse(g)
    gap_types = [gap.type for gap in gaps]

    if expect_gap:
        assert GapType.UNARCHITECTED in gap_types
        arch_gaps = [gap for gap in gaps if gap.type == GapType.UNARCHITECTED]
        assert arch_gaps[0].node_id == "proj.test"
    else:
        assert GapType.UNARCHITECTED not in gap_types


# ── UNMODULARISED ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("module_traces", "expect_gap"), [
    (False, True),
    (True, False),
], ids=["fires_when_no_module_trace", "not_fired_when_module_traces"])
def test_unmodularised(analyser: GapAnalyser, module_traces: bool, expect_gap: bool) -> None:
    """UNMODULARISED fires when no MODULE traces to HLR."""
    hlr = _hlr()
    g = _graph([hlr])
    g.any_trace_to.side_effect = lambda nid, source_type=None: (
        module_traces and source_type == NodeType.MODULE.value
    )

    gaps = analyser.analyse(g)
    gap_types = [gap.type for gap in gaps]

    if expect_gap:
        assert GapType.UNMODULARISED in gap_types
        mod_gaps = [gap for gap in gaps if gap.type == GapType.UNMODULARISED]
        assert mod_gaps[0].node_id == hlr.node_id
    else:
        assert GapType.UNMODULARISED not in gap_types


# ── UNCONTRACTED ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("has_contract", "expect_gap"), [
    (False, True),
    (True, False),
], ids=["fires_when_no_contract", "not_fired_when_contract_exists"])
def test_uncontracted(analyser: GapAnalyser, has_contract: bool, expect_gap: bool) -> None:
    """UNCONTRACTED fires when MODULE has no CONTRACT child."""
    mod = _module()
    if has_contract:
        ctr = _contract()
        g = _graph([mod], children_map={mod.node_id: [ctr]})
    else:
        g = _graph([mod])

    gaps = analyser.analyse(g)
    gap_types = [gap.type for gap in gaps]

    if expect_gap:
        assert GapType.UNCONTRACTED in gap_types
        ctr_gaps = [gap for gap in gaps if gap.type == GapType.UNCONTRACTED]
        assert ctr_gaps[0].node_id == mod.node_id
    else:
        assert GapType.UNCONTRACTED not in gap_types


# ── UNREFINED_HLR ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("has_llr", "expect_gap"), [
    (False, True),
    (True, False),
], ids=["fires_when_no_llr_children", "not_fired_when_llr_children_exist"])
def test_unrefined_hlr(analyser: GapAnalyser, has_llr: bool, expect_gap: bool) -> None:
    """UNREFINED_HLR fires when HLR has no LLR children."""
    hlr = _hlr()
    if has_llr:
        llr = _llr()
        g = _graph([hlr], children_map={hlr.node_id: [llr]})
    else:
        g = _graph([hlr])

    gaps = analyser.analyse(g)
    assert (GapType.UNREFINED_HLR in [gap.type for gap in gaps]) == expect_gap


# ── UNDESIGNED ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("design_traces", "expect_gap"), [
    (False, True),
    (True, False),
], ids=["fires_when_no_design_trace", "not_fired_when_design_traces"])
def test_undesigned(analyser: GapAnalyser, design_traces: bool, expect_gap: bool) -> None:
    """UNDESIGNED fires when no DESIGN traces to LLR."""
    llr = _llr()
    g = _graph([llr])
    g.any_trace_to.side_effect = lambda nid, source_type=None: (
        design_traces and source_type == NodeType.DESIGN.value
    )

    gaps = analyser.analyse(g)
    gap_types = [gap.type for gap in gaps]

    if expect_gap:
        assert GapType.UNDESIGNED in gap_types
        dsn_gaps = [gap for gap in gaps if gap.type == GapType.UNDESIGNED]
        assert dsn_gaps[0].node_id == llr.node_id
    else:
        assert GapType.UNDESIGNED not in gap_types


# ── UNTESTED_HLR ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("case_type", "expect_gap"), [
    (None, True),               # no cases at all
    ("CASE_HLR", False),        # CASE_HLR satisfies
    ("CASE_LLR", True),         # CASE_LLR does not satisfy HLR
], ids=["no_cases", "case_hlr_satisfies", "case_llr_does_not_satisfy"])
def test_untested_hlr(analyser: GapAnalyser, case_type: str | None, expect_gap: bool) -> None:
    """UNTESTED_HLR fires unless a CASE_HLR traces to the HLR."""
    hlr = _hlr()

    if case_type is None:
        g = _graph([hlr])
    else:
        case = GraphNode(node_id="proj.test.suite.sys.case.001",
                         node_type=case_type,
                         title="Test", content="test")
        node_map = {case.node_id: case}
        g = _graph([hlr], node_map=node_map)
        g.nodes_tracing_to.side_effect = lambda nid, source_type=None: (
            [case.node_id] if source_type == case_type else []
        )
        g.node_sync.side_effect = lambda nid: node_map.get(nid)

    gaps = analyser.analyse(g)
    assert (GapType.UNTESTED_HLR in [gap.type for gap in gaps]) == expect_gap


# ── UNTESTED_LLR ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("case_type", "expect_gap"), [
    (None, True),               # no cases at all
    ("CASE_LLR", False),        # CASE_LLR satisfies
    ("CASE_HLR", True),         # CASE_HLR does not satisfy LLR
], ids=["no_cases", "case_llr_satisfies", "case_hlr_does_not_satisfy"])
def test_untested_llr(analyser: GapAnalyser, case_type: str | None, expect_gap: bool) -> None:
    """UNTESTED_LLR fires unless a CASE_LLR traces to the LLR."""
    llr = _llr()

    if case_type is None:
        g = _graph([llr])
    else:
        case = _case_hlr() if case_type == "CASE_HLR" else _case_llr()
        node_map = {case.node_id: case}
        g = _graph([llr], node_map=node_map)
        g.nodes_tracing_to.side_effect = lambda nid, source_type=None: (
            [case.node_id] if source_type == case_type else []
        )
        g.node_sync.side_effect = lambda nid: node_map.get(nid)

    gaps = analyser.analyse(g)
    assert (GapType.UNTESTED_LLR in [gap.type for gap in gaps]) == expect_gap


# ── Integration: architecture-first ordering in sorted output ─────────────────


def test_architecture_gaps_fire_before_llr_elaboration_in_sorted_output(analyser: GapAnalyser) -> None:
    """With PROJECT + HLR, UNARCHITECTED appears before UNREFINED_HLR in output."""
    proj = _project()
    hlr = _hlr()
    g = _graph([proj, hlr])
    gaps = analyser.analyse(g)
    types = [gap.type for gap in gaps]
    assert GapType.UNARCHITECTED in types
    assert GapType.UNMODULARISED in types
    assert GapType.UNREFINED_HLR in types
    assert types.index(GapType.UNARCHITECTED) < types.index(GapType.UNREFINED_HLR)


def test_contract_gap_fires_before_llr_elaboration_in_sorted_output(analyser: GapAnalyser) -> None:
    """UNCONTRACTED fires before UNREFINED_HLR when MODULE exists but has no CONTRACT."""
    hlr = _hlr()
    mod = _module()
    g = _graph([hlr, mod])
    g.any_trace_to.side_effect = lambda nid, source_type=None: (
        source_type == NodeType.MODULE.value
    )
    gaps = analyser.analyse(g)
    types = [gap.type for gap in gaps]
    assert GapType.UNCONTRACTED in types
    assert GapType.UNREFINED_HLR in types
    assert types.index(GapType.UNCONTRACTED) < types.index(GapType.UNREFINED_HLR)


def test_fully_resolved_v4_pipeline_produces_no_structural_gaps(analyser: GapAnalyser) -> None:
    """A complete v4 graph produces no structural gaps."""
    proj = _project()
    arch = _architecture()
    suite = _suite()
    hlr = _hlr()
    llr = _llr()
    mod = _module()
    ctr = _contract()
    dsn = _design()
    case_h = _case_hlr()
    case_l = _case_llr()

    children = {
        proj.node_id: [arch, suite],
        mod.node_id: [ctr],
        hlr.node_id: [llr],
    }
    node_map = {case_h.node_id: case_h, case_l.node_id: case_l}

    g = _graph([proj, arch, suite, hlr, llr, mod, ctr, dsn, case_h, case_l],
               children_map=children, node_map=node_map)

    def _any_trace_to(nid: str, source_type: str | None = None) -> bool:
        if source_type == NodeType.MODULE.value and nid == hlr.node_id:
            return True
        if source_type == NodeType.DESIGN.value and nid == llr.node_id:
            return True
        return False

    def _nodes_tracing_to(nid: str, source_type: str | None = None) -> list[str]:
        if source_type == NodeType.CASE_HLR.value and nid == hlr.node_id:
            return [case_h.node_id]
        if source_type == NodeType.CASE_LLR.value and nid == llr.node_id:
            return [case_l.node_id]
        return []

    g.any_trace_to.side_effect = _any_trace_to
    g.nodes_tracing_to.side_effect = _nodes_tracing_to
    g.node_sync.side_effect = lambda nid: node_map.get(nid)

    structural_gap_types = {
        GapType.UNARCHITECTED, GapType.UNMODULARISED, GapType.UNCONTRACTED,
        GapType.UNREFINED_HLR, GapType.UNDESIGNED, GapType.UNSUITED,
        GapType.UNTESTED_HLR, GapType.UNTESTED_LLR,
    }
    gaps = analyser.analyse(g)
    fired = {gap.type for gap in gaps} & structural_gap_types
    assert fired == set(), f"Unexpected structural gaps: {fired}"


# ── _check_case_trace_types ────────────────────────────────────────────────────


def test_case_hlr_tracing_to_suite_fires_stale_trace_to(analyser: GapAnalyser) -> None:
    """CASE_HLR whose trace_to contains a SUITE id fires STALE_TRACE_TO."""
    suite = _suite()
    case = GraphNode(
        node_id="proj.test.suite.main.case.001",
        node_type=NodeType.CASE_HLR.value,
        title="HLR Case",
        content="test steps",
        trace_to=[suite.node_id],
    )
    node_map = {suite.node_id: suite}
    g = _graph([case], node_map=node_map)
    g.node_sync.side_effect = lambda nid: node_map.get(nid)

    gaps = analyser.analyse(g)
    stale = [gap for gap in gaps if gap.type == GapType.STALE_TRACE_TO
             and gap.node_id == case.node_id]
    assert stale, "Expected STALE_TRACE_TO for CASE_HLR tracing to SUITE"
    assert suite.node_id in stale[0].context.get("wrong_type_refs", [])


def test_case_llr_tracing_to_suite_fires_stale_trace_to(analyser: GapAnalyser) -> None:
    """CASE_LLR whose trace_to contains a SUITE id fires STALE_TRACE_TO."""
    suite = _suite()
    case = GraphNode(
        node_id="proj.test.suite.main.case.002",
        node_type=NodeType.CASE_LLR.value,
        title="LLR Case",
        content="test steps",
        trace_to=[suite.node_id],
    )
    node_map = {suite.node_id: suite}
    g = _graph([case], node_map=node_map)
    g.node_sync.side_effect = lambda nid: node_map.get(nid)

    gaps = analyser.analyse(g)
    stale = [gap for gap in gaps if gap.type == GapType.STALE_TRACE_TO
             and gap.node_id == case.node_id]
    assert stale, "Expected STALE_TRACE_TO for CASE_LLR tracing to SUITE"
    assert suite.node_id in stale[0].context.get("wrong_type_refs", [])


def test_case_hlr_tracing_to_hlr_no_wrong_type_gap(analyser: GapAnalyser) -> None:
    """CASE_HLR correctly tracing to an HLR must NOT fire a wrong-type STALE_TRACE_TO."""
    hlr = _hlr()
    case = GraphNode(
        node_id="proj.test.suite.main.case.003",
        node_type=NodeType.CASE_HLR.value,
        title="HLR Case",
        content="test steps",
        trace_to=[hlr.node_id],
    )
    node_map = {hlr.node_id: hlr}
    g = _graph([case], node_map=node_map)
    g.node_sync.side_effect = lambda nid: node_map.get(nid)

    gaps = analyser.analyse(g)
    wrong_type_stale = [
        gap for gap in gaps
        if gap.type == GapType.STALE_TRACE_TO
        and gap.node_id == case.node_id
        and "wrong_type_refs" in (gap.context or {})
    ]
    assert not wrong_type_stale, "Should not fire wrong-type STALE_TRACE_TO for HLR→HLR trace"
