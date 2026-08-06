"""Unit tests for the Gap Analyser."""

from unittest.mock import MagicMock

import pytest

from backend.analysis.gap_analyser import GapAnalyser
from backend.analysis.gaps import GapPriority, GapType
from backend.graph.models import GraphNode, NodeType


@pytest.fixture
def analyser() -> GapAnalyser:
    return GapAnalyser()


@pytest.fixture
def mock_graph() -> MagicMock:
    graph = MagicMock()
    graph.all_nodes.return_value = []
    graph.children_sync.return_value = []
    graph.node_sync.return_value = None
    return graph


def _make_node(
    node_id: str,
    node_type: str,
    parent_id: str | None = None,
    content: str = "content",
    trace_to: list[str] | None = None,
) -> GraphNode:
    return GraphNode(
        node_id=node_id,
        node_type=node_type,
        title=node_id,
        content=content,
        parent_id=parent_id,
        trace_to=trace_to or [],
    )


def _make_node_with_title(
    node_id: str,
    node_type: str,
    title: str | None = None,
    parent_id: str | None = None,
) -> GraphNode:
    return GraphNode(
        node_id=node_id,
        node_type=node_type,
        title=title if title is not None else "",
        content="Some content",
        parent_id=parent_id,
    )


# ── KEEP individual tests ──────────────────────────────────────────────────────


def test_unchunked_document(analyser: GapAnalyser, mock_graph: MagicMock) -> None:
    """UNCHUNKED_DOCUMENT fires for a document node with no children."""
    doc_node = GraphNode(
        node_id="doc.spec",
        node_type=NodeType.DOCUMENT.value,
        title="Spec",
        content="Some content",
    )
    mock_graph.all_nodes.return_value = [doc_node]
    mock_graph.children_sync.return_value = []

    gaps = analyser.analyse(mock_graph)

    assert len(gaps) == 1
    assert gaps[0].type == GapType.UNCHUNKED_DOCUMENT
    assert gaps[0].node_id == "doc.spec"
    assert gaps[0].priority == GapPriority.DOCUMENT_STRUCTURE


def test_uncovered_para(analyser: GapAnalyser, mock_graph: MagicMock) -> None:
    """UNCOVERED_PARA fires for a paragraph with no HLR children."""
    para_node = GraphNode(
        node_id="doc.spec.p1",
        node_type=NodeType.PARA.value,
        title="Para 1",
        content="The system shall do X.",
    )
    mock_graph.all_nodes.return_value = [para_node]
    mock_graph.children_sync.return_value = []

    gaps = analyser.analyse(mock_graph)

    uncovered = [g for g in gaps if g.type == GapType.UNCOVERED_PARA]
    assert len(uncovered) == 1
    assert uncovered[0].node_id == "doc.spec.p1"



@pytest.mark.parametrize(("content", "should_skip"), [
    ("# Hybrid A* Path Planner — System Whitepaper", True),
    ("## 2. Functional Requirements", True),
    ("### 2.1 Path Planning", True),
    ("", True),
    ("   ", True),
    ("- The system shall accept a start pose.", False),
    ("The system shall respect a configurable minimum turn radius.", False),
])
def test_uncovered_para_skips_non_requirement_content(
    analyser: GapAnalyser, mock_graph: MagicMock, content: str, should_skip: bool,
) -> None:
    """UNCOVERED_PARA should NOT fire for headings, blank, or structural paragraphs."""
    para_node = GraphNode(
        node_id="PARA-0001",
        node_type=NodeType.PARA.value,
        title="Para",
        content=content,
    )
    mock_graph.all_nodes.return_value = [para_node]
    mock_graph.children_sync.return_value = []

    gaps = analyser.analyse(mock_graph)
    uncovered = [g for g in gaps if g.type == GapType.UNCOVERED_PARA]

    if should_skip:
        assert len(uncovered) == 0, f"Should skip non-requirement content: {content!r}"
    else:
        assert len(uncovered) == 1, f"Should flag requirement content: {content!r}"


