"""High-risk coverage for the staleness and drift detectors added in the
zero-truncation refactor. These are the checks that surface silent drift
between graph nodes and workspace artefacts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from backend.analysis.gap_analyser import GapAnalyser
from backend.analysis.gaps import GapType
from backend.graph.models import GraphNode


def _node(nid: str, ntype: str, **kw: Any) -> GraphNode:
    return GraphNode(
        node_id=nid, node_type=ntype,
        parent_id=kw.get("parent_id", ""),
        title=kw.get("title", ""),
        content=kw.get("content", ""),
        trace_to=kw.get("trace_to", []),
        properties=kw.get("properties", {}),
        created_at=kw.get("created_at", datetime.now(UTC)),
        updated_at=kw.get("updated_at", datetime.now(UTC)),
        content_updated_at=kw.get("content_updated_at", None),
    )


class _Graph:
    def __init__(self, nodes: list[GraphNode]) -> None:
        self._by_id = {n.node_id: n for n in nodes}

    def all_nodes(self) -> list[GraphNode]:
        return list(self._by_id.values())

    def node_sync(self, nid: str) -> GraphNode | None:
        return self._by_id.get(nid)

    def children_sync(self, pid: str) -> list[GraphNode]:
        return [n for n in self._by_id.values() if n.parent_id == pid]


# ── STALE_NODE (content-aware) ───────────────────────────────────────────────


def test_metadata_only_parent_update_does_not_stale_children() -> None:
    """A properties/trace-only parent touch bumps updated_at but NOT
    content_updated_at — children must not be flagged STALE_NODE."""
    old = datetime.now(UTC) - timedelta(days=10)
    mid = datetime.now(UTC) - timedelta(days=5)
    new = datetime.now(UTC)
    parent = _node(
        "DOC-1", "DOCUMENT",
        updated_at=new,  # metadata touched just now (e.g. phase-2 chunking)
        content_updated_at=old,  # content itself unchanged for 10 days
    )
    child = _node("PARA-1", "PARA", parent_id="DOC-1", content="body",
                  updated_at=mid)
    gaps = GapAnalyser()._check_staleness(_Graph([parent, child]), child)
    assert gaps == []


def test_parent_content_change_still_emits_stale_node() -> None:
    old = datetime.now(UTC) - timedelta(days=10)
    new = datetime.now(UTC)
    parent = _node(
        "DOC-1", "DOCUMENT",
        updated_at=new,
        content_updated_at=new,  # real content change after child was written
    )
    child = _node("PARA-1", "PARA", parent_id="DOC-1", content="body",
                  updated_at=old)
    gaps = GapAnalyser()._check_staleness(_Graph([parent, child]), child)
    assert len(gaps) == 1
    assert gaps[0].type == GapType.STALE_NODE
    assert gaps[0].node_id == "PARA-1"


def test_child_newer_than_parent_content_change_not_stale() -> None:
    old = datetime.now(UTC) - timedelta(days=10)
    new = datetime.now(UTC)
    parent = _node("HLR-1", "HLR", updated_at=old, content_updated_at=old)
    child = _node("LLR-1", "LLR", parent_id="HLR-1", content="body",
                  updated_at=new)
    gaps = GapAnalyser()._check_staleness(_Graph([parent, child]), child)
    assert gaps == []


def test_node_without_explicit_content_ts_defaults_to_updated_at() -> None:
    """Nodes built without content_updated_at initialise it to updated_at,
    preserving the original timestamp semantics."""
    old = datetime.now(UTC) - timedelta(days=10)
    new = datetime.now(UTC)
    parent = _node("HLR-1", "HLR", updated_at=new)  # no content_updated_at
    child = _node("LLR-1", "LLR", parent_id="HLR-1", content="body",
                  updated_at=old)
    assert parent.content_updated_at == parent.updated_at
    gaps = GapAnalyser()._check_staleness(_Graph([parent, child]), child)
    assert len(gaps) == 1
    assert gaps[0].type == GapType.STALE_NODE


# ── STALE_ARCHITECTURE ───────────────────────────────────────────────────────


def test_stale_architecture_fires_above_threshold() -> None:
    old = datetime.now(UTC) - timedelta(days=30)
    new = datetime.now(UTC)
    arch = _node("ARCH-1", "ARCHITECTURE", parent_id="P", created_at=old)
    # 5 HLRs total; 2 are newer (40% > 20% threshold).
    hlrs = [
        _node(f"HLR-{i}", "HLR", parent_id="P", created_at=old) for i in range(3)
    ] + [
        _node(f"HLR-{i}", "HLR", parent_id="P", created_at=new) for i in range(3, 5)
    ]
    analyser = GapAnalyser()
    gaps = analyser._check_stale_architecture([arch, *hlrs])
    assert len(gaps) == 1
    assert gaps[0].type == GapType.STALE_ARCHITECTURE
    assert "ARCH-1" == gaps[0].node_id


def test_stale_architecture_below_threshold_no_gap() -> None:
    old = datetime.now(UTC) - timedelta(days=30)
    new = datetime.now(UTC)
    arch = _node("ARCH-1", "ARCHITECTURE", parent_id="P", created_at=old)
    # 10 HLRs, only 1 newer (10% < 20% threshold).
    hlrs = [
        _node(f"HLR-{i}", "HLR", parent_id="P", created_at=old) for i in range(9)
    ] + [_node("HLR-new", "HLR", parent_id="P", created_at=new)]
    analyser = GapAnalyser()
    gaps = analyser._check_stale_architecture([arch, *hlrs])
    assert gaps == []


def test_stale_architecture_no_newer_hlrs_skips() -> None:
    old = datetime.now(UTC) - timedelta(days=30)
    arch = _node("ARCH-1", "ARCHITECTURE", created_at=old)
    hlrs = [_node("HLR-1", "HLR", created_at=old - timedelta(days=1))]
    assert GapAnalyser()._check_stale_architecture([arch, *hlrs]) == []


def test_stale_architecture_empty_inputs_return_empty() -> None:
    analyser = GapAnalyser()
    assert analyser._check_stale_architecture([]) == []
    assert analyser._check_stale_architecture([_node("A", "ARCHITECTURE")]) == []


# ── STALE_SUITE ──────────────────────────────────────────────────────────────


def test_stale_suite_fires_when_requirements_newer() -> None:
    old = datetime.now(UTC) - timedelta(days=30)
    new = datetime.now(UTC)
    suite = _node("SUITE-1", "SUITE", created_at=old)
    reqs = [
        _node("HLR-1", "HLR", created_at=old),
        _node("LLR-1", "LLR", created_at=new),
        _node("LLR-2", "LLR", created_at=new),
        _node("LLR-3", "LLR", created_at=new),
    ]
    gaps = GapAnalyser()._check_stale_suite([suite, *reqs])
    assert len(gaps) == 1
    assert gaps[0].type == GapType.STALE_SUITE


def test_stale_suite_no_suite_no_gap() -> None:
    reqs = [_node("HLR-1", "HLR")]
    assert GapAnalyser()._check_stale_suite(reqs) == []


# ── STALE_CODE hash mismatch ─────────────────────────────────────────────────


def test_stale_code_fires_on_hash_mismatch() -> None:
    from backend.codegen.slice_gen import codegen_hash

    design = _node(
        "DES-1", "DESIGN",
        parent_id="MOD-1",
        content="NEW DESIGN BODY",
        properties={"codegen_hash": codegen_hash("OLD", "CONTRACT", "")},
    )
    contract = _node("CTR-1", "CONTRACT", parent_id="MOD-1", content="CONTRACT")
    graph_nodes = [design, contract]
    gaps = GapAnalyser()._check_stale_code(graph_nodes)
    assert len(gaps) == 1
    assert gaps[0].type == GapType.STALE_CODE
    assert "out-of-sync" in gaps[0].description


def test_stale_code_no_gap_when_hashes_match() -> None:
    from backend.codegen.slice_gen import codegen_hash

    design = _node(
        "DES-1", "DESIGN",
        parent_id="MOD-1",
        content="DESIGN BODY",
        properties={"codegen_hash": codegen_hash("DESIGN BODY", "CONTRACT", "")},
    )
    contract = _node("CTR-1", "CONTRACT", parent_id="MOD-1", content="CONTRACT")
    gaps = GapAnalyser()._check_stale_code([design, contract])
    assert gaps == []


def test_stale_code_codegen_error_surfaces() -> None:
    design = _node(
        "DES-1", "DESIGN",
        parent_id="MOD-1",
        properties={"codegen_error": "parser exploded"},
    )
    gaps = GapAnalyser()._check_stale_code([design])
    assert len(gaps) == 1
    assert "parser exploded" in gaps[0].context["codegen_error"]
