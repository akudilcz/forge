"""Write-time enforcement of authoring invariants (correct-by-construction).

graph-write tools must REJECT writes that violate the deterministic
invariants in ``backend/analysis/node_invariants.py``, returning a tool
ERROR the agent can fix in the same turn — instead of accepting the write
and letting the Gap Analyser flag a gap repaired by a later paid dispatch.
"""

from __future__ import annotations

from typing import Any

from backend.graph.models import GraphNode
from backend.graph.provenance import DERIVED_FROM_HASH, provenance_hash
from backend.tools.graph_write import GraphWriteTool
from backend.tools.multi_graph_write import MultiGraphWriteTool


class _StubGraph:
    """Duck-typed in-memory graph good enough for tool-level validation."""

    def __init__(self, nodes: list[GraphNode]) -> None:
        self._by_id = {n.node_id: n for n in nodes}
        self.added: list[GraphNode] = []
        self.updated: list[dict[str, Any]] = []

    def node_sync(self, nid: str) -> GraphNode | None:
        return self._by_id.get(nid)

    async def node(self, nid: str) -> GraphNode | None:
        return self._by_id.get(nid)

    def children_sync(self, pid: str) -> list[GraphNode]:
        return [n for n in self._by_id.values() if n.parent_id == pid]

    async def add_node(self, node: GraphNode) -> GraphNode:
        self.added.append(node)
        self._by_id[node.node_id] = node
        return node

    async def update_node(
        self, node_id: str, content: str | None, properties: dict[str, Any] | None,
        changed_by: str, change_reason: str, title: str | None = None,
        trace_to: list[str] | None = None,
    ) -> tuple[GraphNode, None]:
        self.updated.append({
            "node_id": node_id, "content": content, "properties": properties,
            "title": title, "trace_to": trace_to,
        })
        node = self._by_id[node_id]
        if content is not None:
            node.content = content
        if title is not None:
            node.title = title
        if trace_to is not None:
            node.trace_to = trace_to
        if properties is not None:
            node.properties = properties
        return node, None

    async def allocate_node_id(self, node_type: str) -> str:
        return f"{node_type}-9999"


def _n(nid: str, ntype: str, **kw: Any) -> GraphNode:
    return GraphNode(node_id=nid, node_type=ntype, **kw)


def _para_graph() -> _StubGraph:
    return _StubGraph([
        _n("DOC-0001", "DOCUMENT", content="doc body"),
        _n("PARA-0001", "PARA", parent_id="DOC-0001",
           title="Input Handling", content="Paragraph text."),
    ])


# ── add_node: title invariants ───────────────────────────────────────────────


def test_add_node_without_title_rejected() -> None:
    graph = _para_graph()
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="add_node", node_type="HLR", parent_id="PARA-0001",
        content="The system shall accept CSV input files.",
    )
    assert result.startswith("ERROR")
    assert "title" in result
    assert graph.added == []


def test_add_node_title_too_long_rejected() -> None:
    graph = _para_graph()
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="add_node", node_type="HLR", parent_id="PARA-0001",
        title="a very long title with far too many words in it",
        content="The system shall accept CSV input files.",
    )
    assert result.startswith("ERROR")
    assert "too long" in result


def test_add_node_duplicate_sibling_title_rejected() -> None:
    graph = _para_graph()
    graph._by_id["HLR-0001"] = _n(
        "HLR-0001", "HLR", parent_id="PARA-0001", title="Accept CSV Input",
        content="The system shall accept CSV input files.",
    )
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="add_node", node_type="HLR", parent_id="PARA-0001",
        title="accept csv input",
        content="The system shall reject non-CSV input files.",
    )
    assert result.startswith("ERROR")
    assert "HLR-0001" in result


# ── add_node: requirement wording ────────────────────────────────────────────