def test_unrefined_hlr(analyser: GapAnalyser, mock_graph: MagicMock) -> None:
    """UNREFINED_HLR fires for an HLR with no LLR children."""
    hlr_node = GraphNode(
        node_id="doc.spec.par.1.hlr.001",
        node_type=NodeType.HLR.value,
        title="HLR 1",
        content="The system shall do X.",
    )
    mock_graph.all_nodes.return_value = [hlr_node]
    mock_graph.children_sync.return_value = []

    gaps = analyser.analyse(mock_graph)

    structural_gaps = [g for g in gaps if g.type == GapType.UNREFINED_HLR]
    assert len(structural_gaps) == 1
    assert structural_gaps[0].type == GapType.UNREFINED_HLR


# ── GROUP 1: ORPHAN_NODE ───────────────────────────────────────────────────────


@pytest.mark.parametrize(("scenario", "expected_gap", "check_description"), [
    ("parent_missing", True, False),
    ("parent_wrong_type", True, True),
    ("parent_correct_type", False, False),
], ids=["parent_missing", "parent_wrong_type", "parent_correct_type"])
def test_orphan_node(
    analyser: GapAnalyser,
    mock_graph: MagicMock,
    scenario: str,
    expected_gap: bool,
    check_description: bool,
) -> None:
    """ORPHAN_NODE fires when parent is missing or has wrong type, not when correct."""
    if scenario == "parent_missing":
        mod = _make_node("arch.mod.foo", NodeType.MODULE.value, parent_id="arch.gone")
        mock_graph.all_nodes.return_value = [mod]
        mock_graph.node_sync.return_value = None
        mock_graph.children_sync.return_value = []
        mock_graph.any_trace_to.return_value = True
        mock_graph.nodes_tracing_to.return_value = []
        target_id = "arch.mod.foo"

    elif scenario == "parent_wrong_type":
        doc = _make_node("doc.whitepaper", NodeType.DOCUMENT.value)
        mod = _make_node("mod.bubblesort", NodeType.MODULE.value, parent_id="doc.whitepaper")
        mock_graph.all_nodes.return_value = [doc, mod]
        mock_graph.node_sync.side_effect = lambda nid: doc if nid == "doc.whitepaper" else None
        mock_graph.children_sync.return_value = []
        mock_graph.any_trace_to.return_value = True
        mock_graph.nodes_tracing_to.return_value = []
        target_id = "mod.bubblesort"

    else:  # parent_correct_type
        arch = _make_node("arch.main", NodeType.ARCHITECTURE.value)
        mod = _make_node("arch.main.mod.core", NodeType.MODULE.value, parent_id="arch.main")
        mock_graph.all_nodes.return_value = [arch, mod]
        mock_graph.node_sync.side_effect = lambda nid: arch if nid == "arch.main" else None
        mock_graph.children_sync.return_value = [mod]
        mock_graph.any_trace_to.return_value = True
        mock_graph.nodes_tracing_to.return_value = []
        target_id = "arch.main.mod.core"

    gaps = analyser.analyse(mock_graph)
    orphan_gaps = [g for g in gaps if g.type == GapType.ORPHAN_NODE]

    if expected_gap:
        assert any(g.node_id == target_id for g in orphan_gaps)
        if check_description:
            gap = next(g for g in orphan_gaps if g.node_id == target_id)
            assert "ARCHITECTURE" in gap.description
            assert gap.context.get("parent_type") == NodeType.DOCUMENT.value
    else:
        assert not any(g.node_id == target_id for g in orphan_gaps)


# ── GROUP 2: STALE_TRACE_TO for CASE nodes ────────────────────────────────────


