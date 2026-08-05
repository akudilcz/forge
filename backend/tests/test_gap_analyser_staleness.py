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
