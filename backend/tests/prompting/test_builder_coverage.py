"""Coverage tests for backend.prompting.builder context builders.

Exercises the context-assembly branches of ``build_context_for_gap`` and
the smaller lookup helpers with a lightweight duck-typed graph.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from backend.analysis.gaps import Gap, GapPriority, GapType
from backend.prompting.builder import (
    _build_shallow_req_context,
    _find_architecture_node,
    build_cases_for_requirement,
    build_context_for_gap,
    build_design_for_llr,
    build_sibling_paras_context,
)


def _node(nid: str, ntype: str, **kw: Any) -> SimpleNamespace:
    return SimpleNamespace(
        node_id=nid,
        node_type=ntype,
        parent_id=kw.get("parent_id", None),
        title=kw.get("title", ""),
        content=kw.get("content", ""),
        trace_to=kw.get("trace_to", []),
        properties=kw.get("properties", {}),
    )


class _Graph:
    def __init__(self, nodes: list[SimpleNamespace], tracing: dict[str, list[str]] | None = None) -> None:
        self._nodes = nodes
        self._tracing = tracing or {}

    def all_nodes(self) -> list[SimpleNamespace]:
        return self._nodes

    def node_sync(self, nid: str) -> SimpleNamespace | None:
        return next((n for n in self._nodes if n.node_id == nid), None)

    def children_sync(self, pid: str) -> list[SimpleNamespace]:
        return [n for n in self._nodes if n.parent_id == pid]

    def nodes_tracing_to(self, target: str, source_type: str) -> list[str]:
        return self._tracing.get(target, [])


def _gap(node_id: str, gap_type: GapType) -> Gap:
    return Gap(
        type=gap_type, priority=GapPriority.MAINTENANCE,
        node_id=node_id, description="d",
    )


# ── _build_shallow_req_context ───────────────────────────────────────────────


def test_shallow_context_missing_node_is_empty() -> None:
    assert _build_shallow_req_context(_Graph([]), "LLR-404") == ""


def test_shallow_context_includes_parent_content() -> None:
    """A content-less LLR still yields its parent HLR's content."""
    hlr = _node("HLR-1", "HLR", content="Parent requirement.")
    llr = _node("LLR-1", "LLR", parent_id="HLR-1", content="")
    ctx = _build_shallow_req_context(_Graph([hlr, llr]), "LLR-1")
    assert "Parent requirement." in ctx
    assert "[HLR HLR-1]" in ctx


# ── small helpers ────────────────────────────────────────────────────────────


def test_find_architecture_node_absent_returns_none() -> None:
    assert _find_architecture_node(_Graph([])) is None


def test_design_for_llr_no_owning_module_is_empty() -> None:
    hlr = _node("HLR-1", "HLR", content="req")
    llr = _node("LLR-1", "LLR", parent_id="HLR-1", content="req")
    assert build_design_for_llr(_Graph([hlr, llr]), "LLR-1") == ""


def test_design_for_llr_module_without_designs_is_empty() -> None:
    hlr = _node("HLR-1", "HLR", content="req")
    llr = _node("LLR-1", "LLR", parent_id="HLR-1", content="req")
    mod = _node("MOD-1", "MODULE", content="core")
    graph = _Graph([hlr, llr, mod], tracing={"HLR-1": ["MOD-1"]})
    assert build_design_for_llr(graph, "LLR-1") == ""


def test_cases_for_requirement_none_is_empty() -> None:
    assert build_cases_for_requirement(_Graph([]), "LLR-1") == ""


def test_sibling_paras_missing_target_is_empty() -> None:
    assert build_sibling_paras_context(_Graph([]), "PARA-404") == ""


def test_sibling_paras_no_siblings_is_empty() -> None:
    doc = _node("DOC-1", "DOCUMENT", content="doc")
    para = _node("PARA-1", "PARA", parent_id="DOC-1", content="text")
    assert build_sibling_paras_context(_Graph([doc, para]), "PARA-1") == ""