@pytest.mark.parametrize(("scenario", "expect_stale", "context_key", "context_val"), [
    ("missing_trace", True, "missing_trace", True),
    ("valid_trace", False, None, None),
], ids=["missing_trace", "valid_trace"])
def test_case_stale_trace(
    analyser: GapAnalyser,
    mock_graph: MagicMock,
    scenario: str,
    expect_stale: bool,
    context_key: str | None,
    context_val: bool | None,
) -> None:
    """STALE_TRACE_TO fires for CASE_HLR missing trace_to; not for valid."""
    suite = _make_node("SUITE-0001", NodeType.SUITE.value)

    if scenario == "missing_trace":
        case = _make_node("CASE-0002", NodeType.CASE_HLR.value, parent_id="SUITE-0001",
                          content="Test case", trace_to=[])
        mock_graph.all_nodes.return_value = [case]
        mock_graph.node_sync.side_effect = lambda nid: None
        case_id = "CASE-0002"

    else:  # valid_trace
        hlr = _make_node("HLR-0001", NodeType.HLR.value)
        case = _make_node("CASE-0003", NodeType.CASE_HLR.value, parent_id="SUITE-0001",
                          content="Test case", trace_to=["HLR-0001"])
        mock_graph.all_nodes.return_value = [case]
        mock_graph.node_sync.side_effect = lambda nid: {
            "HLR-0001": hlr, "SUITE-0001": suite,
        }.get(nid)
        case_id = "CASE-0003"

    mock_graph.children_sync.return_value = []

    gaps = analyser.analyse(mock_graph)
    stale = [g for g in gaps if g.type == GapType.STALE_TRACE_TO and g.node_id == case_id]

    if expect_stale:
        assert len(stale) == 1
        assert context_key is not None
        assert stale[0].context.get(context_key) == context_val
        if scenario == "missing_trace":
            assert stale[0].context.get("expected_type") == NodeType.HLR.value
    else:
        assert not stale


# ── GROUP 3: UNTITLED_NODE ────────────────────────────────────────────────────


@pytest.mark.parametrize(("scenario", "expect_gap", "check_msg"), [
    ("title_missing", True, False),
    ("title_too_long", True, True),
    ("good_title", False, False),
], ids=["title_missing", "title_too_long", "good_title"])
def test_untitled_node(
    analyser: GapAnalyser,
    mock_graph: MagicMock,
    scenario: str,
    expect_gap: bool,
    check_msg: bool,
) -> None:
    """UNTITLED_NODE fires when title is absent or too long, not for short valid titles."""
    arch = _make_node_with_title("ARCH-0001", NodeType.ARCHITECTURE.value)

    if scenario == "title_missing":
        module = _make_node_with_title("MOD-0001", NodeType.MODULE.value)
        node_id = "MOD-0001"
    elif scenario == "title_too_long":
        module = _make_node_with_title(
            "MOD-0002", NodeType.MODULE.value,
            title="This is a very long title that is too verbose for a node",
        )
        node_id = "MOD-0002"
    else:
        module = _make_node_with_title("MOD-0003", NodeType.MODULE.value, title="Auth Module")
        node_id = "MOD-0003"

    mock_graph.all_nodes.return_value = [module]
    mock_graph.children_sync.return_value = []
    mock_graph.node_sync.return_value = arch
    mock_graph.any_trace_to.return_value = True
    mock_graph.nodes_tracing_to.return_value = []

    gaps = analyser.analyse(mock_graph)
    untitled = [g for g in gaps if g.type == GapType.UNTITLED_NODE and g.node_id == node_id]

    if expect_gap:
        assert untitled
        if check_msg:
            assert "too long" in untitled[0].description
    else:
        assert not untitled


def test_untitled_node_skips_result_and_record(
    analyser: GapAnalyser,
    mock_graph: MagicMock,
) -> None:
    """UNTITLED_NODE is never raised for RESULT or RECORD nodes."""
    result_node = _make_node_with_title("RESULT-0001", NodeType.RESULT.value)
    record_node = _make_node_with_title("RECORD-0001", NodeType.RECORD.value)
    mock_graph.all_nodes.return_value = [result_node, record_node]
    mock_graph.children_sync.return_value = []
    mock_graph.node_sync.return_value = None
    mock_graph.any_trace_to.return_value = False
    mock_graph.nodes_tracing_to.return_value = []

    gaps = analyser.analyse(mock_graph)
    untitled = [g for g in gaps if g.type == GapType.UNTITLED_NODE]
    assert not untitled


# ── GROUP 3a: EMPTY_CONTENT heading exemption ────────────────────────────────


