"""Tests for task_builder, workspace_sync, phase_steps, and work_queue modules.

Maximise coverage across four modules with focused behavioural tests.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.graph.models import NodeType

if TYPE_CHECKING:
    from backend.work_queue import WorkQueueService

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_node(
    node_id: str,
    node_type: str = "HLR",
    content: str = "some content",
    parent_id: str | None = None,
    trace_to: list[str] | None = None,
    title: str = "Test Node",
    properties: dict[str, Any] | None = None,
) -> MagicMock:
    """Create a lightweight mock node with required attributes."""
    node = MagicMock()
    node.node_id = node_id
    node.node_type = node_type
    node.content = content
    node.parent_id = parent_id
    node.trace_to = trace_to or []
    node.title = title
    node.properties = properties or {}
    return node


def _make_graph(nodes: dict[str, MagicMock] | None = None) -> MagicMock:
    """Create a mock graph with standard methods."""
    graph = MagicMock()
    nodes = nodes or {}

    def node_sync(nid: str) -> MagicMock | None:
        return nodes.get(nid)

    def children_sync(nid: str) -> list[MagicMock]:
        return [n for n in nodes.values() if n.parent_id == nid]

    def all_nodes() -> list[MagicMock]:
        return list(nodes.values())

    def nodes_tracing_to(nid: str, source_type: str = "") -> list[str]:
        return [
            n.node_id
            for n in nodes.values()
            if nid in (n.trace_to or []) and (not source_type or n.node_type == source_type)
        ]

    graph.node_sync = MagicMock(side_effect=node_sync)
    graph.children_sync = MagicMock(side_effect=children_sync)
    graph.all_nodes = MagicMock(side_effect=all_nodes)
    graph.nodes_tracing_to = MagicMock(side_effect=nodes_tracing_to)
    graph.allocate_node_id = AsyncMock(side_effect=lambda prefix: f"{prefix}-001")
    graph.add_node = AsyncMock()
    return graph


def _make_gap(
    gap_type: GapType = GapType.UNCOVERED_PARA,
    node_id: str = "hlr-001",
    description: str = "test gap",
) -> Gap:
    return Gap(
        type=gap_type,
        priority=GapPriority.REQUIREMENTS_HLR,
        node_id=node_id,
        description=description,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 1. task_builder
# ═══════════════════════════════════════════════════════════════════════════════


class TestNeedsPrefetch:
    """needs_prefetch returns False only for STALE_TRACE_TO (agent receives
    the stale refs in Gap.context) — every other type gets full context."""

    def test_unchunked_document_needs_prefetch(self) -> None:
        """UNCHUNKED_DOCUMENT now receives the DOCUMENT content inline rather
        than relying on the agent to graph_read for it."""
        from backend.crew.task_builder import needs_prefetch

        assert needs_prefetch(GapType.UNCHUNKED_DOCUMENT) is True

    def test_stale_trace_to_no_prefetch(self) -> None:
        from backend.crew.task_builder import needs_prefetch

        assert needs_prefetch(GapType.STALE_TRACE_TO) is False

    def test_uncovered_para_needs_prefetch(self) -> None:
        from backend.crew.task_builder import needs_prefetch

        assert needs_prefetch(GapType.UNCOVERED_PARA) is True

    def test_undesigned_needs_prefetch(self) -> None:
        from backend.crew.task_builder import needs_prefetch

        assert needs_prefetch(GapType.UNDESIGNED) is True


class TestBuildAncestorContext:
    """build_ancestor_context walks parent chain collecting content."""

    def test_node_not_found_returns_empty(self) -> None:
        from backend.crew.task_builder import build_ancestor_context

        graph = _make_graph({})
        assert build_ancestor_context(graph, "missing") == ""

    def test_single_node_no_parent(self) -> None:
        from backend.crew.task_builder import build_ancestor_context

        n = _make_node("n1", content="Hello")
        n.parent_id = None
        graph = _make_graph({"n1": n})
        result = build_ancestor_context(graph, "n1")
        assert "[HLR n1]" in result
        assert "Hello" in result

    def test_walks_parent_chain(self) -> None:
        from backend.crew.task_builder import build_ancestor_context

        # DOCUMENT nodes are included as title-only breadcrumbs, not full content
        parent = _make_node("p1", node_type="DOCUMENT", content="Parent content skipped")
        parent.parent_id = None
        parent.title = "Spec Doc"
        child = _make_node("c1", content="Child", parent_id="p1")
        graph = _make_graph({"p1": parent, "c1": child})
        result = build_ancestor_context(graph, "c1")
        # DOCUMENT breadcrumb appears before child content (reversed order)
        assert "DOCUMENT p1" in result
        assert "Child" in result
        assert "Parent content skipped" not in result

    def test_circular_ref_stops(self) -> None:
        from backend.crew.task_builder import build_ancestor_context

        # Node points to itself as parent — should not infinite loop
        n = _make_node("loop1", content="Loopy", parent_id="loop1")
        graph = _make_graph({"loop1": n})
        result = build_ancestor_context(graph, "loop1")
        assert "Loopy" in result

    def test_skips_empty_content_nodes(self) -> None:
        from backend.crew.task_builder import build_ancestor_context

        parent = _make_node("p1", content="", parent_id=None)
        child = _make_node("c1", content="Child", parent_id="p1")
        graph = _make_graph({"p1": parent, "c1": child})
        result = build_ancestor_context(graph, "c1")
        assert "p1" not in result
        assert "Child" in result


class TestBuildTraceToContext:
    """build_trace_to_context fetches referenced nodes from trace_to."""

    def test_node_not_found_returns_empty(self) -> None:
        from backend.crew.task_builder import build_trace_to_context

        graph = _make_graph({})
        assert build_trace_to_context(graph, "missing") == ""

    def test_no_trace_to_returns_empty(self) -> None:
        """Empty trace_to: return empty string — no silent ancestor fallback."""
        from backend.crew.task_builder import build_trace_to_context

        n = _make_node("n1", content="Hello", parent_id=None)
        n.trace_to = []
        graph = _make_graph({"n1": n})
        assert build_trace_to_context(graph, "n1") == ""

    def test_trace_to_refs_found(self) -> None:
        from backend.crew.task_builder import build_trace_to_context

        ref = _make_node("ref1", node_type="HLR", content="Requirement text")
        n = _make_node("n1", content="Case", parent_id=None)
        n.trace_to = ["ref1"]
        graph = _make_graph({"n1": n, "ref1": ref})
        result = build_trace_to_context(graph, "n1")
        assert "Requirement text" in result
        assert "[HLR ref1]" in result

    def test_trace_to_ref_missing_raises(self) -> None:
        """Unresolved trace_to raises — fail loud rather than silent fallback."""
        import pytest

        from backend.crew.task_builder import build_trace_to_context

        n = _make_node("n1", content="Case", parent_id=None)
        n.trace_to = ["missing_ref"]
        graph = _make_graph({"n1": n})
        with pytest.raises(RuntimeError, match="missing_ref"):
            build_trace_to_context(graph, "n1")


class TestBuildSiblingReqContext:
    """build_sibling_req_context lists siblings for dedup checking."""

    def test_node_not_found(self) -> None:
        from backend.crew.task_builder import build_sibling_req_context

        graph = _make_graph({})
        assert build_sibling_req_context(graph, "missing") == ""

    def test_no_parent(self) -> None:
        from backend.crew.task_builder import build_sibling_req_context

        n = _make_node("n1", parent_id=None)
        graph = _make_graph({"n1": n})
        assert build_sibling_req_context(graph, "n1") == ""

    def test_siblings_with_content(self) -> None:
        from backend.crew.task_builder import build_sibling_req_context

        parent = _make_node("p1", node_type="DOCUMENT", content="", parent_id=None)
        s1 = _make_node("s1", node_type="HLR", content="Sibling one", parent_id="p1")
        s2 = _make_node("s2", node_type="HLR", content="Sibling two", parent_id="p1")
        target = _make_node("t1", node_type="HLR", content="Target", parent_id="p1")
        graph = _make_graph({"p1": parent, "s1": s1, "s2": s2, "t1": target})
        result = build_sibling_req_context(graph, "t1")
        assert "SIBLING REQUIREMENTS" in result
        assert "[s1]" in result
        assert "[s2]" in result
        assert "[t1]" not in result  # Excludes self

    def test_no_matching_siblings(self) -> None:
        from backend.crew.task_builder import build_sibling_req_context

        parent = _make_node("p1", node_type="DOCUMENT", content="", parent_id=None)
        target = _make_node("t1", node_type="HLR", content="Target", parent_id="p1")
        other = _make_node("o1", node_type="LLR", content="Different type", parent_id="p1")
        graph = _make_graph({"p1": parent, "t1": target, "o1": other})
        result = build_sibling_req_context(graph, "t1")
        assert result == ""


class TestBuildAllPeersContext:
    """build_all_peers_context returns all same-type nodes excluding self."""

    def test_no_peers(self) -> None:
        from backend.crew.task_builder import build_all_peers_context

        n = _make_node("n1", node_type="HLR", content="Content")
        graph = _make_graph({"n1": n})
        assert build_all_peers_context(graph, "n1", "HLR") == ""

    def test_case_type_filtering(self) -> None:
        from backend.crew.task_builder import build_all_peers_context

        target = _make_node("c1", node_type="CASE_HLR", content="Target case")
        peer_hlr = _make_node("c2", node_type="CASE_HLR", content="HLR case")
        peer_llr = _make_node("c3", node_type="CASE_LLR", content="LLR case")
        graph = _make_graph({"c1": target, "c2": peer_hlr, "c3": peer_llr})
        result = build_all_peers_context(graph, "c1", "CASE_HLR")
        assert "[c2]" in result
        assert "[c3]" not in result  # CASE_LLR filtered out for CASE_HLR

    def test_non_case_includes_all(self) -> None:
        from backend.crew.task_builder import build_all_peers_context

        target = _make_node("h1", node_type="HLR", content="Target")
        peer = _make_node("h2", node_type="HLR", content="Peer content")
        graph = _make_graph({"h1": target, "h2": peer})
        result = build_all_peers_context(graph, "h1", "HLR")
        assert "ALL HLR REQUIREMENTS" in result
        assert "[h2]" in result

    def test_peers_with_trace_to(self) -> None:
        from backend.crew.task_builder import build_all_peers_context

        target = _make_node("h1", node_type="HLR", content="Target")
        peer = _make_node("h2", node_type="HLR", content="Peer")
        peer.trace_to = ["ref-1"]
        graph = _make_graph({"h1": target, "h2": peer})
        result = build_all_peers_context(graph, "h1", "HLR")
        assert "trace_to=['ref-1']" in result


class TestFindSuiteId:
    """find_suite_id returns the first SUITE node ID or empty string."""

    def test_suite_found(self) -> None:
        from backend.crew.task_builder import find_suite_id

        s = _make_node("suite-1", node_type="SUITE", content="Suite")
        graph = _make_graph({"suite-1": s})
        assert find_suite_id(graph) == "suite-1"

    def test_no_suite(self) -> None:
        from backend.crew.task_builder import find_suite_id

        n = _make_node("h1", node_type="HLR", content="Not a suite")
        graph = _make_graph({"h1": n})
        assert find_suite_id(graph) == ""


class TestBuildExistingCasesContext:
    """build_existing_cases_context lists CASE_HLR or CASE_LLR nodes."""

    def test_hlr_type(self) -> None:
        from backend.crew.task_builder import build_existing_cases_context

        c = _make_node("ch1", node_type="CASE_HLR", content="HLR case content")
        c.trace_to = ["hlr-1"]
        graph = _make_graph({"ch1": c})
        result = build_existing_cases_context(graph, "hlr")
        assert "EXISTING HLR CASE NODES" in result
        assert "[ch1]" in result

    def test_llr_type(self) -> None:
        from backend.crew.task_builder import build_existing_cases_context

        c = _make_node("cl1", node_type="CASE_LLR", content="LLR case content")
        c.trace_to = []
        graph = _make_graph({"cl1": c})
        result = build_existing_cases_context(graph, "llr")
        assert "EXISTING LLR CASE NODES" in result

    def test_no_cases(self) -> None:
        from backend.crew.task_builder import build_existing_cases_context

        graph = _make_graph({})
        assert build_existing_cases_context(graph, "hlr") == ""


class TestBuildExistingLlrsContext:
    """build_existing_llrs_context returns formatted LLR listing."""

    def test_llr_listing(self) -> None:
        from backend.crew.task_builder import build_existing_llrs_context

        llr = _make_node("llr-1", node_type="LLR", content="LLR content", parent_id="hlr-1")
        graph = _make_graph({"llr-1": llr})
        result = build_existing_llrs_context(graph)
        assert "EXISTING LLR NODES" in result
        assert "[llr-1]" in result
        assert "parent=hlr-1" in result

    def test_no_llrs(self) -> None:
        from backend.crew.task_builder import build_existing_llrs_context

        graph = _make_graph({})
        assert build_existing_llrs_context(graph) == ""


class TestBuildModuleDesignContext:
    """build_module_design_context fetches MODULE + CONTRACT + DESIGN context."""

    def test_llr_not_found(self) -> None:
        from backend.crew.task_builder import build_module_design_context

        graph = _make_graph({})
        assert build_module_design_context(graph, "missing") == ""

    def test_llr_no_parent(self) -> None:
        from backend.crew.task_builder import build_module_design_context

        llr = _make_node("llr-1", node_type="LLR", content="LLR", parent_id=None)
        graph = _make_graph({"llr-1": llr})
        assert build_module_design_context(graph, "llr-1") == ""

    def test_no_module_tracing(self) -> None:
        from backend.crew.task_builder import build_module_design_context

        llr = _make_node("llr-1", node_type="LLR", content="LLR", parent_id="hlr-1")
        hlr = _make_node("hlr-1", node_type="HLR", content="HLR")
        graph = _make_graph({"llr-1": llr, "hlr-1": hlr})
        assert build_module_design_context(graph, "llr-1") == ""

    def test_full_module_context(self) -> None:
        from backend.crew.task_builder import build_module_design_context

        llr = _make_node("llr-1", node_type="LLR", content="LLR", parent_id="hlr-1")
        hlr = _make_node("hlr-1", node_type="HLR", content="HLR")
        module = _make_node(
            "mod-1",
            node_type="MODULE",
            content="Module plan",
            parent_id=None,
            trace_to=["hlr-1"],
        )
        contract = _make_node(
            "con-1",
            node_type="CONTRACT",
            content="Contract spec",
            parent_id="mod-1",
        )
        design = _make_node(
            "des-1",
            node_type="DESIGN",
            content="Design detail",
            parent_id="mod-1",
            trace_to=["llr-1"],
        )
        graph = _make_graph(
            {
                "llr-1": llr,
                "hlr-1": hlr,
                "mod-1": module,
                "con-1": contract,
                "des-1": design,
            }
        )
        result = build_module_design_context(graph, "llr-1")
        assert "OWNING MODULE" in result
        assert "[MODULE mod-1]" in result
        assert "[CONTRACT con-1]" in result
        assert "[DESIGN des-1]" in result
        assert "1 design(s)" in result

    def test_module_node_missing(self) -> None:
        """Module ID returned but node_sync for it returns None."""
        from backend.crew.task_builder import build_module_design_context

        llr = _make_node("llr-1", node_type="LLR", content="LLR", parent_id="hlr-1")
        hlr = _make_node("hlr-1", node_type="HLR", content="HLR")
        graph = _make_graph({"llr-1": llr, "hlr-1": hlr})
        # Override nodes_tracing_to to return an ID for which node_sync returns None
        graph.nodes_tracing_to = MagicMock(return_value=["mod-ghost"])
        assert build_module_design_context(graph, "llr-1") == ""


class TestBuildAllHlrsContext:
    """build_all_hlrs_context returns formatted HLR listing."""

    def test_hlr_listing(self) -> None:
        from backend.crew.task_builder import build_all_hlrs_context

        hlr = _make_node("hlr-1", node_type="HLR", content="HLR content", parent_id="doc-1")
        graph = _make_graph({"hlr-1": hlr})
        result = build_all_hlrs_context(graph)
        assert "ALL HLR REQUIREMENTS" in result
        assert "[hlr-1]" in result
        assert "parent=doc-1" in result

    def test_no_hlrs(self) -> None:
        from backend.crew.task_builder import build_all_hlrs_context

        graph = _make_graph({})
        assert build_all_hlrs_context(graph) == ""

    def test_excludes_empty_content(self) -> None:
        from backend.crew.task_builder import build_all_hlrs_context

        hlr = _make_node("hlr-1", node_type="HLR", content="", parent_id="doc-1")
        graph = _make_graph({"hlr-1": hlr})
        assert build_all_hlrs_context(graph) == ""


class TestBuildArchitectureContext:
    """build_architecture_context returns ARCHITECTURE node content."""

    def test_architecture_found(self) -> None:
        from backend.crew.task_builder import build_architecture_context

        arch = _make_node("arch-1", node_type="ARCHITECTURE", content="Arch content")
        graph = _make_graph({"arch-1": arch})
        result = build_architecture_context(graph)
        assert "[ARCHITECTURE arch-1]" in result
        assert "Arch content" in result

    def test_no_architecture(self) -> None:
        from backend.crew.task_builder import build_architecture_context

        graph = _make_graph({})
        assert build_architecture_context(graph) == ""

    def test_architecture_empty_content(self) -> None:
        from backend.crew.task_builder import build_architecture_context

        arch = _make_node("arch-1", node_type="ARCHITECTURE", content="")
        graph = _make_graph({"arch-1": arch})
        assert build_architecture_context(graph) == ""


class TestBuildTracedHlrsForModule:
    """build_traced_hlrs_for_module returns HLRs referenced by module trace_to."""

    def test_traced_hlrs(self) -> None:
        from backend.crew.task_builder import build_traced_hlrs_for_module

        hlr = _make_node("hlr-1", node_type="HLR", content="Requirement A")
        module = _make_node("mod-1", node_type="MODULE", content="Module", trace_to=["hlr-1"])
        graph = _make_graph({"hlr-1": hlr, "mod-1": module})
        result = build_traced_hlrs_for_module(graph, "mod-1")
        assert "TRACED HLR REQUIREMENTS" in result
        assert "[HLR hlr-1]" in result

    def test_module_not_found(self) -> None:
        from backend.crew.task_builder import build_traced_hlrs_for_module

        graph = _make_graph({})
        assert build_traced_hlrs_for_module(graph, "missing") == ""

    def test_no_trace_to(self) -> None:
        from backend.crew.task_builder import build_traced_hlrs_for_module

        module = _make_node("mod-1", node_type="MODULE", content="Module", trace_to=[])
        graph = _make_graph({"mod-1": module})
        assert build_traced_hlrs_for_module(graph, "mod-1") == ""

    def test_skips_non_hlr_traces(self) -> None:
        from backend.crew.task_builder import build_traced_hlrs_for_module

        llr = _make_node("llr-1", node_type="LLR", content="Low level req")
        module = _make_node("mod-1", node_type="MODULE", content="Module", trace_to=["llr-1"])
        graph = _make_graph({"llr-1": llr, "mod-1": module})
        assert build_traced_hlrs_for_module(graph, "mod-1") == ""


class TestBuildAllModulesContext:
    """build_all_modules_context returns formatted MODULE listing."""

    def test_module_listing(self) -> None:
        from backend.crew.task_builder import build_all_modules_context

        mod = _make_node("mod-1", node_type="MODULE", content="Module content", trace_to=["hlr-1"])
        graph = _make_graph({"mod-1": mod})
        result = build_all_modules_context(graph)
        assert "ALL MODULE NODES" in result
        assert "[mod-1]" in result
        assert "trace_to=" in result

    def test_no_modules(self) -> None:
        from backend.crew.task_builder import build_all_modules_context

        graph = _make_graph({})
        assert build_all_modules_context(graph) == ""


class TestBuildContextForGap:
    """build_context_for_gap dispatches to the right context builder."""

    def test_no_prefetch_gap_returns_empty(self) -> None:
        from backend.crew.task_builder import build_context_for_gap

        gap = _make_gap(GapType.UNCHUNKED_DOCUMENT, "doc-1")
        graph = _make_graph({})
        assert build_context_for_gap(graph, gap) == ""

    def test_inconsistent_case_hlr_uses_trace_to(self) -> None:
        from backend.crew.task_builder import build_context_for_gap

        ref = _make_node("hlr-1", node_type="HLR", content="Requirement")
        case = _make_node("c1", node_type="CASE_HLR", content="Case", parent_id=None)
        case.trace_to = ["hlr-1"]
        graph = _make_graph({"c1": case, "hlr-1": ref})
        gap = _make_gap(GapType.INCONSISTENT_CONTENT, "c1")
        result = build_context_for_gap(graph, gap)
        assert "Requirement" in result

    def test_undesigned_appends_module_context(self) -> None:
        from backend.crew.task_builder import build_context_for_gap

        llr = _make_node("llr-1", node_type="LLR", content="LLR text", parent_id="hlr-1")
        hlr = _make_node("hlr-1", node_type="HLR", content="HLR text")
        module = _make_node(
            "mod-1",
            node_type="MODULE",
            content="Module",
            parent_id=None,
            trace_to=["hlr-1"],
        )
        graph = _make_graph({"llr-1": llr, "hlr-1": hlr, "mod-1": module})
        gap = _make_gap(GapType.UNDESIGNED, "llr-1")
        result = build_context_for_gap(graph, gap)
        assert "OWNING MODULE" in result

    def test_unrefined_hlr_appends_llrs(self) -> None:
        from backend.crew.task_builder import build_context_for_gap

        hlr = _make_node("hlr-1", node_type="HLR", content="HLR text", parent_id=None)
        llr = _make_node("llr-1", node_type="LLR", content="Existing LLR", parent_id="hlr-1")
        graph = _make_graph({"hlr-1": hlr, "llr-1": llr})
        gap = _make_gap(GapType.UNREFINED_HLR, "hlr-1")
        result = build_context_for_gap(graph, gap)
        assert "EXISTING LLR NODES" in result

    def test_untested_hlr_appends_cases(self) -> None:
        from backend.crew.task_builder import build_context_for_gap

        hlr = _make_node("hlr-1", node_type="HLR", content="HLR text", parent_id=None)
        case = _make_node("ch1", node_type="CASE_HLR", content="Case", parent_id=None)
        case.trace_to = ["hlr-1"]
        graph = _make_graph({"hlr-1": hlr, "ch1": case})
        gap = _make_gap(GapType.UNTESTED_HLR, "hlr-1")
        result = build_context_for_gap(graph, gap)
        assert "EXISTING HLR CASE NODES" in result

    def test_untested_llr_appends_cases(self) -> None:
        from backend.crew.task_builder import build_context_for_gap

        llr = _make_node("llr-1", node_type="LLR", content="LLR text", parent_id=None)
        case = _make_node("cl1", node_type="CASE_LLR", content="Case", parent_id=None)
        case.trace_to = []
        graph = _make_graph({"llr-1": llr, "cl1": case})
        gap = _make_gap(GapType.UNTESTED_LLR, "llr-1")
        result = build_context_for_gap(graph, gap)
        assert "EXISTING LLR CASE NODES" in result

    def test_duplicate_node_hlr_appends_siblings(self) -> None:
        from backend.crew.task_builder import build_context_for_gap

        parent = _make_node("p1", node_type="DOCUMENT", content="Doc", parent_id=None)
        target = _make_node("h1", node_type="HLR", content="Target", parent_id="p1")
        sibling = _make_node("h2", node_type="HLR", content="Sibling", parent_id="p1")
        graph = _make_graph({"p1": parent, "h1": target, "h2": sibling})
        gap = _make_gap(GapType.DUPLICATE_NODE, "h1")
        result = build_context_for_gap(graph, gap)
        assert "SIBLING REQUIREMENTS" in result

    def test_unarchitected_appends_all_hlrs(self) -> None:
        from backend.crew.task_builder import build_context_for_gap

        proj = _make_node("proj-1", node_type="PROJECT", content="Project", parent_id=None)
        hlr = _make_node("hlr-1", node_type="HLR", content="Requirement A", parent_id="proj-1")
        hlr2 = _make_node("hlr-2", node_type="HLR", content="Requirement B", parent_id="proj-1")
        graph = _make_graph({"proj-1": proj, "hlr-1": hlr, "hlr-2": hlr2})
        gap = _make_gap(GapType.UNARCHITECTED, "proj-1")
        result = build_context_for_gap(graph, gap)
        assert "ALL HLR REQUIREMENTS" in result
        assert "[hlr-1]" in result
        assert "[hlr-2]" in result

    def test_uncontracted_appends_arch_and_traced_hlrs(self) -> None:
        from backend.crew.task_builder import build_context_for_gap

        proj = _make_node("proj-1", node_type="PROJECT", content="Project", parent_id=None)
        arch = _make_node(
            "arch-1", node_type="ARCHITECTURE", content="Architecture spec", parent_id="proj-1"
        )
        hlr = _make_node("hlr-1", node_type="HLR", content="Requirement traced", parent_id="proj-1")
        module = _make_node(
            "mod-1", node_type="MODULE", content="Module", parent_id="proj-1", trace_to=["hlr-1"]
        )
        graph = _make_graph({"proj-1": proj, "arch-1": arch, "hlr-1": hlr, "mod-1": module})
        gap = _make_gap(GapType.UNCONTRACTED, "mod-1")
        result = build_context_for_gap(graph, gap)
        assert "[ARCHITECTURE arch-1]" in result
        assert "TRACED HLR REQUIREMENTS" in result
        assert "[HLR hlr-1]" in result

    def test_unsuited_appends_arch_modules_hlrs(self) -> None:
        from backend.crew.task_builder import build_context_for_gap

        proj = _make_node("proj-1", node_type="PROJECT", content="Project", parent_id=None)
        arch = _make_node(
            "arch-1", node_type="ARCHITECTURE", content="Architecture spec", parent_id="proj-1"
        )
        hlr = _make_node("hlr-1", node_type="HLR", content="Requirement A", parent_id="proj-1")
        module = _make_node(
            "mod-1",
            node_type="MODULE",
            content="Module plan",
            parent_id="proj-1",
            trace_to=["hlr-1"],
        )
        graph = _make_graph({"proj-1": proj, "arch-1": arch, "hlr-1": hlr, "mod-1": module})
        gap = _make_gap(GapType.UNSUITED, "proj-1")
        result = build_context_for_gap(graph, gap)
        assert "[ARCHITECTURE arch-1]" in result
        assert "ALL MODULE NODES" in result
        assert "[mod-1]" in result
        assert "ALL HLR REQUIREMENTS" in result
        assert "[hlr-1]" in result


class TestBuildTaskDescription:
    """build_task_description returns (description, expected_output) tuple."""

    @patch("backend.crew.task_builder._build_descriptions")
    def test_first_attempt(self, mock_desc: MagicMock) -> None:
        from backend.crew.task_builder import build_task_description

        mock_desc.return_value = {
            GapType.UNCOVERED_PARA: ("Do the thing", "Thing done"),
        }
        gap = _make_gap(GapType.UNCOVERED_PARA, "p1")
        desc, output = build_task_description(gap, "some context", attempt=1)
        assert desc == "Do the thing"
        assert output == "Thing done"
        assert "ATTEMPT" not in desc

    @patch("backend.crew.task_builder._build_descriptions")
    def test_retry_attempt_adds_prefix(self, mock_desc: MagicMock) -> None:
        from backend.crew.task_builder import build_task_description

        mock_desc.return_value = {
            GapType.UNCOVERED_PARA: ("Do the thing", "Thing done"),
        }
        gap = _make_gap(GapType.UNCOVERED_PARA, "p1")
        desc, output = build_task_description(gap, "ctx", attempt=3)
        assert desc.startswith("ATTEMPT 3:")
        assert "Do the thing" in desc

    @patch("backend.crew.task_builder._build_descriptions")
    def test_unknown_gap_type_fallback(self, mock_desc: MagicMock) -> None:
        from backend.crew.task_builder import build_task_description

        mock_desc.return_value = {}  # No entry for the gap type
        gap = _make_gap(GapType.ORPHAN_NODE, "x1", description="Orphan detected")
        desc, output = build_task_description(gap, "ctx", attempt=1)
        assert "ORPHAN_NODE" in desc
        assert "Orphan detected" in desc


# ═══════════════════════════════════════════════════════════════════════════════
# 2. workspace_sync
# ═══════════════════════════════════════════════════════════════════════════════


class TestReadFile:
    """_read_file returns content or None."""

    def test_file_exists(self, tmp_path: Path) -> None:
        from backend.crew.workspace_sync import _read_file

        f = tmp_path / "hello.py"
        f.write_text("print('hello')", encoding="utf-8")
        assert _read_file(f) == "print('hello')"

    def test_file_missing(self, tmp_path: Path) -> None:
        from backend.crew.workspace_sync import _read_file

        assert _read_file(tmp_path / "nope.py") is None

    def test_file_unreadable(self, tmp_path: Path) -> None:
        from backend.crew.workspace_sync import _read_file

        # A directory path raises OSError when read_text is called
        assert _read_file(tmp_path) is None


class TestFindChildOfType:
    """_find_child_of_type returns the matching child or None."""

    def test_child_found(self) -> None:
        from backend.crew.workspace_sync import _find_child_of_type

        parent = _make_node("p1", node_type="DESIGN", content="Design")
        child = _make_node("c1", node_type="CODE", content="Code", parent_id="p1")
        graph = _make_graph({"p1": parent, "c1": child})
        found = _find_child_of_type(graph, "p1", "CODE")
        assert found is not None
        assert found.node_id == "c1"

    def test_child_not_found(self) -> None:
        from backend.crew.workspace_sync import _find_child_of_type

        parent = _make_node("p1", node_type="DESIGN", content="Design")
        graph = _make_graph({"p1": parent})
        assert _find_child_of_type(graph, "p1", "CODE") is None


class TestSyncCodeNodes:
    """_sync_code_nodes creates CODE nodes from DESIGN nodes with file_path."""

    @pytest.mark.asyncio
    async def test_design_with_file(self, tmp_path: Path) -> None:
        from backend.crew.workspace_sync import _sync_code_nodes

        f = tmp_path / "src" / "module.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("class Foo: pass", encoding="utf-8")

        design = _make_node(
            "des-1",
            node_type=NodeType.DESIGN.value,
            content="Design",
            properties={"file_path": "src/module.py"},
            title="Foo Design",
        )
        design.trace_to = ["llr-1"]
        graph = _make_graph({"des-1": design})
        gaps: list[Gap] = []
        count, refreshed = await _sync_code_nodes(graph, tmp_path, gaps)
        assert count == 1
        assert refreshed == 0
        assert gaps == []
        graph.add_node.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_design_without_file_path(self, tmp_path: Path) -> None:
        from backend.crew.workspace_sync import _sync_code_nodes

        design = _make_node(
            "des-1",
            node_type=NodeType.DESIGN.value,
            content="Design",
            properties={},
        )
        graph = _make_graph({"des-1": design})
        gaps: list[Gap] = []
        count, refreshed = await _sync_code_nodes(graph, tmp_path, gaps)
        assert count == 0
        assert gaps == []

    @pytest.mark.asyncio
    async def test_design_file_missing_emits_gap(self, tmp_path: Path) -> None:
        """Missing workspace file emits a MISSING_CODE gap rather than silently skipping."""
        from backend.analysis.gaps import GapType
        from backend.crew.workspace_sync import _sync_code_nodes

        design = _make_node(
            "des-1",
            node_type=NodeType.DESIGN.value,
            content="Design",
            properties={"file_path": "src/gone.py"},
        )
        graph = _make_graph({"des-1": design})
        gaps: list[Gap] = []
        count, refreshed = await _sync_code_nodes(graph, tmp_path, gaps)
        assert count == 0
        assert len(gaps) == 1
        assert gaps[0].type == GapType.MISSING_CODE

    @pytest.mark.asyncio
    async def test_design_already_has_code_child(self, tmp_path: Path) -> None:
        """When a CODE child exists and file content is unchanged, sync is a no-op."""
        from backend.crew.workspace_sync import _sync_code_nodes

        f = tmp_path / "x.py"
        f.write_text("pass", encoding="utf-8")

        design = _make_node(
            "des-1",
            node_type=NodeType.DESIGN.value,
            content="Design",
            properties={"file_path": "x.py"},
        )
        code = _make_node(
            "code-1", node_type=NodeType.CODE.value, content="Code", parent_id="des-1",
            properties={"file_path": "x.py", "file_content": "pass"},
        )
        graph = _make_graph({"des-1": design, "code-1": code})
        gaps: list[Gap] = []
        count, refreshed = await _sync_code_nodes(graph, tmp_path, gaps)
        assert count == 0
        assert refreshed == 0


class TestSyncTestNodes:
    """_sync_test_nodes creates TEST nodes from CASE nodes with file_path."""

    @pytest.mark.asyncio
    async def test_case_with_file(self, tmp_path: Path) -> None:
        from backend.crew.workspace_sync import _sync_test_nodes

        f = tmp_path / "tests" / "test_foo.py"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("def test_foo(): pass", encoding="utf-8")

        case = _make_node(
            "case-1",
            node_type=NodeType.CASE_HLR.value,
            content="Case",
            properties={"file_path": "tests/test_foo.py"},
            title="Foo Case",
        )
        graph = _make_graph({"case-1": case})

        with patch("backend.crew.workspace_sync.analyse_traces") as mock_at:
            mock_at.return_value = MagicMock(traces=[MagicMock(symbol="test_foo")])
            gaps: list[Gap] = []
            count, refreshed = await _sync_test_nodes(graph, tmp_path, gaps)

        assert count == 1
        assert refreshed == 0
        graph.add_node.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_case_without_file_path(self, tmp_path: Path) -> None:
        from backend.crew.workspace_sync import _sync_test_nodes

        case = _make_node(
            "case-1",
            node_type=NodeType.CASE_HLR.value,
            content="Case",
            properties={},
        )
        graph = _make_graph({"case-1": case})
        gaps: list[Gap] = []
        count, refreshed = await _sync_test_nodes(graph, tmp_path, gaps)
        assert count == 0

    @pytest.mark.asyncio
    async def test_case_already_has_test_child(self, tmp_path: Path) -> None:
        from backend.crew.workspace_sync import _sync_test_nodes

        f = tmp_path / "test.py"
        f.write_text("pass", encoding="utf-8")

        case = _make_node(
            "case-1",
            node_type=NodeType.CASE_LLR.value,
            content="Case",
            properties={"file_path": "test.py"},
        )
        test = _make_node(
            "test-1", node_type=NodeType.TEST.value, content="Test", parent_id="case-1",
            properties={"file_path": "test.py", "file_content": "pass"},
        )
        graph = _make_graph({"case-1": case, "test-1": test})
        gaps: list[Gap] = []
        with patch("backend.crew.workspace_sync.analyse_traces") as mock_at:
            mock_at.return_value = MagicMock(traces=[])
            count, refreshed = await _sync_test_nodes(graph, tmp_path, gaps)
        assert count == 0
        assert refreshed == 0


class TestWorkspaceSync:
    """workspace_sync orchestrates CODE and TEST sync."""

    @pytest.mark.asyncio
    async def test_delegates_to_sync_helpers(self, tmp_path: Path) -> None:
        from backend.crew.workspace_sync import workspace_sync

        flow = MagicMock()
        flow.graph = _make_graph({})
        flow._workspace = tmp_path

        with (
            patch(
                "backend.crew.workspace_sync._sync_code_nodes",
                new_callable=AsyncMock,
                return_value=(2, 0),
            ) as mc,
            patch(
                "backend.crew.workspace_sync._sync_test_nodes",
                new_callable=AsyncMock,
                return_value=(3, 0),
            ) as mt,
        ):
            result = await workspace_sync(flow, 13)

        mc.assert_awaited_once()
        mt.assert_awaited_once()
        assert result["step_name"] == "workspace_sync"
        assert result["deletions"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 3. phase_steps
# ═══════════════════════════════════════════════════════════════════════════════


def _make_flow() -> MagicMock:
    """Create a mock flow object for phase_steps tests."""
    flow = MagicMock()
    flow._run_structural_loop = AsyncMock()
    flow._graph_state_count = MagicMock(return_value=0)
    flow._dispatch = AsyncMock()
    flow.run_qual_check = AsyncMock()
    flow.run_semantic_check = AsyncMock(return_value=0)
    flow.run_design_consolidation = AsyncMock(return_value=0)
    flow.graph = _make_graph({})
    flow.config = MagicMock()
    return flow


class TestStructuralStep:
    """structural step delegates to flow._run_structural_loop."""

    @pytest.mark.asyncio
    async def test_returns_step_result(self) -> None:
        from backend.crew.phase_steps import structural

        flow = _make_flow()
        result = await structural(flow, 5)
        flow._run_structural_loop.assert_awaited_once_with(5, skip_approval=True)
        assert result["step_name"] == "structural"
        assert result["deletions"] == 0


class TestCombinedQualityStep:
    """combined_quality dispatches gaps and handles DispatchQuotaError."""

    @pytest.mark.asyncio
    async def test_no_gaps(self) -> None:
        from backend.crew.phase_steps import combined_quality

        flow = _make_flow()
        flow.run_combined_quality_check = AsyncMock(return_value=[])
        result = await combined_quality(flow, 5)
        assert result["step_name"] == "combined_quality"
        flow._dispatch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatches_gaps(self) -> None:
        from backend.crew.phase_steps import combined_quality

        flow = _make_flow()
        gap1 = _make_gap(GapType.NON_ATOMIC_REQUIREMENT, "h1")
        gap2 = _make_gap(GapType.STALE_TITLE, "h2")
        flow.run_combined_quality_check = AsyncMock(return_value=[gap2, gap1])
        node = _make_node("h1")
        flow.graph.node_sync = MagicMock(return_value=node)
        await combined_quality(flow, 5)
        assert flow._dispatch.await_count == 2

    @pytest.mark.asyncio
    async def test_quota_error_propagates(self) -> None:
        """DispatchQuotaError propagates so quota exhaustion halts the run —
        it is never converted into a completed step."""
        from backend.crew.dispatch import DispatchQuotaError
        from backend.crew.phase_steps import combined_quality

        flow = _make_flow()
        gap = _make_gap(GapType.NON_ATOMIC_REQUIREMENT, "h1")
        flow.run_combined_quality_check = AsyncMock(return_value=[gap])
        node = _make_node("h1")
        flow.graph.node_sync = MagicMock(return_value=node)
        flow._dispatch = AsyncMock(side_effect=DispatchQuotaError("quota exceeded"))
        with pytest.raises(DispatchQuotaError):
            await combined_quality(flow, 5)

    @pytest.mark.asyncio
    async def test_skips_missing_node(self) -> None:
        from backend.crew.phase_steps import combined_quality

        flow = _make_flow()
        gap = _make_gap(GapType.NON_ATOMIC_REQUIREMENT, "missing")
        flow.run_combined_quality_check = AsyncMock(return_value=[gap])
        flow.graph.node_sync = MagicMock(return_value=None)
        await combined_quality(flow, 5)
        flow._dispatch.assert_not_awaited()


class TestQualityGapsStep:
    """quality_gaps delegates to flow.run_qual_check."""

    @pytest.mark.asyncio
    async def test_delegates(self) -> None:
        from backend.crew.phase_steps import quality_gaps

        flow = _make_flow()
        result = await quality_gaps(flow, 5)
        flow.run_qual_check.assert_awaited_once_with(5, _broadcast_status=False)
        assert result["step_name"] == "quality_gaps"


class TestSemanticStep:
    """semantic step uses _batch_new_node_ids and clears after."""

    @pytest.mark.asyncio
    async def test_without_batch_ids(self) -> None:
        from backend.crew.phase_steps import semantic

        flow = _make_flow()
        # No _batch_new_node_ids attribute
        if hasattr(flow, "_batch_new_node_ids"):
            delattr(flow, "_batch_new_node_ids")
        result = await semantic(flow, 5)
        flow.run_semantic_check.assert_awaited_once_with(
            5,
            _broadcast_status=False,
            only_node_ids=None,
        )
        assert result["step_name"] == "semantic"

    @pytest.mark.asyncio
    async def test_with_batch_ids(self) -> None:
        from backend.crew.phase_steps import semantic

        flow = _make_flow()
        flow._batch_new_node_ids = ["n1", "n2"]
        await semantic(flow, 5)
        flow.run_semantic_check.assert_awaited_once_with(
            5,
            _broadcast_status=False,
            only_node_ids=["n1", "n2"],
        )
        # Cleared after use
        assert flow._batch_new_node_ids is None

    @pytest.mark.asyncio
    async def test_returns_deletion_count(self) -> None:
        from backend.crew.phase_steps import semantic

        flow = _make_flow()
        flow.run_semantic_check = AsyncMock(return_value=5)
        result = await semantic(flow, 5)
        assert result["deletions"] == 5


class TestDesignConsolidationStep:
    """design_consolidation delegates to flow.run_design_consolidation."""

    @pytest.mark.asyncio
    async def test_delegates(self) -> None:
        from backend.crew.phase_steps import design_consolidation

        flow = _make_flow()
        flow.run_design_consolidation = AsyncMock(return_value=3)
        result = await design_consolidation(flow, 8)
        flow.run_design_consolidation.assert_awaited_once_with(_broadcast_status=False)
        assert result["step_name"] == "design_consolidation"
        assert result["deletions"] == 3


class TestCaseTraceCoverageStep:
    """case_trace_coverage creates checker and runs it."""

    @pytest.mark.asyncio
    async def test_runs_checker(self) -> None:
        from backend.crew.phase_steps import case_trace_coverage

        flow = _make_flow()
        case = _make_node("CASE_HLR-001", node_type="CASE_HLR", trace_to=["HLR-001"])
        flow.graph = _make_graph({"CASE_HLR-001": case})
        flow._last_checked_case_ids = None
        mock_checker = AsyncMock(return_value=7)
        with (
            patch("backend.agents.factory.build_llm", return_value=MagicMock()),
            patch(
                "backend.crew.case_trace_check.create_case_trace_checker", return_value=mock_checker
            ) as mock_create,
        ):
            result = await case_trace_coverage(flow, 10)
        mock_create.assert_called_once()
        mock_checker.assert_awaited_once()
        assert result["step_name"] == "case_trace_coverage"
        assert result["deletions"] == 7

    @pytest.mark.asyncio
    async def test_no_case_nodes_skips_checker(self) -> None:
        """With no CASE nodes there is nothing to verify — no LLM checker is built."""
        from backend.crew.phase_steps import case_trace_coverage

        flow = _make_flow()  # empty graph
        flow._last_checked_case_ids = None
        with patch(
            "backend.crew.case_trace_check.create_case_trace_checker"
        ) as mock_create:
            result = await case_trace_coverage(flow, 10)
        mock_create.assert_not_called()
        assert result["step_name"] == "case_trace_coverage"
        assert result["deletions"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# 4. work_queue
# ═══════════════════════════════════════════════════════════════════════════════


class TestWorkItem:
    """WorkItem sort_key and to_dict."""

    def test_sort_key_ordering(self) -> None:
        from backend.work_queue import WorkItem

        low_effort = WorkItem(id="wq-001", phase=1, category="c", description="d", effort="low")
        high_effort = WorkItem(id="wq-002", phase=1, category="c", description="d", effort="high")
        assert low_effort.sort_key < high_effort.sort_key

    def test_sort_key_urgency(self) -> None:
        from backend.work_queue import WorkItem

        critical = WorkItem(id="wq-001", phase=1, category="c", description="d", urgency="critical")
        low = WorkItem(id="wq-002", phase=1, category="c", description="d", urgency="low")
        assert critical.sort_key < low.sort_key

    def test_sort_key_bad_id(self) -> None:
        from backend.work_queue import WorkItem

        item = WorkItem(id="bad", phase=1, category="c", description="d")
        assert item.sort_key[3] == 9999

    def test_to_dict(self) -> None:
        from backend.work_queue import WorkItem

        item = WorkItem(id="wq-001", phase=1, category="cat", description="desc", target="tgt")
        d = item.to_dict()
        assert d["id"] == "wq-001"
        assert d["phase"] == 1
        assert d["category"] == "cat"
        assert d["target"] == "tgt"
        assert d["status"] == "pending"

    def test_invalid_urgency_defaults(self) -> None:
        from backend.work_queue import WorkItem

        item = WorkItem(id="wq-001", phase=1, category="c", description="d", urgency="bogus")
        assert item.urgency == "medium"

    def test_invalid_importance_defaults(self) -> None:
        from backend.work_queue import WorkItem

        item = WorkItem(id="wq-001", phase=1, category="c", description="d", importance="bogus")
        assert item.importance == "medium"

    def test_invalid_effort_defaults(self) -> None:
        from backend.work_queue import WorkItem

        item = WorkItem(id="wq-001", phase=1, category="c", description="d", effort="bogus")
        assert item.effort == "medium"


class TestActionRecord:
    """ActionRecord to_dict."""

    def test_to_dict(self) -> None:
        from backend.work_queue import ActionRecord

        rec = ActionRecord(
            round=1,
            work_item_id="wq-001",
            phase=5,
            category="lint",
            files_modified=["a.py"],
            tool_calls=3,
            gap_count_before=10,
            gap_count_after=7,
            outcome="improved",
            summary="Fixed lint",
        )
        d = rec.to_dict()
        assert d["round"] == 1
        assert d["work_item_id"] == "wq-001"
        assert d["outcome"] == "improved"
        assert d["files_modified"] == ["a.py"]

    def test_defaults(self) -> None:
        from backend.work_queue import ActionRecord

        rec = ActionRecord(round=1, work_item_id="wq-001", phase=1, category="c")
        d = rec.to_dict()
        assert d["files_modified"] == []
        assert d["tool_calls"] == 0
        assert d["outcome"] == "no_change"


class TestWorkQueueService:
    """WorkQueueService queue operations."""

    def _make_service(self) -> WorkQueueService:
        from backend.work_queue import WorkQueueService

        svc = WorkQueueService()
        # Mock WS manager to avoid real broadcasts
        svc._ws_manager = MagicMock()
        svc._ws_manager.broadcast_threadsafe = MagicMock()
        return svc

    def test_add_returns_id_and_sorts(self) -> None:
        svc = self._make_service()
        id1 = svc.add(phase=1, category="c1", description="First", effort="high")
        id2 = svc.add(phase=1, category="c2", description="Second", effort="low")
        assert id1 == "wq-001"
        assert id2 == "wq-002"
        # Low effort should sort before high effort
        assert svc._items[0].id == "wq-002"

    def test_add_broadcasts(self) -> None:
        svc = self._make_service()
        svc.add(phase=1, category="c", description="d")
        assert svc._ws_manager is not None
        svc._ws_manager.broadcast_threadsafe.assert_called()

    def test_remove_found(self) -> None:
        svc = self._make_service()
        item_id = svc.add(phase=1, category="c", description="d")
        assert svc.remove(item_id) is True
        assert len(svc._items) == 0

    def test_remove_not_found(self) -> None:
        svc = self._make_service()
        assert svc.remove("wq-999") is False

    def test_promote_urgency(self) -> None:
        svc = self._make_service()
        item_id = svc.add(phase=1, category="c", description="d", urgency="low")
        result = svc.promote(item_id, urgency="critical")
        assert result is True
        assert svc._items[0].urgency == "critical"

    def test_promote_importance(self) -> None:
        svc = self._make_service()
        item_id = svc.add(phase=1, category="c", description="d", importance="low")
        result = svc.promote(item_id, importance="high")
        assert result is True
        assert svc._items[0].importance == "high"

    def test_promote_not_found(self) -> None:
        svc = self._make_service()
        assert svc.promote("wq-999", urgency="critical") is False

    def test_promote_no_valid_change(self) -> None:
        svc = self._make_service()
        item_id = svc.add(phase=1, category="c", description="d")
        # Invalid urgency value, no change
        assert svc.promote(item_id, urgency="bogus") is False

    def test_update_status(self) -> None:
        svc = self._make_service()
        item_id = svc.add(phase=1, category="c", description="d")
        svc.update_status(item_id, "in_progress")
        assert svc._items[0].status == "in_progress"
        assert svc._ws_manager is not None
        svc._ws_manager.broadcast_threadsafe.assert_called()

    def test_update_status_not_found(self) -> None:
        svc = self._make_service()
        # Should not raise
        svc.update_status("wq-999", "done")

    def test_record_action(self) -> None:
        from backend.work_queue import ActionRecord

        svc = self._make_service()
        rec = ActionRecord(round=1, work_item_id="wq-001", phase=1, category="c")
        svc.record_action(rec)
        assert len(svc._history) == 1
        assert svc._ws_manager is not None
        svc._ws_manager.broadcast_threadsafe.assert_called()

    def test_clear_phase(self) -> None:
        svc = self._make_service()
        svc.add(phase=1, category="c1", description="Phase 1 item")
        svc.add(phase=2, category="c2", description="Phase 2 item")
        svc.clear_phase(1)
        assert len(svc._items) == 1
        assert svc._items[0].phase == 2

    def test_clear_phase_clears_items_but_preserves_history(self) -> None:
        """History outlives the queue rebuild.

        ``collect_gaps`` calls ``clear_phase`` at the start of every batch. While
        this also dropped history, ``category_failure_count`` — whose job is to
        count trailing failures *across* attempts — could never see past the
        current batch, and the Control Station History panel reset each cycle.
        """
        from backend.work_queue import ActionRecord

        svc = self._make_service()
        svc.add(phase=1, category="c", description="d")
        svc.record_action(ActionRecord(round=1, work_item_id="wq-001", phase=1, category="c"))
        svc.record_action(ActionRecord(round=1, work_item_id="wq-002", phase=2, category="c"))

        svc.clear_phase(1)

        assert [i.phase for i in svc._items] == [], "phase 1 items were not cleared"
        assert len(svc._history) == 2, "history must survive a queue rebuild"
        assert {h.phase for h in svc._history} == {1, 2}

    def test_next_pending(self) -> None:
        svc = self._make_service()
        svc.add(phase=1, category="c1", description="First")
        svc.add(phase=1, category="c2", description="Second")
        svc.add(phase=2, category="c3", description="Other phase")
        item = svc.next_pending(1)
        assert item is not None
        assert item.phase == 1

    def test_next_pending_skips_non_pending(self) -> None:
        svc = self._make_service()
        item_id = svc.add(phase=1, category="c1", description="d")
        svc.update_status(item_id, "done")
        assert svc.next_pending(1) is None

    def test_next_pending_no_items(self) -> None:
        svc = self._make_service()
        assert svc.next_pending(1) is None

    def test_category_failure_count_trailing(self) -> None:
        from backend.work_queue import ActionRecord

        svc = self._make_service()
        svc.record_action(
            ActionRecord(round=1, work_item_id="w1", phase=1, category="lint", outcome="improved")
        )
        svc.record_action(
            ActionRecord(round=2, work_item_id="w2", phase=1, category="lint", outcome="no_change")
        )
        svc.record_action(
            ActionRecord(round=3, work_item_id="w3", phase=1, category="lint", outcome="worse")
        )
        assert svc.category_failure_count("lint") == 2

    def test_category_failure_count_reset_on_success(self) -> None:
        from backend.work_queue import ActionRecord

        svc = self._make_service()
        svc.record_action(
            ActionRecord(round=1, work_item_id="w1", phase=1, category="lint", outcome="no_change")
        )
        svc.record_action(
            ActionRecord(round=2, work_item_id="w2", phase=1, category="lint", outcome="improved")
        )
        svc.record_action(
            ActionRecord(round=3, work_item_id="w3", phase=1, category="lint", outcome="no_change")
        )
        assert svc.category_failure_count("lint") == 1

    def test_category_failure_count_zero(self) -> None:
        svc = self._make_service()
        assert svc.category_failure_count("lint") == 0

    def test_category_failure_count_ignores_other_categories(self) -> None:
        from backend.work_queue import ActionRecord

        svc = self._make_service()
        svc.record_action(
            ActionRecord(round=1, work_item_id="w1", phase=1, category="lint", outcome="no_change")
        )
        svc.record_action(
            ActionRecord(round=2, work_item_id="w2", phase=1, category="test", outcome="no_change")
        )
        svc.record_action(
            ActionRecord(round=3, work_item_id="w3", phase=1, category="lint", outcome="no_change")
        )
        assert svc.category_failure_count("lint") == 2



    def test_broadcast_no_ws_manager(self) -> None:
        from backend.work_queue import WorkQueueService

        svc = WorkQueueService()
        # Should not raise when _ws_manager is None
        svc.broadcast()

    def test_broadcast_creates_ws_event(self) -> None:
        svc = self._make_service()
        svc.add(phase=1, category="c", description="d")
        assert svc._ws_manager is not None
        call_args = svc._ws_manager.broadcast_threadsafe.call_args
        event = call_args[0][0]
        assert event.event_type.value == "WORK_QUEUE"
        assert "items" in event.payload
        assert "history" in event.payload

    def test_all_items_property(self) -> None:
        svc = self._make_service()
        svc.add(phase=1, category="c", description="d")
        items = svc.all_items
        assert len(items) == 1
        assert items[0]["category"] == "c"

    def test_all_history_property(self) -> None:
        from backend.work_queue import ActionRecord

        svc = self._make_service()
        svc.record_action(ActionRecord(round=1, work_item_id="w1", phase=1, category="c"))
        history = svc.all_history
        assert len(history) == 1
        assert history[0]["category"] == "c"

    def test_items_for_phase(self) -> None:
        svc = self._make_service()
        svc.add(phase=1, category="c1", description="d")
        svc.add(phase=2, category="c2", description="d")
        items = svc.items_for_phase(1)
        assert len(items) == 1
        assert items[0].phase == 1

    def test_history_for_category(self) -> None:
        from backend.work_queue import ActionRecord

        svc = self._make_service()
        svc.record_action(ActionRecord(round=1, work_item_id="w1", phase=1, category="lint"))
        svc.record_action(ActionRecord(round=2, work_item_id="w2", phase=1, category="test"))
        history = svc.history_for_category("lint")
        assert len(history) == 1
        assert history[0].category == "lint"

    def test_initialise_ws_manager(self) -> None:
        from backend.work_queue import WorkQueueService

        svc = WorkQueueService()
        ws = MagicMock()
        svc.initialise(ws)
        assert svc._ws_manager is ws
