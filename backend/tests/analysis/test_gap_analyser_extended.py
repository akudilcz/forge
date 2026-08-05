"""Extended gap analyser tests covering all gap types."""

from typing import Any
from unittest.mock import MagicMock

import pytest

from backend.analysis.gap_analyser import GapAnalyser
from backend.analysis.gaps import GapPriority, GapType
from backend.graph.models import GraphNode, NodeType


@pytest.fixture
def analyser() -> GapAnalyser:
    return GapAnalyser()


def _graph(
    nodes: list[GraphNode],
    children_map: dict[str, list[GraphNode]] | None = None,
    node_map: dict[str, GraphNode] | None = None,
) -> Any:
    """Build a minimal mock graph (typed Any — it is a MagicMock stand-in)."""
    g = MagicMock()
    g.all_nodes.return_value = nodes
    g.children_sync.side_effect = lambda nid: (children_map or {}).get(nid, [])
    g.node_sync.side_effect = lambda nid: (node_map or {}).get(nid)
    g.any_trace_to.return_value = False
    g.nodes_tracing_to.return_value = []
    return g


# ── KEEP individual tests ──────────────────────────────────────────────────────


def test_stale_node(analyser: GapAnalyser) -> None:
    from backend.graph.provenance import DERIVED_FROM_HASH, provenance_hash

    parent = GraphNode(
        node_id="doc.spec",
        node_type=NodeType.DOCUMENT.value,
        title="Spec",
        content="current document body",
    )
    child = GraphNode(
        node_id="doc.spec.p1",
        node_type=NodeType.PARA.value,
        title="Para",
        content="some content",
        parent_id="doc.spec",
        properties={DERIVED_FROM_HASH: provenance_hash("older document body")},
    )
    g = _graph([parent, child], node_map={"doc.spec": parent, "doc.spec.p1": child})
    gaps = analyser.analyse(g)
    assert GapType.STALE_NODE in [gap.type for gap in gaps]



def test_empty_content(analyser: GapAnalyser) -> None:
    node = GraphNode(
        node_id="req.hlr.1",
        node_type=NodeType.HLR.value,
        title="HLR 1",
        content="",
        properties={"req_level": "hlr"},
    )
    g = _graph([node])
    gaps = analyser.analyse(g)
    assert GapType.EMPTY_CONTENT in [gap.type for gap in gaps]


def test_no_structural_gaps_for_heading_para(analyser: GapAnalyser) -> None:
    """A heading PARA is structural scaffolding — it never needs derived HLRs."""
    doc = GraphNode(node_id="doc.spec", node_type=NodeType.DOCUMENT.value, title="Spec", content="")
    heading = GraphNode(
        node_id="doc.spec.p1",
        node_type=NodeType.PARA.value,
        title="Overview",
        content="Overview",
        parent_id="doc.spec",
        para_type="heading",
    )
    g = _graph(
        [doc, heading],
        children_map={"doc.spec": [heading]},
        node_map={"doc.spec": doc},
    )
    gaps = analyser.analyse(g)
    struct = [x for x in gaps if x.type in (GapType.UNCHUNKED_DOCUMENT, GapType.UNCOVERED_PARA)]
    assert len(struct) == 0


def test_uncovered_para_fires_for_plain_text(analyser: GapAnalyser) -> None:
    """Any body paragraph without HLR children triggers UNCOVERED_PARA."""
    doc = GraphNode(node_id="doc.spec", node_type=NodeType.DOCUMENT.value, title="Spec", content="")
    para = GraphNode(
        node_id="doc.spec.p1",
        node_type=NodeType.PARA.value,
        title="Para",
        content="The planner accepts a start pose and returns a path.",
        parent_id="doc.spec",
        para_type="paragraph",
    )
    g = _graph(
        [doc, para],
        children_map={"doc.spec": [para], "doc.spec.p1": []},
        node_map={"doc.spec": doc},
    )
    gaps = analyser.analyse(g)
    uncovered = [x for x in gaps if x.type == GapType.UNCOVERED_PARA]
    assert len(uncovered) == 1
    assert uncovered[0].node_id == "doc.spec.p1"