def test_empty_content_skipped_for_heading_paras(
    analyser: GapAnalyser,
    mock_graph: MagicMock,
) -> None:
    """Heading PARAs with empty content should NOT fire EMPTY_CONTENT.

    Populating them from parent content creates duplicate content across the
    subtree, which semantic dedup then deletes, orphaning children.
    """
    heading = GraphNode(
        node_id="PARA-0001",
        node_type=NodeType.PARA.value,
        title="Functional Requirements",
        content="",
        para_type="heading",
        parent_id="DOCUMENT-0001",
    )
    mock_graph.all_nodes.return_value = [heading]
    mock_graph.children_sync.return_value = []
    mock_graph.node_sync.return_value = None
    mock_graph.any_trace_to.return_value = True
    mock_graph.nodes_tracing_to.return_value = []

    gaps = analyser.analyse(mock_graph)
    empty = [g for g in gaps if g.type == GapType.EMPTY_CONTENT]
    assert not empty, f"expected no EMPTY_CONTENT for heading PARA, got {empty}"


def test_empty_content_still_fires_for_non_heading_paras(
    analyser: GapAnalyser,
    mock_graph: MagicMock,
) -> None:
    """Non-heading PARAs with empty content must still fire EMPTY_CONTENT."""
    functional = GraphNode(
        node_id="PARA-0001",
        node_type=NodeType.PARA.value,
        title="Sort Lists",
        content="",
        para_type="functional",
        parent_id="DOCUMENT-0001",
    )
    mock_graph.all_nodes.return_value = [functional]
    mock_graph.children_sync.return_value = []
    mock_graph.node_sync.return_value = None
    mock_graph.any_trace_to.return_value = True
    mock_graph.nodes_tracing_to.return_value = []

    gaps = analyser.analyse(mock_graph)
    empty = [g for g in gaps if g.type == GapType.EMPTY_CONTENT and g.node_id == "PARA-0001"]
    assert len(empty) == 1


# ── GROUP 3b: TITLE_COLLIDES_WITH_PARENT ──────────────────────────────────────


def test_title_collides_with_parent_fires_when_titles_match(
    analyser: GapAnalyser,
    mock_graph: MagicMock,
) -> None:
    """Child with exact same title as parent raises TITLE_COLLIDES_WITH_PARENT."""
    parent = _make_node_with_title("PARA-0001", NodeType.PARA.value, title="Provide bubble_sort Function")
    child = _make_node_with_title(
        "HLR-0001", NodeType.HLR.value,
        title="Provide bubble_sort Function", parent_id="PARA-0001",
    )
    mock_graph.all_nodes.return_value = [parent, child]
    mock_graph.children_sync.return_value = []
    mock_graph.node_sync.side_effect = lambda nid: parent if nid == "PARA-0001" else None
    mock_graph.any_trace_to.return_value = True
    mock_graph.nodes_tracing_to.return_value = []

    gaps = analyser.analyse(mock_graph)
    hits = [g for g in gaps if g.type == GapType.TITLE_COLLIDES_WITH_PARENT and g.node_id == "HLR-0001"]
    assert len(hits) == 1
    assert hits[0].context["parent_id"] == "PARA-0001"
    assert hits[0].context["parent_title"] == "Provide bubble_sort Function"


def test_title_collides_is_case_insensitive(analyser: GapAnalyser, mock_graph: MagicMock) -> None:
    """Case + whitespace differences still count as a collision."""
    parent = _make_node_with_title("PARA-0001", NodeType.PARA.value, title="Provide Function")
    child = _make_node_with_title(
        "HLR-0001", NodeType.HLR.value,
        title="  provide function  ", parent_id="PARA-0001",
    )
    mock_graph.all_nodes.return_value = [parent, child]
    mock_graph.children_sync.return_value = []
    mock_graph.node_sync.side_effect = lambda nid: parent if nid == "PARA-0001" else None
    mock_graph.any_trace_to.return_value = True
    mock_graph.nodes_tracing_to.return_value = []

    gaps = analyser.analyse(mock_graph)
    hits = [g for g in gaps if g.type == GapType.TITLE_COLLIDES_WITH_PARENT]
    assert len(hits) == 1


