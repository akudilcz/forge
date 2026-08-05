"""Behavioural tests for context bundle assembly (backend/graph/context.py)."""

from __future__ import annotations

from typing import Any

from backend.graph.context import (
    _build_inner,
    _build_middle,
    build_context_bundle,
)
from backend.graph.engine import ProjectGraph
from backend.graph.models import NodeType


def _make_graph() -> ProjectGraph:
    """In-memory-only graph: context assembly uses sync NX methods exclusively."""
    return ProjectGraph(":memory:")


def _add(
    g: ProjectGraph,
    node_id: str,
    node_type: str = NodeType.PARA.value,
    parent_id: str | None = None,
    trace_to: list[str] | None = None,
    content: str = "",
) -> None:
    g._nx_add_node({
        "node_id": node_id,
        "node_type": node_type,
        "title": f"title {node_id}",
        "lifecycle": "draft",
        "parent_id": parent_id,
        "trace_to": trace_to or [],
        "content": content or f"content {node_id}",
    })


# ── build_context_bundle ─────────────────────────────────────────────────────


def test_missing_node_returns_empty_bundle() -> None:
    g = _make_graph()
    bundle = build_context_bundle(g, "nope")
    assert bundle == {"node_id": "nope", "inner": [], "middle": [], "outer": []}


def test_full_bundle_tiers_and_roles() -> None:
    g = _make_graph()
    _add(g, "PROJECT-1", node_type="PROJECT")
    _add(g, "ARCH-1", node_type="ARCHITECTURE", parent_id="PROJECT-1")
    _add(g, "MOD-1", node_type="MODULE", parent_id="ARCH-1")
    _add(g, "N", node_type="DESIGN", parent_id="MOD-1", trace_to=["LLR-1"])
    _add(g, "CHILD-1", node_type="CODE", parent_id="N")
    _add(g, "CTR-1", node_type=NodeType.CONTRACT.value, parent_id="MOD-1")
    _add(g, "SIB-1", node_type="DESIGN", parent_id="MOD-1", content="x" * 300)
    _add(g, "HLR-1", node_type="HLR", parent_id="PARA-1")
    _add(g, "PARA-1", node_type="PARA")
    _add(g, "LLR-1", node_type="LLR", parent_id="HLR-1")
    _add(g, "TRACER-1", node_type="CASE_LLR", trace_to=["N"])

    bundle = build_context_bundle(g, "N")

    inner_roles = {(e["role"], e["node_id"]) for e in bundle["inner"]}
    assert inner_roles == {
        ("parent", "MOD-1"), ("child", "CHILD-1"),
        ("contract", "CTR-1"), ("trace_to", "LLR-1"),
    }
    # INNER entries are full: content included.
    assert all("content" in e for e in bundle["inner"])

    middle = {e["role"]: e for e in bundle["middle"]}
    assert set(middle) == {"sibling", "trace_from"}
    assert middle["sibling"]["node_id"] == "SIB-1"
    # Sibling content summarised to 120 chars inside the summary string.
    assert "x" * 120 in middle["sibling"]["summary"]
    assert "x" * 121 not in middle["sibling"]["summary"]
    assert middle["trace_from"]["node_id"] == "TRACER-1"

    outer = {(e["role"], e["node_id"]) for e in bundle["outer"]}
    assert ("ancestor", "ARCH-1") in outer
    assert ("ancestor", "PROJECT-1") in outer
    assert ("trace_ancestor", "HLR-1") in outer
    assert ("trace_ancestor", "PARA-1") in outer


def test_trace_to_referencing_parent_not_duplicated() -> None:
    g = _make_graph()
    _add(g, "P")
    _add(g, "N", parent_id="P", trace_to=["P"])
    bundle = build_context_bundle(g, "N")
    roles = [(e["role"], e["node_id"]) for e in bundle["inner"]]
    assert roles == [("parent", "P")]