# ── GROUP 1: UNSYNCED_TEST / UNSYNCED_DESIGN ──────────────────────────────────


@pytest.mark.parametrize(("scenario", "gap_type", "expect_gap"), [
    ("case_no_test_child", GapType.UNSYNCED_TEST, True),
    ("design_no_code_child", GapType.UNSYNCED_DESIGN, True),
    ("design_with_code_child", GapType.UNSYNCED_DESIGN, False),
], ids=["case_no_test_child", "design_no_code_child", "design_with_code_child"])
def test_unsynced(
    analyser: GapAnalyser, scenario: str, gap_type: GapType, expect_gap: bool
) -> None:
    """UNSYNCED_TEST/DESIGN fires when child is absent, not when it exists."""
    if scenario == "case_no_test_child":
        node = GraphNode(node_id="tst.case.1", node_type=NodeType.CASE_HLR.value,
                         title="Test case 1", content="test something")
        g = _graph([node])

    elif scenario == "design_no_code_child":
        node = GraphNode(node_id="mod.foo.design.validator", node_type=NodeType.DESIGN.value,
                         title="Validator Design",
                         content="class Validator: validate(token: str) -> bool")
        g = _graph([node])

    else:  # design_with_code_child
        node = GraphNode(node_id="mod.foo.design.validator", node_type=NodeType.DESIGN.value,
                         title="Validator Design",
                         content="class Validator: validate(token: str) -> bool")
        code = GraphNode(node_id="CODE-0001", node_type=NodeType.CODE.value,
                         title="Validator implementation", content="Implements the Validator class")
        g = _graph([node], children_map={node.node_id: [code]})

    gaps = analyser.analyse(g)
    types = [gap.type for gap in gaps]
    if expect_gap:
        assert gap_type in types
    else:
        assert gap_type not in types


# ── GROUP 2: STALE_TRACE_TO ───────────────────────────────────────────────────


@pytest.mark.parametrize(("scenario", "expect_stale"), [
    ("missing_ref", True),
    ("all_refs_valid", False),
    ("trace_to_empty", False),
], ids=["missing_ref", "all_refs_valid", "trace_to_empty"])
def test_stale_trace_to(analyser: GapAnalyser, scenario: str, expect_stale: bool) -> None:
    """STALE_TRACE_TO fires only when trace_to references a missing node."""
    existing = GraphNode(node_id="req.llr.2", node_type=NodeType.HLR.value,
                         title="LLR2", content="x", properties={"req_level": "llr"})

    if scenario == "missing_ref":
        node = GraphNode(node_id="llr.1.ctr.abc", node_type=NodeType.CONTRACT.value,
                         title="Contract", content="interface spec",
                         trace_to=["req.llr.2", "req.llr.ghost"])
        g = _graph([node], node_map={"req.llr.2": existing})
        g.any_trace_to = MagicMock(return_value=False)

    elif scenario == "all_refs_valid":
        node = GraphNode(node_id="llr.1.ctr.abc", node_type=NodeType.CONTRACT.value,
                         title="Contract", content="interface spec",
                         trace_to=["req.llr.2"])
        g = _graph([node], node_map={"req.llr.2": existing})
        g.any_trace_to = MagicMock(return_value=False)

    else:  # trace_to_empty
        node = GraphNode(node_id="req.llr.1", node_type=NodeType.HLR.value,
                         title="LLR", content="shall do something",
                         properties={"req_level": "llr"})
        g = _graph([node])
        g.any_trace_to = MagicMock(return_value=False)

    gaps = analyser.analyse(g)
    types = [gap.type for gap in gaps]

    if expect_stale:
        assert GapType.STALE_TRACE_TO in types
        stale_gap = next(gap for gap in gaps if gap.type == GapType.STALE_TRACE_TO)
        assert "req.llr.ghost" in stale_gap.context["stale_refs"]
        assert "req.llr.2" not in stale_gap.context["stale_refs"]
    else:
        assert GapType.STALE_TRACE_TO not in types