def test_title_collides_quiet_when_titles_differ(
    analyser: GapAnalyser,
    mock_graph: MagicMock,
) -> None:
    """Different-scope child title does not fire."""
    parent = _make_node_with_title("PARA-0001", NodeType.PARA.value, title="Provide Function")
    child = _make_node_with_title(
        "HLR-0001", NodeType.HLR.value,
        title="Return Sorted List", parent_id="PARA-0001",
    )
    mock_graph.all_nodes.return_value = [parent, child]
    mock_graph.children_sync.return_value = []
    mock_graph.node_sync.side_effect = lambda nid: parent if nid == "PARA-0001" else None
    mock_graph.any_trace_to.return_value = True
    mock_graph.nodes_tracing_to.return_value = []

    gaps = analyser.analyse(mock_graph)
    hits = [g for g in gaps if g.type == GapType.TITLE_COLLIDES_WITH_PARENT]
    assert not hits


# ── GROUP 3c: SIBLING_TITLE_DUPLICATE ─────────────────────────────────────────


def test_sibling_title_duplicate_fires_for_identical_sibling_titles(
    analyser: GapAnalyser,
    mock_graph: MagicMock,
) -> None:
    """Two siblings under the same parent with identical titles raise one gap
    targeting the later-created node."""
    parent = _make_node_with_title("HLR-0001", NodeType.HLR.value, title="Parent HLR")
    a = _make_node_with_title(
        "LLR-0001", NodeType.LLR.value,
        title="Validate Input Range", parent_id="HLR-0001",
    )
    b = _make_node_with_title(
        "LLR-0002", NodeType.LLR.value,
        title="validate input range", parent_id="HLR-0001",
    )
    mock_graph.all_nodes.return_value = [parent, a, b]
    mock_graph.children_sync.return_value = []
    mock_graph.node_sync.side_effect = lambda nid: parent if nid == "HLR-0001" else None
    mock_graph.any_trace_to.return_value = True
    mock_graph.nodes_tracing_to.return_value = []

    gaps = analyser.analyse(mock_graph)
    hits = [g for g in gaps if g.type == GapType.SIBLING_TITLE_DUPLICATE]
    assert len(hits) == 1
    assert hits[0].node_id == "LLR-0002"  # later one retitled
    assert hits[0].context["sibling_id"] == "LLR-0001"


def test_sibling_title_duplicate_three_way(analyser: GapAnalyser, mock_graph: MagicMock) -> None:
    """Three siblings sharing a title emit two gaps; the first is canonical."""
    parent = _make_node_with_title("HLR-0001", NodeType.HLR.value, title="Parent HLR")
    nodes = [
        _make_node_with_title(f"LLR-000{i}", NodeType.LLR.value, title="Same Title", parent_id="HLR-0001")
        for i in (1, 2, 3)
    ]
    mock_graph.all_nodes.return_value = [parent, *nodes]
    mock_graph.children_sync.return_value = []
    mock_graph.node_sync.side_effect = lambda nid: parent if nid == "HLR-0001" else None
    mock_graph.any_trace_to.return_value = True
    mock_graph.nodes_tracing_to.return_value = []

    gaps = analyser.analyse(mock_graph)
    hits = [g for g in gaps if g.type == GapType.SIBLING_TITLE_DUPLICATE]
    assert len(hits) == 2
    assert {g.node_id for g in hits} == {"LLR-0002", "LLR-0003"}
    for g in hits:
        assert g.context["sibling_id"] == "LLR-0001"


def test_sibling_title_duplicate_quiet_when_distinct(
    analyser: GapAnalyser,
    mock_graph: MagicMock,
) -> None:
    """Distinct sibling titles do not fire."""
    parent = _make_node_with_title("HLR-0001", NodeType.HLR.value, title="Parent HLR")
    a = _make_node_with_title(
        "LLR-0001", NodeType.LLR.value, title="First Behavior", parent_id="HLR-0001",
    )
    b = _make_node_with_title(
        "LLR-0002", NodeType.LLR.value, title="Second Behavior", parent_id="HLR-0001",
    )
    mock_graph.all_nodes.return_value = [parent, a, b]
    mock_graph.children_sync.return_value = []
    mock_graph.node_sync.side_effect = lambda nid: parent if nid == "HLR-0001" else None
    mock_graph.any_trace_to.return_value = True
    mock_graph.nodes_tracing_to.return_value = []

    gaps = analyser.analyse(mock_graph)
    hits = [g for g in gaps if g.type == GapType.SIBLING_TITLE_DUPLICATE]
    assert not hits