def test_dangling_trace_target_skipped_in_inner_and_outer() -> None:
    g = _make_graph()
    _add(g, "N", trace_to=["ghost"], parent_id="P")
    _add(g, "P")
    bundle = build_context_bundle(g, "N")
    assert all(e["node_id"] != "ghost" for e in bundle["inner"])
    assert bundle["outer"] == []


def test_outer_stops_at_dangling_ancestor() -> None:
    g = _make_graph()
    _add(g, "N", parent_id="P")
    _add(g, "P", parent_id="ghost-grandparent")
    bundle = build_context_bundle(g, "N")
    assert bundle["outer"] == []


def test_outer_skips_already_seen_ancestor() -> None:
    # G is both a trace_to target (INNER) and an ancestor: the outer walk
    # must skip it but still continue past it to the root.
    g = _make_graph()
    _add(g, "ROOT", node_type="PROJECT")
    _add(g, "G", parent_id="ROOT")
    _add(g, "P", parent_id="G")
    _add(g, "N", parent_id="P", trace_to=["G"])
    bundle = build_context_bundle(g, "N")
    outer_ids = [e["node_id"] for e in bundle["outer"]]
    assert outer_ids == ["ROOT"]
    assert any(e["role"] == "trace_to" and e["node_id"] == "G" for e in bundle["inner"])


def test_trace_ancestor_walk_stops_at_dangling_parent() -> None:
    g = _make_graph()
    _add(g, "N", parent_id="P", trace_to=["T"])
    _add(g, "P")
    _add(g, "T", parent_id="X")
    _add(g, "X", parent_id="ghost")
    bundle = build_context_bundle(g, "N")
    outer_ids = [e["node_id"] for e in bundle["outer"]]
    assert outer_ids == ["X"]


def test_trace_ancestor_already_seen_is_skipped() -> None:
    # Two trace targets sharing the same ancestor chain: the shared
    # ancestor appears once.
    g = _make_graph()
    _add(g, "N", parent_id="P", trace_to=["T1", "T2"])
    _add(g, "P")
    _add(g, "SHARED")
    _add(g, "T1", parent_id="SHARED")
    _add(g, "T2", parent_id="SHARED")
    bundle = build_context_bundle(g, "N")
    outer_ids = [e["node_id"] for e in bundle["outer"]]
    assert outer_ids == ["SHARED"]


# ── _build_inner seen-set contract ───────────────────────────────────────────


def test_inner_skips_child_already_seen() -> None:
    g = _make_graph()
    _add(g, "N", parent_id="N")  # self-parent: N is its own child
    node = g.node_sync("N")
    assert node is not None
    inner = _build_inner(g, node, "N", {"N"})
    assert inner == []


def test_inner_skips_contract_already_seen() -> None:
    g = _make_graph()
    _add(g, "P")
    _add(g, "N", parent_id="P")
    _add(g, "CTR", node_type=NodeType.CONTRACT.value, parent_id="P")
    node = g.node_sync("N")
    assert node is not None
    inner = _build_inner(g, node, "N", {"N", "CTR"})
    assert [e["node_id"] for e in inner] == ["P"]


# ── _build_middle with a degenerate graph ────────────────────────────────────


class _StubGraph:
    """Graph stub whose nodes_tracing_to returns ids node_sync cannot resolve."""

    def __init__(self, tracers: list[str], nodes: dict[str, Any]) -> None:
        self._tracers = tracers
        self._nodes = nodes

    def siblings_sync(self, node_id: str) -> list[Any]:
        return []

    def nodes_tracing_to(self, node_id: str) -> list[str]:
        return self._tracers

    def node_sync(self, node_id: str) -> Any:
        return self._nodes.get(node_id)


def test_middle_skips_seen_and_unresolvable_tracers() -> None:
    real = _make_graph()
    _add(real, "TR", trace_to=["N"])
    tracer_node = real.node_sync("TR")
    stub = _StubGraph(
        tracers=["already-seen", "ghost", "TR"],
        nodes={"TR": tracer_node},
    )
    middle = _build_middle(stub, "N", {"N", "already-seen"})  # type: ignore[arg-type]
    assert [e["node_id"] for e in middle] == ["TR"]
