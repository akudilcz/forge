"""Tests for the MODULE class-plan validator."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from backend.quality.module_validators import (
    check_design_count_allowed,
    count_planned_classes,
)


def _mod(nid: str, content: str) -> Any:
    return SimpleNamespace(
        node_id=nid, node_type="MODULE", content=content, parent_id="",
        title="", trace_to=[], properties={},
    )


def _design(nid: str, parent: str) -> Any:
    return SimpleNamespace(
        node_id=nid, node_type="DESIGN", parent_id=parent, content="",
        title="", trace_to=[], properties={},
    )


class _Graph:
    def __init__(self, nodes: list[Any]) -> None:
        self._by_id = {n.node_id: n for n in nodes}

    def node_sync(self, nid: str) -> Any:
        return self._by_id.get(nid)

    def children_sync(self, pid: str) -> list[Any]:
        return [n for n in self._by_id.values() if n.parent_id == pid]


def test_count_planned_classes_from_markdown_plan() -> None:
    content = (
        "## Responsibilities\nDo stuff.\n\n"
        "## Class Plan\n"
        "- Parser: chunks markdown\n"
        "- Linker: wires references\n"
        "- Auditor: verifies coverage\n"
    )
    assert count_planned_classes(content) == 3


def test_count_planned_classes_ignores_stopwords() -> None:
    content = "## Class Plan\nThe Parser is the main class.\n"
    assert count_planned_classes(content) == 1


def test_count_planned_classes_no_plan_section_returns_pattern_count() -> None:
    content = "A single Parser class handles everything."
    assert count_planned_classes(content) == 1


def test_count_planned_classes_empty_content() -> None:
    assert count_planned_classes("") == 0


def test_check_design_count_allowed_under_limit() -> None:
    mod = _mod("M1", "## Class Plan\n- Alpha\n- Beta\n")
    d1 = _design("D1", "M1")
    graph = _Graph([mod, d1])
    assert check_design_count_allowed(graph, "M1") is None


def test_check_design_count_allowed_at_limit_rejects() -> None:
    mod = _mod("M1", "## Class Plan\n- Alpha\n")
    d1 = _design("D1", "M1")
    graph = _Graph([mod, d1])
    err = check_design_count_allowed(graph, "M1")
    assert err is not None
    assert "1 DESIGN" in err
    assert "1 class" in err


def test_check_design_count_allowed_multiple_classes() -> None:
    mod = _mod("M1", "## Class Plan\n- Alpha\n- Beta\n- Gamma\n")
    graph = _Graph([mod, _design("D1", "M1"), _design("D2", "M1")])
    # 2 existing, 3 planned → still allowed
    assert check_design_count_allowed(graph, "M1") is None
    # Add a third → now at limit
    graph = _Graph(
        [mod, _design("D1", "M1"), _design("D2", "M1"), _design("D3", "M1")]
    )
    assert check_design_count_allowed(graph, "M1") is not None


def test_check_design_count_allowed_non_module_ignored() -> None:
    """Unknown or non-MODULE parent: not our concern."""
    not_a_module = SimpleNamespace(
        node_id="X", node_type="HLR", content="", parent_id="",
        title="", trace_to=[], properties={},
    )
    graph = _Graph([not_a_module])
    assert check_design_count_allowed(graph, "X") is None