# ── GROUP 4: DUPLICATE_NODE ───────────────────────────────────────────────────


@pytest.mark.parametrize(("scenario", "expect_dup"), [
    ("same_content_fires", True),
    ("unique_content_no_gap", False),
    ("different_case_type_no_gap", False),
    ("same_case_type_fires", True),
], ids=["same_content_fires", "unique_content_no_gap", "different_case_type_no_gap", "same_case_type_fires"])
def test_check_duplicate_siblings(analyser: GapAnalyser, scenario: str, expect_dup: bool) -> None:
    """DUPLICATE_NODE fires only for same-content siblings with matching case_type partition."""
    if scenario == "same_content_fires":
        node_a = GraphNode(node_id="LLR-0001", node_type=NodeType.LLR.value, title="A",
                           content="The system shall do X.", parent_id="HLR-0001")
        node_b = GraphNode(node_id="LLR-0002", node_type=NodeType.LLR.value, title="B",
                           content="The system shall do X.", parent_id="HLR-0001")
        nodes = [node_a, node_b]
        dup_id: str | None = "LLR-0002"
        canonical_id: str | None = "LLR-0001"

    elif scenario == "unique_content_no_gap":
        node_a = GraphNode(node_id="LLR-0001", node_type=NodeType.LLR.value, title="A",
                           content="The system shall do X.", parent_id="HLR-0001")
        node_b = GraphNode(node_id="LLR-0002", node_type=NodeType.LLR.value, title="B",
                           content="The system shall do Y.", parent_id="HLR-0001")
        nodes = [node_a, node_b]
        dup_id = canonical_id = None

    elif scenario == "different_case_type_no_gap":
        node_a = GraphNode(node_id="CASE_HLR-0001", node_type=NodeType.CASE_HLR.value, title="A",
                           content="Verify the system handles input.", parent_id="SUITE-0001")
        node_b = GraphNode(node_id="CASE_LLR-0001", node_type=NodeType.CASE_LLR.value, title="B",
                           content="Verify the system handles input.", parent_id="SUITE-0001")
        nodes = [node_a, node_b]
        dup_id = canonical_id = None

    else:  # same_case_type_fires
        node_a = GraphNode(node_id="CASE_LLR-0001", node_type=NodeType.CASE_LLR.value, title="A",
                           content="Verify the system handles input.", parent_id="SUITE-0001")
        node_b = GraphNode(node_id="CASE_LLR-0002", node_type=NodeType.CASE_LLR.value, title="B",
                           content="Verify the system handles input.", parent_id="SUITE-0001")
        nodes = [node_a, node_b]
        dup_id = "CASE_LLR-0002"
        canonical_id = "CASE_LLR-0001"

    gaps = analyser._check_duplicate_siblings(nodes)
    dup_gaps = [g for g in gaps if g.type == GapType.DUPLICATE_NODE]

    if expect_dup:
        assert len(dup_gaps) == 1
        assert dup_gaps[0].node_id == dup_id
        assert dup_gaps[0].context.get("duplicate_of") == canonical_id
    else:
        assert not dup_gaps


# ── _has_case_of_type backward compat tests ──────────────────────────────────