# ── build_context_for_gap: per-gap-type wiring ───────────────────────────────


def test_stale_trace_to_gap_needs_no_prefetch() -> None:
    assert build_context_for_gap(_Graph([]), _gap("N-1", GapType.STALE_TRACE_TO)) == ""


def test_unchunked_document_inlines_document_content() -> None:
    doc = _node("DOC-1", "DOCUMENT", title="Whitepaper", content="Full doc text.")
    ctx = build_context_for_gap(_Graph([doc]), _gap("DOC-1", GapType.UNCHUNKED_DOCUMENT))
    assert "Full doc text." in ctx


def test_unarchitected_without_hlrs_yields_no_landscape() -> None:
    doc = _node("DOC-1", "DOCUMENT", title="Doc", content="doc")
    ctx = build_context_for_gap(_Graph([doc]), _gap("DOC-1", GapType.UNARCHITECTED))
    assert "ALL HLR REQUIREMENTS" not in ctx
    assert "WHITEPAPER DIGEST" not in ctx


def test_unarchitected_includes_hlrs_and_digest() -> None:
    doc = _node("DOC-1", "DOCUMENT", title="Doc", content="doc")
    hlr = _node("HLR-1", "HLR", parent_id="DOC-1", content="Shall be fast.")
    nfr = _node(
        "PARA-1", "PARA", parent_id="DOC-1", content="Latency under 5ms.",
        properties={"para_type": "non_functional"},
    )
    graph = _Graph([doc, hlr, nfr])
    ctx = build_context_for_gap(graph, _gap("DOC-1", GapType.UNARCHITECTED))
    assert "ALL HLR REQUIREMENTS" in ctx
    assert "WHITEPAPER DIGEST" in ctx


def test_uncontracted_sparse_graph_builds_minimal_context() -> None:
    """A MODULE gap with no architecture, HLRs, or peers packs cleanly."""
    ctx = build_context_for_gap(_Graph([]), _gap("MOD-404", GapType.UNCONTRACTED))
    assert ctx == ""


def test_uncontracted_rich_graph_includes_all_peer_sections() -> None:
    arch = _node(
        "ARCH-1", "ARCHITECTURE",
        content=(
            "## Overview\nModular.\n\n"
            "## Technology Stack\nPython 3.12.\n\n"
            "## Cross-Cutting Concerns\nLogging.\n"
        ),
    )
    hlr = _node("HLR-1", "HLR", content="Shall compute routes.")
    llr = _node("LLR-1", "LLR", parent_id="HLR-1", content="Shall use A*.")
    mod = _node("MOD-1", "MODULE", content="Router module.", trace_to=["HLR-1"])
    other_mod = _node("MOD-2", "MODULE", content="Other module.")
    peer_contract = _node(
        "CON-2", "CONTRACT", parent_id="MOD-2", content="def other() -> None",
    )
    graph = _Graph(
        [arch, hlr, llr, mod, other_mod, peer_contract],
        tracing={"HLR-1": ["MOD-1"]},
    )
    ctx = build_context_for_gap(graph, _gap("MOD-1", GapType.UNCONTRACTED))
    assert "Tech Stack + Cross-Cutting" in ctx
    assert "TRACED HLR REQUIREMENTS" in ctx
    assert "SIBLING CONTRACTS" in ctx
    assert "LLRs UNDER TRACED HLRs" in ctx


def test_unsuited_sparse_graph_builds_empty_context() -> None:
    ctx = build_context_for_gap(_Graph([]), _gap("PROJ-1", GapType.UNSUITED))
    assert ctx == ""


def test_undesigned_without_module_context_is_empty() -> None:
    ctx = build_context_for_gap(_Graph([]), _gap("LLR-404", GapType.UNDESIGNED))
    assert ctx == ""