def test_add_node_accepts_every_ears_pattern() -> None:
    # Regression: the old prefix rule rejected While/When/Where/If-then
    # requirements — four of the five real EARS patterns.
    patterns = [
        ("Ubiquitous Parse", "The system shall parse CSV rows."),
        ("Queue Dispatch State", "While the queue is non-empty, the system shall dispatch tasks."),
        ("Upload Validation Trigger", "When a file is uploaded, the system shall validate it."),
        ("Optional Request Logging", "Where logging is enabled, the system shall record each request."),
        ("Malformed Input Error", "If the input is malformed, then the system shall raise ValueError."),
    ]
    graph = _para_graph()
    tool = GraphWriteTool(graph=graph)
    for title, content in patterns:
        result = tool._execute(
            operation="add_node", node_type="HLR", parent_id="PARA-0001",
            title=title, content=content,
        )
        assert result.startswith("OK"), f"{content!r} rejected: {result}"
    assert len(graph.added) == len(patterns)


def test_add_node_bad_requirement_wording_rejected() -> None:
    graph = _para_graph()
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="add_node", node_type="HLR", parent_id="PARA-0001",
        title="Accept CSV Input", content="Accept CSV input files.",
    )
    assert result.startswith("ERROR")
    assert "The system shall" in result


def test_add_node_para_placeholder_rejected() -> None:
    graph = _para_graph()
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="add_node", node_type="HLR", parent_id="PARA-0001",
        title="Accept CSV Input", content="The system shall PARA-0012.",
    )
    assert result.startswith("ERROR")
    assert "PARA-0012" in result


# ── add_node: content length + duplicate sibling content ─────────────────────


def test_add_node_short_contract_content_rejected() -> None:
    graph = _StubGraph([_n("MOD-0001", "MODULE", title="CSV Module",
                           content="Handles CSV parsing responsibilities.")])
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="add_node", node_type="CONTRACT", parent_id="MOD-0001",
        title="CSV Module Contract", content="short stub",
    )
    assert result.startswith("ERROR")
    assert "50" in result


def test_add_node_duplicate_sibling_content_rejected() -> None:
    graph = _para_graph()
    graph._by_id["HLR-0001"] = _n(
        "HLR-0001", "HLR", parent_id="PARA-0001", title="Accept CSV Input",
        content="The system shall accept CSV input files.",
    )
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="add_node", node_type="HLR", parent_id="PARA-0001",
        title="Accept Input Files",
        content="the system shall accept csv input files.",
    )
    assert result.startswith("ERROR")
    assert "HLR-0001" in result


# ── add_node: CASE trace membership ──────────────────────────────────────────


def _suite_graph() -> _StubGraph:
    return _StubGraph([
        _n("SUITE-0001", "SUITE", title="Test Strategy",
           content="Strategy body long enough to pass the length check."),
        _n("HLR-0001", "HLR", title="Accept CSV Input",
           content="The system shall accept CSV input files."),
    ])


def test_add_case_without_trace_rejected() -> None:
    graph = _suite_graph()
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="add_node", node_type="CASE_HLR", parent_id="SUITE-0001",
        title="Verify CSV Acceptance",
        content="Given a valid CSV file, when ingested, then it is accepted.",
    )
    assert result.startswith("ERROR")
    assert "trace" in result


def test_add_case_wrong_trace_type_rejected() -> None:
    graph = _suite_graph()
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="add_node", node_type="CASE_HLR", parent_id="SUITE-0001",
        title="Verify CSV Acceptance",
        content="Given a valid CSV file, when ingested, then it is accepted.",
        trace_to='["SUITE-0001"]',
    )
    assert result.startswith("ERROR")
    assert "SUITE-0001" in result


def test_add_case_valid_trace_accepted() -> None:
    graph = _suite_graph()
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="add_node", node_type="CASE_HLR", parent_id="SUITE-0001",
        title="Verify CSV Acceptance",
        content="Given a valid CSV file, when ingested, then it is accepted.",
        trace_to='["HLR-0001"]',
    )
    assert result.startswith("OK")
    assert len(graph.added) == 1


# ── valid writes still pass ──────────────────────────────────────────────────


def test_add_valid_hlr_accepted() -> None:
    graph = _para_graph()
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="add_node", node_type="HLR", parent_id="PARA-0001",
        title="Accept CSV Input",
        content="The system shall accept CSV input files.",
    )
    assert result.startswith("OK")
    assert len(graph.added) == 1


# ── update_node enforcement ──────────────────────────────────────────────────