class TestHasCaseOfType:
    """Test _has_case_of_type — only CASE_HLR/CASE_LLR count (no legacy CASE)."""

    def test_case_llr_satisfies(self, analyser: GapAnalyser, mock_graph: MagicMock) -> None:
        """CASE_LLR tracing to an LLR satisfies the gap."""
        mock_graph.nodes_tracing_to.side_effect = (
            lambda tid, source_type=None: ["CASE_LLR-001"] if source_type == "CASE_LLR" else []
        )
        assert analyser._has_case_of_type(mock_graph, "LLR-001", "llr") is True

    def test_case_hlr_satisfies(self, analyser: GapAnalyser, mock_graph: MagicMock) -> None:
        """CASE_HLR tracing to an HLR satisfies the gap."""
        mock_graph.nodes_tracing_to.side_effect = (
            lambda tid, source_type=None: ["CASE_HLR-001"] if source_type == "CASE_HLR" else []
        )
        assert analyser._has_case_of_type(mock_graph, "HLR-001", "hlr") is True


    def test_no_case_returns_false(self, analyser: GapAnalyser, mock_graph: MagicMock) -> None:
        """No CASE_LLR tracing to the LLR → gap remains."""
        mock_graph.nodes_tracing_to.return_value = []
        assert analyser._has_case_of_type(mock_graph, "LLR-001", "llr") is False


# ── UNTESTED_LLR integration tests ──────────────────────────────────────────


class TestUntestedLlr:
    """Verify UNTESTED_LLR gap detection for LLR nodes."""

    def test_llr_with_case_llr_no_gap(self, analyser: GapAnalyser, mock_graph: MagicMock) -> None:
        """LLR covered by a CASE_LLR node → no UNTESTED_LLR gap."""
        llr = _make_node("LLR-001", NodeType.LLR.value, parent_id="HLR-001",
                         content="The system shall do X.")
        case = GraphNode(
            node_id="CASE_LLR-001", node_type=NodeType.CASE_LLR.value,
            title="Test", content="Verify", parent_id="SUITE-001",
            trace_to=["LLR-001"],
        )
        mock_graph.all_nodes.return_value = [llr, case]
        mock_graph.children_sync.return_value = []

        def _nodes_tracing(target_id: str, source_type: str | None = None) -> list[str]:
            if source_type == "CASE_LLR" and target_id == "LLR-001":
                return ["CASE_LLR-001"]
            if source_type == "DESIGN" and target_id == "LLR-001":
                return ["DESIGN-001"]
            return []
        mock_graph.nodes_tracing_to.side_effect = _nodes_tracing
        mock_graph.any_trace_to.side_effect = lambda tid, source_type: bool(
            _nodes_tracing(tid, source_type))
        mock_graph.node_sync.side_effect = lambda nid: {
            "LLR-001": llr, "CASE_LLR-001": case,
        }.get(nid)

        gaps = analyser.analyse(mock_graph)
        untested = [g for g in gaps if g.type == GapType.UNTESTED_LLR]
        assert len(untested) == 0

    def test_llr_with_only_legacy_case_still_untested(
        self,
        analyser: GapAnalyser,
        mock_graph: MagicMock,
    ) -> None:
        """LLR traced only by legacy CASE → UNTESTED_LLR gap persists."""
        llr = _make_node("LLR-001", NodeType.LLR.value, parent_id="HLR-001",
                         content="The system shall do X.")

        mock_graph.all_nodes.return_value = [llr]
        mock_graph.children_sync.return_value = []

        def _nodes_tracing(target_id: str, source_type: str | None = None) -> list[str]:
            # Legacy CASE traces exist but no CASE_LLR
            if source_type == "DESIGN" and target_id == "LLR-001":
                return ["DESIGN-001"]
            return []
        mock_graph.nodes_tracing_to.side_effect = _nodes_tracing
        mock_graph.any_trace_to.side_effect = lambda tid, source_type: bool(
            _nodes_tracing(tid, source_type))
        mock_graph.node_sync.side_effect = lambda nid: {
            "LLR-001": llr,
        }.get(nid)

        gaps = analyser.analyse(mock_graph)
        untested = [g for g in gaps if g.type == GapType.UNTESTED_LLR]
        assert len(untested) == 1
        assert untested[0].node_id == "LLR-001"