def test_uncovered_para_includes_sibling_paragraphs() -> None:
    doc = _node("DOC-1", "DOCUMENT", title="Doc", content="doc")
    para = _node("PARA-1", "PARA", parent_id="DOC-1", content="Behaviour text.")
    sib = _node(
        "PARA-2", "PARA", parent_id="DOC-1", content="Constraint text.",
        properties={"para_type": "constraint"},
    )
    graph = _Graph([doc, para, sib])
    ctx = build_context_for_gap(graph, _gap("PARA-1", GapType.UNCOVERED_PARA))
    assert "SIBLING PARAGRAPHS" in ctx
    assert "Constraint text." in ctx


def test_unrefined_hlr_without_llrs_has_no_peer_section() -> None:
    hlr = _node("HLR-1", "HLR", content="Shall be safe.")
    ctx = build_context_for_gap(_Graph([hlr]), _gap("HLR-1", GapType.UNREFINED_HLR))
    assert "EXISTING LLR NODES" not in ctx
    assert "Shall be safe." in ctx


def test_untested_llr_missing_node_builds_empty_sections() -> None:
    ctx = build_context_for_gap(_Graph([]), _gap("LLR-404", GapType.UNTESTED_LLR))
    assert ctx == ""


def test_untested_llr_includes_design_and_suite_strategy() -> None:
    hlr = _node("HLR-1", "HLR", content="Shall route.")
    llr = _node("LLR-1", "LLR", parent_id="HLR-1", content="Shall use A*.")
    mod = _node("MOD-1", "MODULE", content="Router.")
    design = _node(
        "DESIGN-1", "DESIGN", parent_id="MOD-1",
        content="class Router", trace_to=["LLR-1"],
    )
    suite = _node("SUITE-1", "SUITE", content="Unit tests first.")
    graph = _Graph([hlr, llr, mod, design, suite], tracing={"HLR-1": ["MOD-1"]})
    ctx = build_context_for_gap(graph, _gap("LLR-1", GapType.UNTESTED_LLR))
    assert "OWNING DESIGN(s) FOR LLR LLR-1" in ctx
    assert "SUITE [SUITE-1]" in ctx


def test_duplicate_node_llr_without_siblings_has_no_dedup_section() -> None:
    hlr = _node("HLR-1", "HLR", content="parent")
    llr = _node("LLR-1", "LLR", parent_id="HLR-1", content="Shall do X.")
    ctx = build_context_for_gap(_Graph([hlr, llr]), _gap("LLR-1", GapType.DUPLICATE_NODE))
    assert "SIBLING REQUIREMENTS" not in ctx


def test_shallow_context_skips_parent_without_content() -> None:
    hlr = _node("HLR-1", "HLR", content="")
    llr = _node("LLR-1", "LLR", parent_id="HLR-1", content="Req text.")
    ctx = _build_shallow_req_context(_Graph([hlr, llr]), "LLR-1")
    assert ctx == "[LLR LLR-1]\nReq text."


def test_uncontracted_skips_non_llr_children_of_traced_hlrs() -> None:
    hlr = _node("HLR-1", "HLR", content="Shall route.")
    note = _node("PARA-9", "PARA", parent_id="HLR-1", content="note")
    mod = _node("MOD-1", "MODULE", content="Router.", trace_to=["HLR-1"])
    graph = _Graph([hlr, note, mod], tracing={"HLR-1": ["MOD-1"]})
    ctx = build_context_for_gap(graph, _gap("MOD-1", GapType.UNCONTRACTED))
    assert "LLRs UNDER TRACED HLRs" not in ctx


def test_uncovered_para_without_siblings_has_no_sibling_section() -> None:
    doc = _node("DOC-1", "DOCUMENT", title="Doc", content="doc")
    para = _node("PARA-1", "PARA", parent_id="DOC-1", content="Behaviour text.")
    ctx = build_context_for_gap(_Graph([doc, para]), _gap("PARA-1", GapType.UNCOVERED_PARA))
    assert "SIBLING PARAGRAPHS" not in ctx