def _hlr_graph() -> _StubGraph:
    return _StubGraph([
        _n("PARA-0001", "PARA", title="Input Handling", content="Paragraph."),
        _n("HLR-0001", "HLR", parent_id="PARA-0001", title="Accept CSV Input",
           content="The system shall accept CSV input files."),
        _n("HLR-0002", "HLR", parent_id="PARA-0001", title="Reject Bad Input",
           content="The system shall reject malformed input files."),
    ])


def test_update_node_bad_wording_rejected() -> None:
    graph = _hlr_graph()
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="update_node", node_id="HLR-0001",
        content="Now handles CSV input.",
    )
    assert result.startswith("ERROR")
    assert "The system shall" in result
    assert graph.updated == []


def test_update_node_title_collision_rejected() -> None:
    graph = _hlr_graph()
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="update_node", node_id="HLR-0002", title="Accept CSV Input",
    )
    assert result.startswith("ERROR")
    assert "HLR-0001" in result


def test_update_node_valid_change_accepted() -> None:
    graph = _hlr_graph()
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="update_node", node_id="HLR-0001",
        content="The system shall accept UTF-8 encoded CSV input files.",
    )
    assert result.startswith("OK")
    assert len(graph.updated) == 1


# ── trace ops enforcement ────────────────────────────────────────────────────


def _case_graph() -> _StubGraph:
    return _StubGraph([
        _n("SUITE-0001", "SUITE", title="Test Strategy", content="Strategy."),
        _n("HLR-0001", "HLR", title="Accept CSV Input",
           content="The system shall accept CSV input files."),
        _n("CASE_HLR-0001", "CASE_HLR", parent_id="SUITE-0001",
           title="Verify CSV Acceptance", content="Given/when/then body text.",
           trace_to=["HLR-0001"]),
    ])


def test_update_trace_wrong_type_rejected() -> None:
    graph = _case_graph()
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="update_trace", node_id="CASE_HLR-0001",
        trace_to='["SUITE-0001"]',
    )
    assert result.startswith("ERROR")
    assert "SUITE-0001" in result


def test_add_traces_wrong_type_rejected() -> None:
    graph = _case_graph()
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="add_traces", node_id="CASE_HLR-0001",
        trace_to='["SUITE-0001"]',
    )
    assert result.startswith("ERROR")


def test_update_trace_valid_accepted() -> None:
    graph = _case_graph()
    graph._by_id["HLR-0002"] = _n(
        "HLR-0002", "HLR", title="Reject Bad Input",
        content="The system shall reject malformed input files.",
    )
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="update_trace", node_id="CASE_HLR-0001",
        trace_to='["HLR-0002"]',
    )
    assert result.startswith("OK")


# ── refresh_provenance (deterministic STALE_NODE closure) ────────────────────


def test_refresh_provenance_restamps_from_live_parent() -> None:
    graph = _hlr_graph()
    stale = graph._by_id["HLR-0001"]
    stale.properties = {DERIVED_FROM_HASH: "0" * 64}
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(operation="refresh_provenance", node_id="HLR-0001")
    assert result.startswith("OK")
    parent_hash = provenance_hash(graph._by_id["PARA-0001"].content)
    assert stale.properties[DERIVED_FROM_HASH] == parent_hash


def test_refresh_provenance_already_current_is_noop() -> None:
    graph = _hlr_graph()
    parent_hash = provenance_hash(graph._by_id["PARA-0001"].content)
    graph._by_id["HLR-0001"].properties = {DERIVED_FROM_HASH: parent_hash}
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(operation="refresh_provenance", node_id="HLR-0001")
    assert result.startswith("OK")
    assert "current" in result
    assert graph.updated == []


def test_refresh_provenance_missing_node_errors() -> None:
    graph = _hlr_graph()
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(operation="refresh_provenance", node_id="HLR-9999")
    assert result.startswith("ERROR")


# ── multi_graph_write: batch atomicity ───────────────────────────────────────


