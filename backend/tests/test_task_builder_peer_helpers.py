"""Tests for peer-artefact context helpers added in the zero-truncation refactor."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def _node(nid: str, ntype: str, **kw: Any) -> Any:
    return SimpleNamespace(
        node_id=nid,
        node_type=ntype,
        parent_id=kw.get("parent_id", ""),
        title=kw.get("title", ""),
        content=kw.get("content", ""),
        trace_to=kw.get("trace_to", []),
        properties=kw.get("properties", {}),
    )


class _Graph:
    """Tiny in-memory stand-in for ProjectGraph used by task_builder helpers."""

    def __init__(self, nodes: list[Any]) -> None:
        self._nodes = {n.node_id: n for n in nodes}

    def all_nodes(self) -> list[Any]:
        return list(self._nodes.values())

    def node_sync(self, nid: str) -> Any:
        return self._nodes.get(nid)

    def children_sync(self, parent_id: str) -> list[Any]:
        return [n for n in self._nodes.values() if n.parent_id == parent_id]

    def nodes_tracing_to(self, target_id: str, source_type: str = "") -> list[str]:
        result = []
        for n in self._nodes.values():
            if source_type and n.node_type != source_type:
                continue
            if target_id in (n.trace_to or []):
                result.append(n.node_id)
        return result


def test_build_peer_contracts_context_full_content() -> None:
    from backend.crew.task_builder import build_peer_contracts_context

    ctr_a = _node(
        "CONTRACT-0001",
        "CONTRACT",
        parent_id="MODULE-0001",
        content="def fetch(x: int) -> User",
    )
    ctr_b = _node(
        "CONTRACT-0002",
        "CONTRACT",
        parent_id="MODULE-0002",
        content="def push(y: str) -> None",
    )
    graph = _Graph([ctr_a, ctr_b])
    out = build_peer_contracts_context(graph)
    assert "def fetch(x: int) -> User" in out
    assert "def push(y: str) -> None" in out


def test_build_peer_contracts_excludes_given_module() -> None:
    from backend.crew.task_builder import build_peer_contracts_context

    own = _node("CONTRACT-0001", "CONTRACT", parent_id="MODULE-me", content="mine")
    peer = _node("CONTRACT-0002", "CONTRACT", parent_id="MODULE-other", content="theirs")
    graph = _Graph([own, peer])
    out = build_peer_contracts_context(graph, exclude_module_id="MODULE-me")
    assert "mine" not in out
    assert "theirs" in out


def test_build_design_for_llr_reverse_lookup() -> None:
    from backend.crew.task_builder import build_design_for_llr

    hlr = _node("HLR-0001", "HLR", content="req")
    llr = _node("LLR-0001", "LLR", parent_id="HLR-0001", content="atomic req")
    module = _node(
        "MODULE-0001", "MODULE", trace_to=["HLR-0001"], content="class plan"
    )
    design = _node(
        "DESIGN-0001",
        "DESIGN",
        parent_id="MODULE-0001",
        trace_to=["LLR-0001"],
        content="def solve(x): ...",
    )
    graph = _Graph([hlr, llr, module, design])
    out = build_design_for_llr(graph, "LLR-0001")
    assert "def solve(x): ..." in out
    assert "DESIGN-0001" in out


def test_build_cases_for_requirement_full_content() -> None:
    from backend.crew.task_builder import build_cases_for_requirement

    c1 = _node("CASE-1", "CASE_HLR", trace_to=["HLR-0001"], content="Step 1: call foo")
    c2 = _node("CASE-2", "CASE_HLR", trace_to=["HLR-0002"], content="Step 2: unrelated")
    graph = _Graph([c1, c2])
    out = build_cases_for_requirement(graph, "HLR-0001")
    assert "Step 1: call foo" in out
    assert "Step 2" not in out


def test_build_document_digest_filters_by_para_type() -> None:
    from backend.crew.task_builder import build_document_digest

    rationale = _node(
        "PARA-1", "PARA",
        content="Why we chose async.",
        properties={"para_type": "rationale"},
    )
    functional = _node(
        "PARA-2", "PARA",
        content="Boring behaviour.",
        properties={"para_type": "functional"},
    )
    constraint = _node(
        "PARA-3", "PARA",
        content="Must run on 3.12.",
        properties={"para_type": "constraint"},
    )
    graph = _Graph([rationale, functional, constraint])
    out = build_document_digest(graph)
    assert "Why we chose async." in out
    assert "Must run on 3.12." in out
    assert "Boring behaviour." not in out  # functional excluded


def test_build_all_llrs_context_full_content() -> None:
    from backend.crew.task_builder import build_all_llrs_context

    l1 = _node("LLR-0001", "LLR", parent_id="HLR-0001", content="The system shall X.")
    l2 = _node("LLR-0002", "LLR", parent_id="HLR-0001", content="The system shall Y.")
    graph = _Graph([l1, l2])
    out = build_all_llrs_context(graph)
    assert "The system shall X." in out
    assert "The system shall Y." in out


def test_build_sibling_paras_context_excludes_self() -> None:
    from backend.crew.task_builder import build_sibling_paras_context

    target = _node(
        "PARA-1", "PARA",
        parent_id="DOC-1",
        content="target paragraph",
        properties={"para_type": "functional"},
    )
    sibling = _node(
        "PARA-2", "PARA",
        parent_id="DOC-1",
        content="sibling rationale",
        properties={"para_type": "rationale"},
    )
    graph = _Graph([target, sibling])
    out = build_sibling_paras_context(graph, "PARA-1")
    assert "sibling rationale" in out
    assert "target paragraph" not in out
