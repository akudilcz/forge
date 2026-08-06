"""High-risk coverage for the staleness and drift detectors added in the
zero-truncation refactor. These are the checks that surface silent drift
between graph nodes and workspace artefacts."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import mock

from backend.analysis.gap_analyser import GapAnalyser
from backend.analysis.gaps import GapType
from backend.graph.models import GraphNode
from backend.graph.provenance import DERIVED_FROM_HASH, provenance_hash


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


def test_matching_provenance_hash_not_stale() -> None:
    """Child stamped against the parent's current content is fresh —
    regardless of any timestamp relationship."""
    parent = _node("DOC-1", "DOCUMENT", content="doc body",
                   updated_at=datetime.now(UTC))
    child = _node(
        "PARA-1", "PARA", parent_id="DOC-1", content="body",
        updated_at=datetime.now(UTC) - timedelta(days=10),
        properties={DERIVED_FROM_HASH: provenance_hash("doc body")},
    )
    gaps = GapAnalyser()._check_staleness(_Graph([parent, child]), child)
    assert gaps == []


def test_parent_content_change_emits_stale_node() -> None:
    """STALE_NODE fires iff stored hash != current parent content hash."""
    parent = _node("DOC-1", "DOCUMENT", content="NEW doc body")
    child = _node(
        "PARA-1", "PARA", parent_id="DOC-1", content="body",
        properties={DERIVED_FROM_HASH: provenance_hash("OLD doc body")},
    )
    gaps = GapAnalyser()._check_staleness(_Graph([parent, child]), child)
    assert len(gaps) == 1
    assert gaps[0].type == GapType.STALE_NODE
    assert gaps[0].node_id == "PARA-1"
    assert gaps[0].context["parent_id"] == "DOC-1"


def test_metadata_only_parent_touch_never_stales_children() -> None:
    """The hash covers parent CONTENT only — properties/trace/title touches
    of the parent can never cascade STALE_NODE onto children."""
    parent = _node(
        "DOC-1", "DOCUMENT", content="doc body", title="Renamed Title",
        properties={"chunked": True}, trace_to=["X-1"],
        updated_at=datetime.now(UTC),
    )
    child = _node(
        "PARA-1", "PARA", parent_id="DOC-1", content="body",
        updated_at=datetime.now(UTC) - timedelta(days=10),
        properties={DERIVED_FROM_HASH: provenance_hash("doc body")},
    )
    gaps = GapAnalyser()._check_staleness(_Graph([parent, child]), child)
    assert gaps == []


def test_unstamped_node_is_not_flagged_but_logged_loud() -> None:
    """Legacy nodes without derived_from_hash are backfilled at schema
    migration; if the analyser ever meets one mid-run it must NOT guess a
    verdict — it emits no gap and logs loudly."""
    parent = _node("HLR-1", "HLR", content="req text")
    child = _node("LLR-1", "LLR", parent_id="HLR-1", content="body")
    with mock.patch(
        "backend.analysis.gap_analyser_integrity.forge_logger"
    ) as logger:
        gaps = GapAnalyser()._check_staleness(_Graph([parent, child]), child)
    assert gaps == []
    assert logger.emit.called
    assert logger.emit.call_args.args[0] == "WARNING"


def test_workspace_sync_types_skip_staleness() -> None:
    parent = _node("DESIGN-1", "DESIGN", content="new design body")
    child = _node(
        "CODE-1", "CODE", parent_id="DESIGN-1", content="src ref",
        properties={DERIVED_FROM_HASH: provenance_hash("old design body")},
    )
    gaps = GapAnalyser()._check_staleness(_Graph([parent, child]), child)
    assert gaps == []


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


# ── CONTRACT_VIOLATION vs structured public_api (specs/13) ───────

_TOPO_API = [
    {
        "module": "toposort", "symbol": "descendants", "kind": "function",
        "signature": (
            "descendants(graph: Mapping[Any, Iterable[Any]], node: Any) "
            "-> set[Any]"
        ),
    },
    {
        "module": "toposort", "symbol": "CyclicGraphError", "kind": "class",
        "signature": "class CyclicGraphError(ValueError)",
    },
]


def _module_with_design(design_content: str, contract_props: dict[str, Any]) -> _Graph:
    module = _node("MODULE-1", "MODULE", content="Module body.")
    contract = _node(
        "CONTRACT-1", "CONTRACT", parent_id="MODULE-1",
        content="Public interface: descendants(graph, node).",
        properties=contract_props,
    )
    design = _node(
        "DESIGN-1", "DESIGN", parent_id="MODULE-1", content=design_content,
    )
    return _Graph([module, contract, design])


def test_alignment_internal_helpers_not_flagged_with_public_api() -> None:
    """Live gaps (topological_sort r3, DESIGN-0001..0006): internal methods
    of private classes are NOT contract violations once the CONTRACT carries
    structured public_api."""
    graph = _module_with_design(
        "class _Graph (internal): in_degree(node: Any) -> int; "
        "successors(node: Any) -> list[Any]; decompose(x: int) -> list[Any].",
        {"public_api": _TOPO_API},
    )
    gaps = GapAnalyser()._check_design_contract_alignment(
        graph, graph.all_nodes(),
    )
    assert gaps == []


def test_alignment_public_api_signature_conflict_flagged() -> None:
    graph = _module_with_design(
        "descendants(node: Any) -> set[Any] is the only entry point.",
        {"public_api": _TOPO_API},
    )
    gaps = GapAnalyser()._check_design_contract_alignment(
        graph, graph.all_nodes(),
    )
    assert len(gaps) == 1
    assert gaps[0].type == GapType.CONTRACT_VIOLATION
    assert gaps[0].node_id == "DESIGN-1"
    assert gaps[0].context["conflicting_functions"] == ["descendants"]


def test_alignment_legacy_contract_without_public_api_keeps_token_check() -> None:
    """Contracts authored before specs/13 keep the older token-subset
    behaviour (documented fallback)."""
    graph = _module_with_design(
        "Provides decompose(component) plus descendants(graph, node).",
        {},
    )
    gaps = GapAnalyser()._check_design_contract_alignment(
        graph, graph.all_nodes(),
    )
    assert len(gaps) == 1
    assert gaps[0].type == GapType.CONTRACT_VIOLATION
    assert gaps[0].context["extra_functions"] == ["decompose"]