def test_multi_graph_write_rejects_batch_atomically() -> None:
    """One invalid op rejects the whole batch — nothing is applied."""
    graph = _para_graph()
    tool = MultiGraphWriteTool(graph)
    ops = (
        '[{"operation": "add_node", "node_type": "HLR",'
        ' "parent_id": "PARA-0001", "title": "Accept CSV Input",'
        ' "content": "The system shall accept CSV input files."},'
        ' {"operation": "add_node", "node_type": "HLR",'
        ' "parent_id": "PARA-0001", "title": "Bad One",'
        ' "content": "Not a shall statement."}]'
    )
    result = tool._execute(operations=ops)
    assert "0/2" in result
    assert "ERROR" in result
    assert "[1]" in result
    assert graph.added == []


def test_multi_graph_write_valid_batch_applies() -> None:
    graph = _para_graph()
    tool = MultiGraphWriteTool(graph)
    ops = (
        '[{"operation": "add_node", "node_type": "HLR",'
        ' "parent_id": "PARA-0001", "title": "Accept CSV Input",'
        ' "content": "The system shall accept CSV input files."},'
        ' {"operation": "add_node", "node_type": "HLR",'
        ' "parent_id": "PARA-0001", "title": "Reject Bad Input",'
        ' "content": "The system shall reject malformed input files."}]'
    )
    result = tool._execute(operations=ops)
    assert "2/2" in result
    assert len(graph.added) == 2


# ── add_node: CONTRACT public_api (specs/13) ────────────────────────────────


def _module_graph() -> _StubGraph:
    return _StubGraph([
        _n("MODULE-0001", "MODULE", title="Merge Sort Engine",
           content="Sorting responsibilities."),
    ])


_CONTRACT_CONTENT = (
    "def sort(items: list) -> None — sorts in place. "
    "Preconditions: items is a mutable sequence. "
    "Postconditions: items ascending. Errors: raises TypeError on "
    "incomparable elements."
)


def test_add_contract_without_public_api_rejected() -> None:
    graph = _module_graph()
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="add_node", node_type="CONTRACT", parent_id="MODULE-0001",
        title="Merge Sort Contract", content=_CONTRACT_CONTENT,
    )
    assert result.startswith("ERROR")
    assert "public_api" in result
    assert graph.added == []


def test_add_contract_with_valid_public_api_accepted() -> None:
    graph = _module_graph()
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="add_node", node_type="CONTRACT", parent_id="MODULE-0001",
        title="Merge Sort Contract", content=_CONTRACT_CONTENT,
        properties=(
            '{"public_api": [{"module": "merge_sort", "symbol": "sort",'
            ' "kind": "function",'
            ' "signature": "def sort(items: list) -> None"}]}'
        ),
    )
    assert result.startswith("OK")
    assert len(graph.added) == 1


def test_add_contract_with_malformed_public_api_rejected() -> None:
    graph = _module_graph()
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="add_node", node_type="CONTRACT", parent_id="MODULE-0001",
        title="Merge Sort Contract", content=_CONTRACT_CONTENT,
        properties='{"public_api": [{"module": "merge_sort"}]}',
    )
    assert result.startswith("ERROR")
    assert "public_api" in result
    assert graph.added == []


# ── title collision with parent (TITLE_COLLIDES_WITH_PARENT at write time) ───


def test_add_node_title_colliding_with_parent_rejected() -> None:
    """Live gap (topological_sort r3): LLR-0073 was written with the same
    title as its parent HLR-0077 — the write path must reject it."""
    graph = _StubGraph([
        _n("PARA-0001", "PARA", title="Public API", content="Section text."),
        _n("HLR-0077", "HLR", parent_id="PARA-0001",
           title="Return Descendant Set",
           content="The system shall return the descendant set."),
    ])
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="add_node", node_type="LLR", parent_id="HLR-0077",
        title="Return Descendant Set",
        content="The system shall return the transitive descendant closure.",
    )
    assert result.startswith("ERROR")
    assert "HLR-0077" in result
    assert graph.added == []


def test_update_node_retitle_to_parent_title_rejected() -> None:
    graph = _StubGraph([
        _n("HLR-0077", "HLR", title="Return Descendant Set",
           content="The system shall return the descendant set."),
        _n("LLR-0073", "LLR", parent_id="HLR-0077", title="Descendant Closure",
           content="The system shall return the transitive closure."),
    ])
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="update_node", node_id="LLR-0073",
        title="return descendant set",
    )
    assert result.startswith("ERROR")
    assert "HLR-0077" in result
    assert graph.updated == []