def test_identical_para_siblings_are_not_duplicates(analyser: GapAnalyser) -> None:
    """PARAs are document mirrors — byte-identical siblings are legitimate
    (empty heading sections, repeated whitepaper sentences). Live gaps:
    topological_sort r3 PARA-0010/0011/0013 vs PARA-0008."""
    nodes = [
        GraphNode(node_id="PARA-0008", node_type=NodeType.PARA.value,
                  title="Derived Queries", content="", parent_id="DOCUMENT-0001"),
        GraphNode(node_id="PARA-0010", node_type=NodeType.PARA.value,
                  title="Correctness Properties", content="", parent_id="DOCUMENT-0001"),
        GraphNode(node_id="PARA-0011", node_type=NodeType.PARA.value,
                  title="Failure Modes Edge Cases", content="", parent_id="DOCUMENT-0001"),
    ]
    gaps = analyser._check_duplicate_siblings(nodes)
    assert [g for g in gaps if g.type == GapType.DUPLICATE_NODE] == []


# ── U6: cover-or-classify — non_normative marking exempts a PARA ─────────────


def test_uncovered_para_skipped_when_marked_non_normative(
    analyser: GapAnalyser, mock_graph: MagicMock,
) -> None:
    """A PARA marked non_normative with a documented rationale needs no HLR."""
    para_node = GraphNode(
        node_id="PARA-0001",
        node_type=NodeType.PARA.value,
        title="Historic Background Story",
        content="Sorting has a long history in computing.",
        properties={
            "non_normative": True,
            "non_normative_rationale": "background/context",
        },
    )
    mock_graph.all_nodes.return_value = [para_node]
    mock_graph.children_sync.return_value = []

    gaps = analyser.analyse(mock_graph)

    assert [g for g in gaps if g.type == GapType.UNCOVERED_PARA] == []
    assert [g for g in gaps if g.type == GapType.INADEQUATE_CONTENT] == []


def test_uncovered_para_marked_without_rationale_is_loud_gap(
    analyser: GapAnalyser, mock_graph: MagicMock,
) -> None:
    """non_normative without a valid rationale is a loud INADEQUATE_CONTENT
    gap — never a silent exemption, never a plain UNCOVERED_PARA."""
    para_node = GraphNode(
        node_id="PARA-0001",
        node_type=NodeType.PARA.value,
        title="Historic Background Story",
        content="Sorting has a long history in computing.",
        properties={"non_normative": True},
    )
    mock_graph.all_nodes.return_value = [para_node]
    mock_graph.children_sync.return_value = []

    gaps = analyser.analyse(mock_graph)

    assert [g for g in gaps if g.type == GapType.UNCOVERED_PARA] == []
    loud = [g for g in gaps if g.type == GapType.INADEQUATE_CONTENT]
    assert len(loud) == 1
    assert loud[0].node_id == "PARA-0001"
    assert "non_normative_rationale" in loud[0].description


def test_uncovered_para_unmarked_functional_still_fires(
    analyser: GapAnalyser, mock_graph: MagicMock,
) -> None:
    """Unmarked, uncovered functional PARAs keep firing — quota softened,
    coverage obligation unchanged."""
    para_node = GraphNode(
        node_id="PARA-0001",
        node_type=NodeType.PARA.value,
        title="Sort Input Contract",
        content="The system shall accept a list of integers.",
        para_type="functional",
    )
    mock_graph.all_nodes.return_value = [para_node]
    mock_graph.children_sync.return_value = []

    gaps = analyser.analyse(mock_graph)

    assert len([g for g in gaps if g.type == GapType.UNCOVERED_PARA]) == 1


def test_uncovered_para_sub_type_handling_unchanged(
    analyser: GapAnalyser, mock_graph: MagicMock,
) -> None:
    """heading PARAs stay exempt; unmarked rationale PARAs still fire (an
    agent must explicitly classify them non_normative to exempt them)."""
    heading = GraphNode(
        node_id="PARA-0001", node_type=NodeType.PARA.value,
        title="Section Heading Marker", content="## 2. Requirements",
        para_type="heading",
    )
    rationale = GraphNode(
        node_id="PARA-0002", node_type=NodeType.PARA.value,
        title="Design Rationale Prose",
        content="Chunking first keeps the LLM context small.",
        para_type="rationale",
    )
    mock_graph.all_nodes.return_value = [heading, rationale]
    mock_graph.children_sync.return_value = []

    gaps = analyser.analyse(mock_graph)

    uncovered = [g for g in gaps if g.type == GapType.UNCOVERED_PARA]
    assert [g.node_id for g in uncovered] == ["PARA-0002"]