# ── GROUP 3: UNSUITED ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(("has_suite", "expect_gap"), [
    (False, True),
    (True, False),
], ids=["no_suite_fires", "suite_exists_no_gap"])
def test_unsuited(analyser: GapAnalyser, has_suite: bool, expect_gap: bool) -> None:
    """UNSUITED fires when PROJECT has no SUITE child, not when one exists."""
    proj = GraphNode(node_id="proj.test", node_type=NodeType.PROJECT.value,
                     title="Test Project", content="")

    if has_suite:
        suite = GraphNode(node_id="proj.test.suite.main", node_type=NodeType.SUITE.value,
                          title="Test Suite", content="")
        g = _graph([proj], children_map={"proj.test": [suite]})
    else:
        g = _graph([proj])

    gaps = analyser.analyse(g)
    types = [gap.type for gap in gaps]

    if expect_gap:
        assert GapType.UNSUITED in types
        unsuited_gap = next(gap for gap in gaps if gap.type == GapType.UNSUITED)
        assert unsuited_gap.node_id == "proj.test"
        assert unsuited_gap.priority == GapPriority.TEST_SUITE
    else:
        assert GapType.UNSUITED not in types


# ── MALFORMED_REQUIREMENT wording check (EARS-aware) ────────────────────────


@pytest.mark.parametrize("content", [
    "The system shall accept a start pose.",
    "The system shall return an error when a timeout expires.",
    "The system shall reject writes while in safe mode.",
    "The system shall expand obstacles where an inflation radius is configured.",
    "The system shall return a failure indication if no path is found.",
])
def test_wording_check_accepts_valid_format(analyser: GapAnalyser, content: str) -> None:
    """Requirements starting with 'The system shall' pass the wording check."""
    hlr = GraphNode(
        node_id="HLR-0001", node_type=NodeType.HLR.value,
        title="Test HLR", content=content,
    )
    g = _graph([hlr])
    gaps = analyser.analyse(g)
    malformed = [x for x in gaps if x.type == GapType.MALFORMED_REQUIREMENT]
    assert len(malformed) == 0


@pytest.mark.parametrize("content", [
    "Accept a start pose and return a path.",
    "When a timeout expires, the system shall return an error.",
    "Where an inflation radius is configured, the system shall expand obstacles.",
    "The planner uses A* search.",
])
def test_wording_check_flags_wrong_start(analyser: GapAnalyser, content: str) -> None:
    """Content not starting with 'The system shall' is flagged — even with conditions first."""
    hlr = GraphNode(
        node_id="HLR-0001", node_type=NodeType.HLR.value,
        title="Test HLR", content=content,
    )
    g = _graph([hlr])
    gaps = analyser.analyse(g)
    malformed = [x for x in gaps if x.type == GapType.MALFORMED_REQUIREMENT]
    assert len(malformed) == 1


@pytest.mark.parametrize(("title", "content"), [
    ("Handle PARA-0003 Content", "The system shall address the requirements specified in PARA-0003."),
    ("Address PARA-0012 Content", "The system shall comply with the requirements defined in PARA-0012."),
    ("Address PARA-0013 Content", "The system shall PARA-0013."),
])
def test_wording_check_flags_placeholder_requirements(
    analyser: GapAnalyser, title: str, content: str
) -> None:
    """Placeholder HLRs containing raw PARA node IDs should be flagged."""
    hlr = GraphNode(
        node_id="HLR-0001", node_type=NodeType.HLR.value,
        title=title, content=content,
    )
    g = _graph([hlr])
    gaps = analyser.analyse(g)
    malformed = [x for x in gaps if x.type == GapType.MALFORMED_REQUIREMENT]
    assert len(malformed) >= 1, f"Should flag placeholder: {content!r}"