def test_multi_graph_write_rejects_child_titled_as_pending_parent() -> None:
    """Parent created earlier in the same batch: the batch prevalidation
    must see the pending parent's title, not only the live graph."""
    graph = _StubGraph([
        _n("DOC-0001", "DOCUMENT", content="doc body"),
        _n("PARA-0001", "PARA", parent_id="DOC-0001",
           title="Graph Queries", content="Section text."),
    ])
    tool = MultiGraphWriteTool(graph=graph)
    import json as _json
    ops = _json.dumps([
        {
            "operation": "add_node", "node_type": "HLR", "node_id": "HLR-0001",
            "parent_id": "PARA-0001", "title": "Return Descendant Set",
            "content": "The system shall return the descendant set.",
        },
        {
            "operation": "add_node", "node_type": "LLR", "node_id": "LLR-0001",
            "parent_id": "HLR-0001", "title": "Return Descendant Set",
            "content": "The system shall return the transitive closure.",
        },
    ])
    result = tool._execute(operations=ops)
    assert "0/2 operations succeeded" in result
    assert "HLR-0001" in result
    assert graph.added == []


def test_add_para_with_identical_sibling_content_accepted() -> None:
    """PARAs are exempt from byte-identical sibling rejection — a document
    may repeat the same sentence in two sections (specs/12 §3.5/§3.6)."""
    graph = _StubGraph([
        _n("DOC-0001", "DOCUMENT", content="doc body"),
        _n("PARA-0001", "PARA", parent_id="DOC-0001",
           title="Determinism Property", content="Output is deterministic."),
    ])
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="add_node", node_type="PARA", parent_id="DOC-0001",
        title="Ordering Validity", content="Output is deterministic.",
    )
    assert result.startswith("OK")
    assert len(graph.added) == 1


# ── non_normative marking route (U6 — graph_update_node) ─────────────────────


def test_update_para_marking_without_rationale_rejected() -> None:
    graph = _para_graph()
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="update_node", node_id="PARA-0001",
        properties='{"non_normative": true}',
    )
    assert result.startswith("ERROR")
    assert "non_normative_rationale" in result
    assert graph.updated == []


def test_update_para_marking_with_valid_rationale_accepted() -> None:
    graph = _para_graph()
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="update_node", node_id="PARA-0001",
        properties='{"non_normative": true, '
                   '"non_normative_rationale": "duplicate-of-PARA-0002"}',
    )
    assert result.startswith("OK")
    assert len(graph.updated) == 1


def test_update_non_para_marking_rejected() -> None:
    graph = _StubGraph([
        _n("HLR-0001", "HLR", title="Sort Stability Guarantee",
           content="The system shall keep sorting stable."),
    ])
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="update_node", node_id="HLR-0001",
        properties='{"non_normative": true, '
                   '"non_normative_rationale": "background/context"}',
    )
    assert result.startswith("ERROR")
    assert "PARA" in result
    assert graph.updated == []


def test_update_para_marking_merges_with_existing_properties() -> None:
    """Rationale supplied in a second call must validate against the merged
    property bag, not the delta alone."""
    graph = _StubGraph([
        _n("DOC-0001", "DOCUMENT", content="doc body"),
        _n("PARA-0001", "PARA", parent_id="DOC-0001",
           title="Input Handling", content="Paragraph text.",
           properties={"non_normative": True,
                       "non_normative_rationale": "background/context"}),
    ])
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="update_node", node_id="PARA-0001",
        properties='{"non_normative_rationale": "example/illustration"}',
    )
    assert result.startswith("OK")


def test_add_para_with_invalid_marking_rejected() -> None:
    graph = _para_graph()
    tool = GraphWriteTool(graph=graph)
    result = tool._execute(
        operation="add_node", node_type="PARA", parent_id="DOC-0001",
        title="Scene Setting Prose", content="Background story.",
        properties='{"non_normative": true}',
    )
    assert result.startswith("ERROR")
    assert "non_normative_rationale" in result
    assert graph.added == []
